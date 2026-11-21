"""Offline builder for the 24-hour modeled ponding scenario and the static terrain screening layer.

Run from the repo root (needs requirements-analysis.txt, mainly numpy, pillow, pyproj):
    python scripts/build_ponding.py

Steps
1. Mosaic Terrarium elevation tiles, zoom 13, x=1307..1311, y=3164..3168 (1280 x 1280 px).
   meters = R*256 + G + B/256 - 32768. Pixel area = (Web Mercator pixel side)^2 * cos(lat)^2.
2. 8-neighbor priority flood from the raster boundary. Keeps the original elevation, the spill
   elevation and a parent pointer toward the boundary. Depression pixels are > 1 cm below spill.
   Adjacent depression pixels with the same spill elevation form one depression. Each pixel's runoff
   goes to the first depression on its parent path (or out of the boundary if there is none).
3. Rain: for Nov 19 UTC, the 24 archived forecast hours ending 01:00 UTC Nov 19 through 00:00 UTC Nov 20
   at each of the five forecast points (data/fixtures replay file). Each pixel gets its nearest point's rain.
   Runoff = 50% of rain (uncalibrated). Each depression fills to the water surface that stores its inflow,
   up to spill capacity; extra inflow is counted as spill (NOT routed downstream).
   Asserts runoff = storage + spill + boundary outflow.
4. 100 m EPSG:26910 cells that intersect active DataSF street centerlines (3psu-pn9h). Each cell is
   sampled on a 5 x 5 grid at 20 m spacing. Exports cell centers where the max sampled modeled depth
   is >= 0.10 m (ponding scenario) and, separately, where the max sampled depth-to-spill of the terrain
   is >= 0.25 m (static terrain screening).

This does not model inlet capture, pipe capacity, spill routing or confirmed water depth.
"""
import heapq
import io
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.forecast import PONDING_GEOJSON, PONDING_SUMMARY, POINTS, TERRAIN_GEOJSON, load_replay  # noqa: E402

Z = 13
XS = range(1307, 1312)
YS = range(3164, 3169)
TILE = 256
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_CACHE = ROOT / "data" / "raw" / "terrarium"
STREETS_URL = "https://data.sf.gov/resource/3psu-pn9h.geojson"
STREETS_CACHE = ROOT / "data" / "raw" / "street_centerlines_active.geojson"
DEPRESSION_MIN_M = 0.01
RUNOFF_FRACTION = 0.5
CELL_M = 100
SAMPLE_OFFSETS = np.array([-40.0, -20.0, 0.0, 20.0, 40.0])
PONDING_MIN_M = 0.10
TERRAIN_MIN_M = 0.25
RAIN_FIRST_END = datetime(2026, 11, 19, 1, tzinfo=timezone.utc)
RAIN_HOURS = 24
EARTH_R = 6378137.0


# ---------- 1. Elevation mosaic ----------
def get_tile(x, y):
    TILE_CACHE.mkdir(parents=True, exist_ok=True)
    path = TILE_CACHE / f"{Z}_{x}_{y}.png"
    if not path.exists():
        r = requests.get(TILE_URL.format(z=Z, x=x, y=y), timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    rgb = np.asarray(Image.open(io.BytesIO(path.read_bytes())).convert("RGB"), dtype=np.float64)
    return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768


def mosaic():
    rows = [np.hstack([get_tile(x, y) for x in XS]) for y in YS]
    return np.vstack(rows)


def pixel_lonlat(col, row):
    """Pixel centers (mosaic col/row, may be arrays) -> lon, lat."""
    n = TILE * 2 ** Z
    gx = XS[0] * TILE + np.asarray(col, float) + 0.5
    gy = YS[0] * TILE + np.asarray(row, float) + 0.5
    lon = gx / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * gy / n))))
    return lon, lat


def lonlat_to_pixel(lon, lat):
    n = TILE * 2 ** Z
    lat_r = np.radians(lat)
    gx = (np.asarray(lon) + 180.0) / 360.0 * n
    gy = (1 - np.log(np.tan(lat_r) + 1 / np.cos(lat_r)) / np.pi) / 2 * n
    return gx - XS[0] * TILE - 0.5, gy - YS[0] * TILE - 0.5


# ---------- 2. Priority flood ----------
def priority_flood(elev):
    """Returns (spill, parent, order). parent = -1 at the boundary; order = pop order (parents first)."""
    H, W = elev.shape
    N = H * W
    e = elev.ravel().tolist()
    spill = list(e)
    parent = [-1] * N
    closed = bytearray(N)
    heap = []
    for r in range(H):
        for c in range(W):
            if r in (0, H - 1) or c in (0, W - 1):
                i = r * W + c
                closed[i] = 1
                heap.append((e[i], i))
    heapq.heapify(heap)
    order = []
    push, pop = heapq.heappush, heapq.heappop
    nbrs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    while heap:
        s, i = pop(heap)
        order.append(i)
        r, c = divmod(i, W)
        for dr, dc in nbrs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < H and 0 <= cc < W:
                j = rr * W + cc
                if not closed[j]:
                    closed[j] = 1
                    sj = e[j] if e[j] > s else s
                    spill[j] = sj
                    parent[j] = i
                    push(heap, (sj, j))
    return (np.array(spill).reshape(H, W), np.array(parent, dtype=np.int64), np.array(order, dtype=np.int64))


def label_depressions(elev, spill):
    """Group adjacent depression pixels sharing a spill elevation. Returns int array, -1 = not a depression."""
    H, W = elev.shape
    dep = (spill - elev) > DEPRESSION_MIN_M
    lab = np.full(H * W, -1, dtype=np.int64)
    depf, spf = dep.ravel(), spill.ravel()
    n = 0
    for start in np.flatnonzero(depf):
        if lab[start] >= 0:
            continue
        lab[start] = n
        stack = [start]
        s0 = spf[start]
        while stack:
            i = stack.pop()
            r, c = divmod(int(i), W)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if (dr or dc) and 0 <= rr < H and 0 <= cc < W:
                        j = rr * W + cc
                        if depf[j] and lab[j] < 0 and spf[j] == s0:
                            lab[j] = n
                            stack.append(j)
        n += 1
    return lab, n


def first_depression(lab, parent, order):
    """Depression receiving each pixel's runoff (-1 = leaves through the boundary)."""
    dest = np.full(lab.shape, -1, dtype=np.int64)
    lab_l, par_l, dest_l = lab.tolist(), parent.tolist(), dest.tolist()
    for i in order.tolist():
        if lab_l[i] >= 0:
            dest_l[i] = lab_l[i]
        elif par_l[i] >= 0:
            dest_l[i] = dest_l[par_l[i]]
    return np.array(dest_l, dtype=np.int64)


# ---------- 3. Rain, runoff and depression filling ----------
def nov19_rain_by_point():
    rp = load_replay()
    if rp is None:
        sys.exit("ERROR: replay fixture missing; run scripts/fetch_replay_fixture.py first")
    ends = [RAIN_FIRST_END + timedelta(hours=k) for k in range(RAIN_HOURS)]
    totals = []
    for ser in rp["series"]:
        vals = [ser.get(t) for t in ends]
        if any(v is None for v in vals):
            sys.exit("ERROR: a Nov 19 rain hour is missing in the fixture")
        totals.append(sum(vals))
    return totals


def fill_depressions(elev_f, spill_f, area_f, lab, n_dep, inflow):
    """Solve each depression's water surface. Returns (depth per pixel, stored per dep, spill per dep)."""
    depth = np.zeros_like(elev_f)
    stored = np.zeros(n_dep)
    spilled = np.zeros(n_dep)
    idx = np.flatnonzero(lab >= 0)
    idx = idx[np.lexsort((elev_f[idx], lab[idx]))]          # by depression, then elevation
    groups = np.split(idx, np.flatnonzero(np.diff(lab[idx])) + 1)
    for g in groups:
        d = lab[g[0]]
        e, a = elev_f[g], area_f[g]
        S = spill_f[g[0]]
        cap = float(np.sum((S - e) * a))
        V = float(inflow[d])
        if V >= cap:
            level, stored[d], spilled[d] = S, cap, V - cap
        else:
            cumA, cumEA = np.cumsum(a), np.cumsum(e * a)
            vol_at = cumA * e - cumEA                      # volume when the surface is at e[k]
            k = int(np.searchsorted(vol_at, V, side="right")) - 1
            k = max(k, 0)
            level = (V + cumEA[k]) / cumA[k]
            level = min(level, S)
            stored[d] = V
        depth[g] = np.maximum(0.0, level - e)
    return depth, stored, spilled


# ---------- 4. Street cells ----------
def street_lines():
    if STREETS_CACHE.exists():
        return json.loads(STREETS_CACHE.read_text())
    feats, offset, page = [], 0, 5000
    while True:
        r = requests.get(STREETS_URL, params={"$where": "active=true", "$limit": page, "$offset": offset,
                                              "$order": "cnn"}, timeout=120)
        r.raise_for_status()
        batch = r.json()["features"]
        feats += batch
        if len(batch) < page:
            break
        offset += page
    gj = {"type": "FeatureCollection", "features": feats}
    STREETS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STREETS_CACHE.write_text(json.dumps(gj))
    return gj


def cells_on_segment(x0, y0, x1, y1, size=CELL_M):
    """Grid cells a straight segment passes through (Amanatides-Woo traversal)."""
    cx, cy = math.floor(x0 / size), math.floor(y0 / size)
    ex, ey = math.floor(x1 / size), math.floor(y1 / size)
    out = {(cx, cy)}
    dx, dy = x1 - x0, y1 - y0
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    tmx = ((cx + (sx > 0)) * size - x0) / dx if dx else math.inf
    tmy = ((cy + (sy > 0)) * size - y0) / dy if dy else math.inf
    tdx = size / abs(dx) if dx else math.inf
    tdy = size / abs(dy) if dy else math.inf
    for _ in range(10000):
        if (cx, cy) == (ex, ey):
            break
        if tmx < tmy:
            cx, tmx = cx + sx, tmx + tdx
        elif tmy < tmx:
            cy, tmy = cy + sy, tmy + tdy
        else:  # exactly through a corner
            cx, cy, tmx, tmy = cx + sx, cy + sy, tmx + tdx, tmy + tdy
        out.add((cx, cy))
    return out


def street_cells(to_utm):
    cells = set()
    for f in street_lines()["features"]:
        g = f.get("geometry") or {}
        lines = g.get("coordinates") or []
        if g.get("type") == "LineString":
            lines = [lines]
        elif g.get("type") != "MultiLineString":
            continue
        for line in lines:
            if len(line) < 2:
                continue
            xs, ys = to_utm.transform([p[0] for p in line], [p[1] for p in line])
            for k in range(len(xs) - 1):
                cells |= cells_on_segment(xs[k], ys[k], xs[k + 1], ys[k + 1])
    return sorted(cells)


def sample_max(grid, cells, to_ll):
    """Max of `grid` over a 5 x 5 sample grid (20 m spacing) in each cell; NaN if outside the raster."""
    H, W = grid.shape
    ox, oy = np.meshgrid(SAMPLE_OFFSETS, SAMPLE_OFFSETS)
    ox, oy = ox.ravel(), oy.ravel()
    c = np.array(cells, dtype=float)
    cxm, cym = (c[:, 0] + 0.5) * CELL_M, (c[:, 1] + 0.5) * CELL_M
    sx = (cxm[:, None] + ox[None, :]).ravel()
    sy = (cym[:, None] + oy[None, :]).ravel()
    lon, lat = to_ll.transform(sx, sy)
    col, row = lonlat_to_pixel(np.asarray(lon), np.asarray(lat))
    col, row = np.rint(col).astype(int), np.rint(row).astype(int)
    inside = (col >= 0) & (col < W) & (row >= 0) & (row < H)
    vals = np.full(sx.shape, np.nan)
    vals[inside] = grid[row[inside], col[inside]]
    vals = vals.reshape(len(cells), -1)
    centers_lon, centers_lat = to_ll.transform(cxm, cym)
    with np.errstate(all="ignore"):
        return np.nanmax(np.where(np.isnan(vals), -np.inf, vals), axis=1), np.asarray(centers_lon), np.asarray(centers_lat)


def points_geojson(cells, lon, lat, values, key):
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [round(float(lo), 6), round(float(la), 6)]},
              "properties": {"cell_id": f"E{cx}_N{cy}", key: round(float(v), 3)}}
             for (cx, cy), lo, la, v in zip(cells, lon, lat, values)]
    return {"type": "FeatureCollection", "features": feats}


def main():
    t0 = time.time()
    elev = mosaic()
    H, W = elev.shape
    print(f"Mosaic {W}x{H} px, elevation {elev.min():.1f}..{elev.max():.1f} m ({time.time() - t0:.0f}s)")

    rows = np.arange(H)
    _, lat_rows = pixel_lonlat(np.zeros(H), rows)
    side = 2 * math.pi * EARTH_R / (TILE * 2 ** Z)                 # Web Mercator pixel side, meters
    area = np.repeat((side ** 2 * np.cos(np.radians(lat_rows)) ** 2)[:, None], W, axis=1)

    spill, parent, order = priority_flood(elev)
    print(f"Priority flood done ({time.time() - t0:.0f}s)")
    lab, n_dep = label_depressions(elev, spill)
    to_spill = spill - elev
    print(f"{n_dep:,} depressions, {(lab >= 0).sum():,} depression pixels ({time.time() - t0:.0f}s)")
    dest = first_depression(lab, parent, order)

    rain = nov19_rain_by_point()
    col, row = np.meshgrid(np.arange(W), np.arange(H))
    lon, lat = pixel_lonlat(col.ravel(), row.ravel())
    kx = math.cos(math.radians(37.76))
    d2 = [((lon - p[1]) * kx) ** 2 + (lat - p[0]) ** 2 for p in POINTS]
    nearest = np.argmin(np.vstack(d2), axis=0)
    rain_m = np.asarray(rain)[nearest] / 1000.0
    area_f, elev_f = area.ravel(), elev.ravel()
    runoff = RUNOFF_FRACTION * rain_m * area_f                    # m^3 per pixel

    inflow = np.bincount(dest[dest >= 0], weights=runoff[dest >= 0], minlength=n_dep)
    outflow = float(runoff[dest < 0].sum())
    depth, stored, spilled = fill_depressions(elev_f, spill.ravel(), area_f, lab, n_dep, inflow)
    total = float(runoff.sum())
    stor_from_depth = float((depth * area_f).sum())
    balance = stored.sum() + spilled.sum() + outflow
    assert math.isclose(total, balance, rel_tol=1e-9), (total, balance)
    assert math.isclose(stor_from_depth, stored.sum(), rel_tol=1e-6), (stor_from_depth, stored.sum())
    print(f"Runoff {total:,.0f} m3 = storage {stored.sum():,.0f} + spill {spilled.sum():,.0f} "
          f"+ boundary outflow {outflow:,.0f} (checked)")

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:26910", "EPSG:4326", always_xy=True)
    cells = street_cells(to_utm)
    print(f"{len(cells):,} street cells ({time.time() - t0:.0f}s)")

    depth2 = depth.reshape(H, W)
    dmax, clon, clat = sample_max(depth2, cells, to_ll)
    tmax, _, _ = sample_max(np.where(lab.reshape(H, W) >= 0, to_spill, 0.0), cells, to_ll)

    keep = dmax >= PONDING_MIN_M
    pond = points_geojson([c for c, k in zip(cells, keep) if k], clon[keep], clat[keep], dmax[keep], "scenario_depth_m")
    keep_t = tmax >= TERRAIN_MIN_M
    terr = points_geojson([c for c, k in zip(cells, keep_t) if k], clon[keep_t], clat[keep_t], tmax[keep_t],
                          "depression_to_spill_m")
    PONDING_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    PONDING_GEOJSON.write_text(json.dumps(pond, separators=(",", ":")))
    TERRAIN_GEOJSON.write_text(json.dumps(terr, separators=(",", ":")))
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tiles": {"zoom": Z, "x": [XS[0], XS[-1]], "y": [YS[0], YS[-1]], "url": TILE_URL},
        "raster_px": [W, H], "depressions": int(n_dep), "depression_px": int((lab >= 0).sum()),
        "rain_window_utc": [RAIN_FIRST_END.isoformat(), (RAIN_FIRST_END + timedelta(hours=RAIN_HOURS - 1)).isoformat()],
        "rain_mm_by_point": [round(r, 2) for r in rain],
        "runoff_fraction": RUNOFF_FRACTION,
        "runoff_m3": round(total, 1), "storage_m3": round(float(stored.sum()), 1),
        "spill_m3": round(float(spilled.sum()), 1), "boundary_outflow_m3": round(outflow, 1),
        "street_cells": len(cells), "cell_m": CELL_M, "crs": "EPSG:26910",
        "ponding_cells": int(keep.sum()), "ponding_min_m": PONDING_MIN_M,
        "terrain_cells": int(keep_t.sum()), "terrain_min_m": TERRAIN_MIN_M,
        "limits": "Terrain-only. No spill routing, inlet capture, pipe capacity, infiltration model, "
                  "or confirmed water depth.",
    }
    PONDING_SUMMARY.write_text(json.dumps(summary, indent=1) + "\n")
    print(f"Wrote {keep.sum()} ponding cells and {keep_t.sum()} terrain cells ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
