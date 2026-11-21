"""Tests for src.flood.terrain (no network)."""
import numpy as np
import pytest

from src.flood import SF_BBOX, M_PER_DEG_LAT, M_PER_DEG_LON, Grid
from src.flood import terrain as t


def test_terrarium_round_trip():
    z = np.array([[-32768.0, -10.5, 0.0], [12.25, 283.0, 4000.125]])
    back = t.decode_terrarium(t.encode_terrarium(z))
    assert np.allclose(back, z, atol=1 / 256)


def test_decode_known_pixel():
    # R=128, G=10, B=128 -> 128*256 + 10 + 0.5 - 32768 = 10.5 m
    assert t.decode_terrarium(np.array([[[128, 10, 128]]], np.uint8))[0, 0] == pytest.approx(10.5)


def test_tile_math():
    # Equator / prime meridian is the centre of the world at every zoom.
    x, y = t.lonlat_to_tile(0.0, 0.0, 14)
    assert x == pytest.approx(8192) and y == pytest.approx(8192)
    lon, lat = t.tile_to_lonlat(2620, 6332, 14)
    x2, y2 = t.lonlat_to_tile(lon, lat, 14)
    assert x2 == pytest.approx(2620) and y2 == pytest.approx(6332)
    x0, x1, y0, y1 = t.tile_range(SF_BBOX, 14)
    assert (x0, x1, y0, y1) == (2615, 2623, 6329, 6336)
    # covered extent really contains the bbox
    lon_w, lat_n = t.tile_to_lonlat(x0, y0, 14)
    lon_e, lat_s = t.tile_to_lonlat(x1 + 1, y1 + 1, 14)
    assert lon_w <= SF_BBOX[2] and lon_e >= SF_BBOX[3] and lat_s <= SF_BBOX[0] and lat_n >= SF_BBOX[1]


def test_grid_axes_bounds():
    lat_top, lon_left, dlat, dlon, rows, cols = t.grid_axes(30.0, SF_BBOX)
    assert dlat == pytest.approx(30 / M_PER_DEG_LAT) and dlon == pytest.approx(30 / M_PER_DEG_LON)
    g = Grid(z=np.zeros((rows, cols), np.float32), lat_top=lat_top, lon_left=lon_left, dlat=dlat, dlon=dlon)
    (lat_min, lon_min), (lat_max, lon_max) = g.bounds()
    assert lat_max == SF_BBOX[1] and lon_min == SF_BBOX[2]
    assert SF_BBOX[0] - dlat < lat_min <= SF_BBOX[0] and SF_BBOX[3] <= lon_max < SF_BBOX[3] + dlon


def test_mosaic_resample_with_fake_tiles():
    bbox = (37.75, 37.77, -122.45, -122.43)
    # fake tiles: elevation = global pixel row index (so it varies smoothly north->south)
    def fake(x, y, zoom):
        rows = (y * 256 + np.arange(256))[:, None] * np.ones((1, 256))
        return rows.astype(np.float32)
    mosaic, x0, y0 = t.fetch_mosaic(bbox, 14, workers=2, fetch=fake)
    lat = np.array([37.76, 37.755])
    lon = np.array([-122.44, -122.44])
    z = t.resample_mosaic(mosaic, x0, y0, lat, lon)
    _, ty = t.lonlat_to_tile(lon, lat, 14)
    assert np.allclose(z, ty * 256 - 0.5, atol=1e-3)


def _plane_grid(sx=0.1, sn=0.05, n=40, cell=30.0):
    dlat, dlon = cell / M_PER_DEG_LAT, cell / M_PER_DEG_LON
    r, c = np.mgrid[0:n, 0:n]
    z = 100 + sx * c * cell - sn * r * cell      # rises to east (sx) and to north (sn)
    return Grid(z=z.astype(np.float32), lat_top=37.8, lon_left=-122.5, dlat=dlat, dlon=dlon,
                land=np.ones((n, n), bool))


def test_slope_plane():
    g = _plane_grid()
    s = t.slope(g)
    assert np.allclose(s, np.hypot(0.1, 0.05), rtol=1e-3)


def test_hillshade_plane():
    flat = _plane_grid(0, 0)
    assert np.allclose(t.hillshade(flat, altitude=45), np.sin(np.radians(45)))
    # slope facing NW (falls to the NW = rises to SE) is lit brighter by a NW sun than one facing SE
    rises_se = _plane_grid(sx=0.3, sn=-0.3)
    rises_nw = _plane_grid(sx=-0.3, sn=0.3)
    a, b = t.hillshade(rises_se), t.hillshade(rises_nw)
    assert a.min() >= 0 and b.max() <= 1
    assert a.mean() > b.mean()


def test_elevation_at():
    g = _plane_grid()
    lat, lon = g.latlon(10, 20)
    assert t.elevation_at(g, lat, lon) == pytest.approx(g.z[10, 20], abs=1e-3)
    out = t.elevation_at(g, np.array([lat, 10.0]), np.array([lon, 10.0]))
    assert out[0] == pytest.approx(g.z[10, 20], abs=1e-3) and np.isnan(out[1])


def test_rasterize_polygon():
    lat_top, lon_left, dlat, dlon = 1.0, 0.0, 0.1, 0.1
    outer = np.array([[0.2, 0.8], [0.8, 0.8], [0.8, 0.2], [0.2, 0.2], [0.2, 0.8]])  # (lon, lat)
    hole = np.array([[0.4, 0.6], [0.6, 0.6], [0.6, 0.4], [0.4, 0.4], [0.4, 0.6]])
    m = t.rasterize_polygons([[outer, hole]], lat_top, lon_left, dlat, dlon, 10, 10)
    assert m[3, 3] and not m[0, 0] and not m[5, 5] and not m[9, 9]


def test_cache_round_trip(tmp_path, monkeypatch):
    g = _plane_grid()
    g.land[:5] = False
    p = tmp_path / "dem.npz"
    t.save_grid(g, p)
    h = t.load_grid(p)
    assert np.array_equal(h.z, g.z) and np.array_equal(h.land, g.land)
    assert (h.lat_top, h.lon_left, h.dlat, h.dlon) == (g.lat_top, g.lon_left, g.dlat, g.dlon)
    # load_dem uses the cache without touching the network
    monkeypatch.setattr(t, "cache_path", lambda cell_m=30.0: p)
    monkeypatch.setattr(t, "build_dem", lambda *a, **k: pytest.fail("network build called"))
    assert t.load_dem().shape == g.shape


def test_committed_cache_sane():
    p = t.cache_path(30.0)
    if not p.exists():
        pytest.skip("no cached DEM")
    g = t.load_grid(p)
    assert g.z.dtype == np.float32 and np.isfinite(g.z).all()
    assert 120_000 < g.land.sum() < 150_000            # ~121 km2 of land at 900 m2/cell
    assert 270 < g.z[g.land].max() < 300                # Mount Davidson ~283 m
    assert 240 < t.elevation_at(g, 37.7515, -122.4479) < 290  # Twin Peaks
