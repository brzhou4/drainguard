"""SF elevation grid from AWS Terrain Tiles (Mapzen terrarium, USGS 3DEP/NED in the US).

load_dem() mosaics zoom-14 terrarium tiles (~7.5 m px at SF) over SF_BBOX, bilinear-resamples
them onto the regular lat/lon working grid, rasterises the SF city boundary (union of DataSF
Analysis Neighborhoods) into a land mask, and caches the result to data/processed/.
"""
import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy.ndimage import map_coordinates

from . import DEFAULT_CELL_M, M_PER_DEG_LAT, M_PER_DEG_LON, SF_BBOX, Grid

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
NHOOD_URL = "https://data.sf.gov/resource/j2bu-swwd.geojson"
ZOOM = 14
TILE_PX = 256
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"


# ---------- terrarium tiles ----------

def decode_terrarium(rgb):
    """(H, W, 3) uint8 terrarium RGB -> elevation (m) float32: R*256 + G + B/256 - 32768."""
    rgb = np.asarray(rgb, dtype=np.float64)
    return (rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0).astype(np.float32)


def encode_terrarium(z):
    """Elevation (m) -> (H, W, 3) uint8 terrarium RGB (inverse of decode_terrarium; for tests)."""
    v = np.asarray(z, dtype=np.float64) + 32768.0
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.round((v - r * 256.0 - g) * 256.0)
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


def lonlat_to_tile(lon, lat, zoom=ZOOM):
    """Lon/lat -> fractional web-mercator tile coordinates (x, y) at a zoom."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lr = np.radians(lat)
    y = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / np.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x, y, zoom=ZOOM):
    """Tile coordinates (top-left corner for integers) -> lon/lat."""
    n = 2.0 ** zoom
    lon = np.asarray(x, float) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * np.asarray(y, float) / n))))
    return lon, lat


def tile_range(bbox=SF_BBOX, zoom=ZOOM):
    """Integer tile index ranges covering bbox: (x0, x1, y0, y1), inclusive."""
    lat_min, lat_max, lon_min, lon_max = bbox
    x0, y0 = lonlat_to_tile(lon_min, lat_max, zoom)
    x1, y1 = lonlat_to_tile(lon_max, lat_min, zoom)
    return int(np.floor(x0)), int(np.floor(x1)), int(np.floor(y0)), int(np.floor(y1))


def fetch_tile(x, y, zoom=ZOOM, retries=4, timeout=30):
    """Download and decode one terrarium tile -> (256, 256) float32 elevation."""
    url = TILE_URL.format(z=zoom, x=x, y=y)
    err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return decode_terrarium(np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")))
        except Exception as e:  # noqa: BLE001 - retry any network/decode error
            err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"terrain tile {zoom}/{x}/{y} failed after {retries} tries: {err}")


def fetch_mosaic(bbox=SF_BBOX, zoom=ZOOM, workers=8, fetch=fetch_tile):
    """Mosaic all tiles covering bbox -> (elevation array, x0, y0) where x0/y0 are the top-left tile."""
    x0, x1, y0, y1 = tile_range(bbox, zoom)
    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    mosaic = np.zeros(((y1 - y0 + 1) * TILE_PX, (x1 - x0 + 1) * TILE_PX), np.float32)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for (x, y), tile in zip(jobs, ex.map(lambda xy: fetch(xy[0], xy[1], zoom), jobs)):
            r, c = (y - y0) * TILE_PX, (x - x0) * TILE_PX
            mosaic[r:r + TILE_PX, c:c + TILE_PX] = tile
    return mosaic, x0, y0


# ---------- grid ----------

def grid_axes(cell_m=DEFAULT_CELL_M, bbox=SF_BBOX):
    """Regular lat/lon grid over bbox -> (lat_top, lon_left, dlat, dlon, rows, cols)."""
    lat_min, lat_max, lon_min, lon_max = bbox
    dlat, dlon = cell_m / M_PER_DEG_LAT, cell_m / M_PER_DEG_LON
    rows = int(np.ceil((lat_max - lat_min) / dlat))
    cols = int(np.ceil((lon_max - lon_min) / dlon))
    return lat_max, lon_min, dlat, dlon, rows, cols


def resample_mosaic(mosaic, x0, y0, lat, lon, zoom=ZOOM):
    """Bilinear-sample a tile mosaic at lat/lon arrays (pixel centres at +0.5)."""
    tx, ty = lonlat_to_tile(lon, lat, zoom)
    px = (tx - x0) * TILE_PX - 0.5
    py = (ty - y0) * TILE_PX - 0.5
    return map_coordinates(mosaic, [py.ravel(), px.ravel()], order=1, mode="nearest").reshape(np.shape(lat))


def _cell_centres(lat_top, lon_left, dlat, dlon, rows, cols):
    lat = lat_top - (np.arange(rows) + 0.5) * dlat
    lon = lon_left + (np.arange(cols) + 0.5) * dlon
    return np.meshgrid(lat, lon, indexing="ij")


def fetch_sf_polygons(timeout=60):
    """SF city boundary as a list of outer/inner rings [(lon, lat), ...] from DataSF neighborhoods."""
    r = requests.get(NHOOD_URL, params={"$limit": 100}, timeout=timeout)
    r.raise_for_status()
    feats = r.json()["features"]
    if len(feats) < 30:
        raise RuntimeError(f"SF neighborhood boundary: only {len(feats)} polygons returned")
    polys = []
    for f in feats:
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        polys += [[np.asarray(ring, float)[:, :2] for ring in part] for part in parts]
    return polys


def rasterize_polygons(polys, lat_top, lon_left, dlat, dlon, rows, cols):
    """Polygons (each a list of rings: outer first, then holes) -> bool mask of cells whose CENTRE is inside.

    Exact even-odd scanline fill in numpy; polygons are OR-ed so one polygon's hole never erases another.
    """
    mask = np.zeros((rows, cols), bool)
    yc = np.arange(rows) + 0.5                       # cell-centre row coordinates
    for rings in polys:
        # all edges of this polygon (outer + holes) in fractional (col, row) coordinates
        x0, y0, x1, y1 = [], [], [], []
        for ring in rings:
            px = (ring[:, 0] - lon_left) / dlon
            py = (lat_top - ring[:, 1]) / dlat
            x0.append(px), y0.append(py), x1.append(np.roll(px, -1)), y1.append(np.roll(py, -1))
        x0, y0, x1, y1 = (np.concatenate(a) for a in (x0, y0, x1, y1))
        r_lo = max(int(np.floor(min(y0.min(), y1.min()))), 0)
        r_hi = min(int(np.ceil(max(y0.max(), y1.max()))), rows)
        poly = np.zeros((rows, cols), bool)
        for r in range(r_lo, r_hi):
            y = yc[r]
            hit = (y0 <= y) != (y1 <= y)             # half-open rule: no double counting at vertices
            if not hit.any():
                continue
            xs = np.sort(x0[hit] + (y - y0[hit]) * (x1[hit] - x0[hit]) / (y1[hit] - y0[hit]))
            for a, b in zip(xs[0::2], xs[1::2]):     # centres c+0.5 in [a, b)
                c0, c1 = max(int(np.ceil(a - 0.5)), 0), min(int(np.ceil(b - 0.5)), cols)
                if c1 > c0:
                    poly[r, c0:c1] = True
        mask |= poly
    return mask


def build_dem(cell_m=DEFAULT_CELL_M, bbox=SF_BBOX, workers=8):
    """Download tiles + boundary and build the Grid (no caching)."""
    lat_top, lon_left, dlat, dlon, rows, cols = grid_axes(cell_m, bbox)
    mosaic, x0, y0 = fetch_mosaic(bbox, ZOOM, workers)
    lat, lon = _cell_centres(lat_top, lon_left, dlat, dlon, rows, cols)
    z = resample_mosaic(mosaic, x0, y0, lat, lon).astype(np.float32)
    z[~np.isfinite(z)] = 0.0
    inside = rasterize_polygons(fetch_sf_polygons(), lat_top, lon_left, dlat, dlon, rows, cols)
    return Grid(z=z, lat_top=lat_top, lon_left=lon_left, dlat=dlat, dlon=dlon, land=inside & (z > 0))


def cache_path(cell_m=DEFAULT_CELL_M):
    return CACHE_DIR / f"sf_dem_{int(cell_m)}m.npz"


def save_grid(grid, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, z=grid.z.astype(np.float32), land=np.packbits(grid.land),
                        shape=np.array(grid.shape), meta=np.array([grid.lat_top, grid.lon_left, grid.dlat, grid.dlon]))


def load_grid(path):
    with np.load(path) as d:
        shape = tuple(int(v) for v in d["shape"])
        land = np.unpackbits(d["land"])[: shape[0] * shape[1]].reshape(shape).astype(bool)
        lat_top, lon_left, dlat, dlon = (float(v) for v in d["meta"])
        return Grid(z=d["z"].astype(np.float32), lat_top=lat_top, lon_left=lon_left,
                    dlat=dlat, dlon=dlon, land=land)


def load_dem(cell_m=DEFAULT_CELL_M, bbox=SF_BBOX, cache=True):
    """SF elevation Grid (z in m, land mask). Uses data/processed/sf_dem_{cell}m.npz when present.

    The cache is only used for the default bbox; a custom bbox is always built fresh.
    """
    path = cache_path(cell_m)
    use_cache = cache and tuple(bbox) == tuple(SF_BBOX)
    if use_cache and path.exists():
        return load_grid(path)
    grid = build_dem(cell_m, bbox)
    if use_cache:
        save_grid(grid, path)
    return grid


# ---------- derived surfaces ----------

def _gradients(grid):
    z = np.asarray(grid.z, np.float64)
    z = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0)
    dz_dy, dz_dx = np.gradient(z, grid.cell_dy_m, grid.cell_dx_m)
    return dz_dx, -dz_dy        # rows increase southward, so flip to get d/d(north)


def slope(grid):
    """Slope magnitude (m/m) per cell (central differences)."""
    dzdx, dzdn = _gradients(grid)
    return np.hypot(dzdx, dzdn)


def hillshade(grid, azimuth=315.0, altitude=45.0):
    """Lambertian hillshade 0..1 (azimuth clockwise from north, degrees; altitude above horizon)."""
    dzdx, dzdn = _gradients(grid)
    az, alt = np.radians(azimuth), np.radians(altitude)
    lx, ly, lz = np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt)  # east, north, up
    n = np.sqrt(dzdx ** 2 + dzdn ** 2 + 1.0)
    shade = (-dzdx * lx - dzdn * ly + lz) / n
    return np.clip(shade, 0.0, 1.0)


def elevation_at(grid, lat, lon):
    """Bilinear elevation (m) at lat/lon (scalars or arrays); NaN outside the grid."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    rows, cols = grid.shape
    fr = (grid.lat_top - lat) / grid.dlat - 0.5
    fc = (lon - grid.lon_left) / grid.dlon - 0.5
    out = map_coordinates(np.asarray(grid.z, np.float64), [np.atleast_1d(fr).ravel(), np.atleast_1d(fc).ravel()],
                          order=1, mode="nearest").reshape(np.shape(lat))
    inside = (fr >= -0.5) & (fr <= rows - 0.5) & (fc >= -0.5) & (fc <= cols - 0.5)
    out = np.where(inside, out, np.nan)
    return float(out) if out.ndim == 0 else out
