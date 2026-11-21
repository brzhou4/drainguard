"""Flood calculator: wire rain -> terrain -> drains -> hydro -> frequency into one Scenario for the app and CLI."""
import functools
import importlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import ndimage

from src.flood import MM_PER_IN, Commonality, FloodResult

FLOOD_M = 0.15            # "flooded" = 6 in of standing water or more
DEEP_M = 0.5              # "deep" = about knee-high or more
M2_PER_ACRE = 4046.856
NHOOD_URL = "https://data.sf.gov/resource/j2bu-swwd.geojson"
# Export depth classes (m): [lo, hi) and a label.
DEPTH_CLASSES = [(0.05, 0.15, "0.05-0.15 m (2-6 in)"), (0.15, 0.3, "0.15-0.3 m (6-12 in)"),
                 (0.3, 0.6, "0.3-0.6 m (1-2 ft)"), (0.6, np.inf, ">0.6 m (over 2 ft)")]


def _processed():
    from src.data import PROCESSED
    return PROCESSED


def _mod(name):
    """Pipeline module by short name (looked up at call time so tests can swap in fakes)."""
    return importlib.import_module(f"src.flood.{name}")


@dataclass
class Scenario:
    result: FloodResult
    commonality: Commonality
    hotspots: pd.DataFrame
    inlets: pd.DataFrame          # inlets + bool column 'overwhelmed'
    summary: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------- caches

@functools.lru_cache(maxsize=4)
def cached_grid(cell_m=30.0):
    """Elevation grid at cell_m resolution, built once per process."""
    return _mod("terrain").load_dem(cell_m=cell_m)


@functools.lru_cache(maxsize=1)
def cached_inlets():
    """All SF inlets with design capacity, loaded once per process."""
    return _mod("drains").load_inlets()


# ---------------------------------------------------------------- neighborhoods

def load_neighborhoods_geojson(cache=True):
    """DataSF Analysis Neighborhoods GeoJSON (field 'nhood'), cached on disk. None if unavailable."""
    path = _processed() / "sf_neighborhoods.geojson"
    if cache and path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    try:
        import requests
        r = requests.get(NHOOD_URL, params={"$limit": 100}, timeout=60)
        r.raise_for_status()
        gj = r.json()
    except Exception:
        return json.loads(path.read_text()) if path.exists() else None
    for f in gj.get("features", []):   # ~1 m precision is plenty; keeps the committed cache small
        f["properties"] = {"nhood": (f.get("properties") or {}).get("nhood", "")}
        g = f.get("geometry") or {}
        g["coordinates"] = json.loads(json.dumps(g.get("coordinates", [])),
                                      parse_float=lambda s: round(float(s), 5))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gj, separators=(",", ":")))
    return gj


@functools.lru_cache(maxsize=1)
def _neighborhoods():
    """[(name, [outer ring arrays (n,2) lon/lat], bbox)] for each neighborhood."""
    gj = load_neighborhoods_geojson()
    out = []
    for f in (gj or {}).get("features", []):
        g, name = f.get("geometry") or {}, (f.get("properties") or {}).get("nhood", "")
        polys = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            polys = [polys]
        rings = [np.asarray(p[0], float) for p in polys if p]
        if not rings:
            continue
        allpts = np.vstack(rings)
        out.append((name, rings, (*allpts.min(0), *allpts.max(0))))
    return tuple(out)


def _in_ring(lon, lat, ring):
    """Ray-casting point-in-polygon, vectorised over points."""
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    inside = np.zeros(lon.shape, bool)
    for xa, ya, xb, yb in zip(x, y, x2, y2):
        if ya == yb:
            continue
        cross = (ya > lat) != (yb > lat)
        xi = xa + (lat - ya) * (xb - xa) / (yb - ya)
        inside ^= cross & (lon < xi)
    return inside


def neighborhood_of(lats, lons, nearest=True):
    """Neighborhood name for each point; nearest polygon (by vertex) if outside all, '' if no data."""
    lats, lons = np.atleast_1d(np.asarray(lats, float)), np.atleast_1d(np.asarray(lons, float))
    names = np.full(lats.shape, "", dtype=object)
    nh = _neighborhoods()
    if not nh:
        return list(names)
    for name, rings, (x0, y0, x1, y1) in nh:
        cand = (names == "") & (lons >= x0) & (lons <= x1) & (lats >= y0) & (lats <= y1)
        if not cand.any():
            continue
        idx = np.flatnonzero(cand)
        hit = np.zeros(len(idx), bool)
        for ring in rings:
            hit |= _in_ring(lons[idx], lats[idx], ring)
        names[idx[hit]] = name
    if nearest:
        for i in np.flatnonzero(names == ""):
            best, bd = "", np.inf
            for name, rings, _ in nh:
                pts = np.vstack(rings)
                d = np.min((pts[:, 0] - lons[i]) ** 2 * np.cos(np.radians(lats[i])) ** 2 + (pts[:, 1] - lats[i]) ** 2)
                if d < bd:
                    best, bd = name, d
            names[i] = best
    return list(names)


# ---------------------------------------------------------------- hotspots

def _patches(result, min_depth_m):
    depth = np.nan_to_num(result.depth_m, nan=0.0)
    wet = depth >= min_depth_m
    labels, n = ndimage.label(wet, structure=np.ones((3, 3), int))
    return depth, labels, n


def hotspots(result, n=15, min_depth_m=FLOOD_M, _all=False):
    """Connected flooded patches (8-connectivity), biggest area x depth first, top n."""
    cols = ["lat", "lon", "max_depth_m", "max_depth_in", "area_m2", "area_acres", "cells", "neighborhood"]
    depth, labels, k = _patches(result, min_depth_m)
    if k == 0:
        return pd.DataFrame(columns=cols)
    ids = np.arange(1, k + 1)
    cells = ndimage.sum_labels(np.ones_like(depth), labels, ids)
    maxd = ndimage.maximum(depth, labels, ids)
    pos = ndimage.maximum_position(depth, labels, ids)
    area = cells * result.grid.cell_area_m2
    order = np.argsort(-(area * maxd), kind="stable")
    if not _all:
        order = order[:n]
    rr = np.array([pos[i][0] for i in order])
    cc = np.array([pos[i][1] for i in order])
    lat, lon = result.grid.latlon(rr, cc)
    df = pd.DataFrame({"lat": np.round(lat, 6), "lon": np.round(lon, 6),
                       "max_depth_m": np.round(maxd[order], 3),
                       "max_depth_in": np.round(maxd[order] / 0.0254, 1),
                       "area_m2": np.round(area[order], 0), "area_acres": np.round(area[order] / M2_PER_ACRE, 2),
                       "cells": cells[order].astype(int)})
    df["neighborhood"] = neighborhood_of(df["lat"].values, df["lon"].values) if len(df) else []
    return df.reset_index(drop=True)


def _worst_neighborhoods(result, k=5):
    """Top k neighborhoods by flooded area (all flooded cells, not just top hotspots)."""
    depth = np.nan_to_num(result.depth_m, nan=0.0)
    r, c = np.nonzero(depth >= FLOOD_M)
    if not len(r):
        return []
    lat, lon = result.grid.latlon(r, c)
    names = pd.Series(neighborhood_of(lat, lon, nearest=False))
    names = names[names != ""]
    return list(names.value_counts().head(k).index)


# ---------------------------------------------------------------- run

def _dry_commonality(rain):
    return Commonality(return_period_yr=None, annual_chance_pct=None, duration_hr=rain.duration_hr,
                       depth_in=0.0, method="none", sentence="No rain in this event, so nothing floods.")


def _summary(result, comm, inlets, rain):
    a = result.grid.cell_area_m2
    depth = np.nan_to_num(result.depth_m, nan=0.0)
    flooded_m2 = float((depth >= FLOOD_M).sum() * a)
    rp = getattr(comm, "return_period_yr", None)
    return {
        "flooded_km2": round(flooded_m2 / 1e6, 4),
        "flooded_acres": round(flooded_m2 / M2_PER_ACRE, 1),
        "deep_km2": round(float((depth >= DEEP_M).sum() * a) / 1e6, 4),
        "max_depth_in": round(float(depth.max()) / 0.0254, 1) if depth.size else 0.0,
        "rain_in": round(float(rain.depth_in), 2),
        "duration_hr": float(rain.duration_hr),
        "peak_in_hr": round(float(rain.peak_in_hr), 2),
        "drained_pct": round(100.0 * result.volume_drained_m3 / result.volume_runoff_m3, 1)
        if result.volume_runoff_m3 > 0 else 0.0,
        "ponded_m3": round(float(result.volume_ponded_m3)),
        "to_bay_m3": round(float(result.volume_to_bay_m3)),
        "inlets_overwhelmed": int(inlets["overwhelmed"].sum()) if "overwhelmed" in inlets else 0,
        "inlets_total": int(len(inlets)),
        "return_period_yr": rp,
        "annual_chance_pct": getattr(comm, "annual_chance_pct", None),
        "sentence": getattr(comm, "sentence", ""),
        "worst_neighborhoods": _worst_neighborhoods(result),
    }


def run(rain, cell_m=30.0, clog_pct=0.0, runoff_coeff=0.85, progress=None, grid=None, inlets=None):
    """Simulate one rain event over SF and package everything the app/CLI show."""
    params = {"cell_m": cell_m, "clog_pct": clog_pct, "runoff_coeff": runoff_coeff,
              "source": rain.source, "label": rain.label, "date": rain.date}
    grid = grid if grid is not None else cached_grid(cell_m)
    inlets = (inlets if inlets is not None else cached_inlets()).copy()
    if not rain.depth_in or rain.depth_in <= 0:
        depth = np.where(np.isnan(grid.z), np.nan, 0.0).astype(np.float32)
        if grid.land is not None:
            depth = np.where(grid.land, depth, np.nan).astype(np.float32)
        result = FloodResult(depth_m=depth, grid=grid, rain=rain)
        result.peak_inflow_m3s = np.zeros(grid.shape)
        inlets["overwhelmed"] = False
        comm = _dry_commonality(rain)
        summary = _summary(result, comm, inlets, rain)
        summary["sentence"] = comm.sentence
        return Scenario(result, comm, hotspots(result), inlets, summary, params)

    drains = _mod("drains")
    cap = drains.capacity_grid(grid, inlets, clog_pct=clog_pct, runoff_coeff=runoff_coeff)
    result = _mod("hydro").simulate(grid, cap, rain, runoff_coeff=runoff_coeff, progress=progress)
    inflow = getattr(result, "peak_inflow_m3s", None)
    if inflow is not None:
        inlets["overwhelmed"] = np.asarray(drains.overwhelmed(inlets, grid, inflow), bool)
    else:
        inlets["overwhelmed"] = False
    try:
        comm = _mod("frequency").commonality(rain)
    except Exception as e:  # commonality is a nice-to-have; never lose the flood map over it
        comm = Commonality(None, None, rain.duration_hr, rain.depth_in, "unavailable",
                           f"Couldn't estimate how common this storm is ({e}).")
    return Scenario(result, comm, hotspots(result), inlets, _summary(result, comm, inlets, rain), params)


# ---------------------------------------------------------------- export

def _rects(mask):
    """Cover a bool mask with rectangles (row runs merged down identical columns): [(r0, r1, c0, c1)] exclusive."""
    open_runs, out = {}, []
    for r in range(mask.shape[0]):
        row = np.concatenate([[False], mask[r], [False]])
        d = np.diff(row.astype(np.int8))
        runs = set(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))
        for key in list(open_runs):
            if key not in runs:
                out.append((open_runs.pop(key), r, *key))
        for key in runs:
            open_runs.setdefault(key, r)
    out += [(r0, mask.shape[0], *key) for key, r0 in open_runs.items()]
    return out


def geojson(scenario, min_depth_m=0.05):
    """Flooded cells as a GeoJSON FeatureCollection: one MultiPolygon per depth class."""
    res = scenario.result if isinstance(scenario, Scenario) else scenario
    g = res.grid
    depth = np.nan_to_num(res.depth_m, nan=0.0)
    feats = []
    for lo, hi, label in DEPTH_CLASSES:
        lo = max(lo, min_depth_m)
        if lo >= hi:
            continue
        mask = (depth >= lo) & (depth < hi)
        if not mask.any():
            continue
        polys = []
        for r0, r1, c0, c1 in _rects(mask):
            n, s = g.lat_top - r0 * g.dlat, g.lat_top - r1 * g.dlat
            w, e = g.lon_left + c0 * g.dlon, g.lon_left + c1 * g.dlon
            ring = [[round(w, 6), round(s, 6)], [round(e, 6), round(s, 6)], [round(e, 6), round(n, 6)],
                    [round(w, 6), round(n, 6)], [round(w, 6), round(s, 6)]]
            polys.append([ring])
        feats.append({"type": "Feature",
                      "geometry": {"type": "MultiPolygon", "coordinates": polys},
                      "properties": {"depth_class": label, "min_depth_m": lo,
                                     "max_depth_m": None if np.isinf(hi) else hi,
                                     "area_m2": round(float(mask.sum() * g.cell_area_m2))}})
    return {"type": "FeatureCollection", "features": feats}
