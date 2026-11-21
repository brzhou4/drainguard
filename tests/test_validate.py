"""Validation metrics on a synthetic grid (no network, no real model run)."""
import json

import numpy as np
import pandas as pd

from src.flood import FloodResult, Grid, RainEvent
from src.flood import validate as v


def _grid(n=40):
    z = np.ones((n, n), np.float32)
    return Grid(z=z, lat_top=37.80, lon_left=-122.45, dlat=0.00027, dlon=0.00034, land=np.ones((n, n), bool))


def test_nearest_and_hit_rate():
    g = _grid()
    mask = np.zeros(g.shape, bool)
    mask[10, 10] = True
    lat, lon = g.latlon(np.array([10, 10, 30]), np.array([10, 12, 30]))
    d = v.nearest_m(g, mask, lat, lon)
    assert d[0] < 1e-6
    assert abs(d[1] - 2 * g.cell_dx_m) < 0.5
    assert v.hit_rate(g, mask, lat, lon, 100) == 2 / 3
    assert np.isinf(v.nearest_m(g, np.zeros(g.shape, bool), lat, lon)).all()


def test_random_points_stay_on_land():
    g = _grid()
    g.land[:, 20:] = False
    lat, lon = v.random_land_points(g, 500, np.random.default_rng(0))
    r, c = g.rc(lat, lon)
    assert g.land[r, c].all()


def test_lift_high_when_incidents_sit_on_flood_and_about_one_when_random():
    g = _grid()
    mask = np.zeros(g.shape, bool)
    mask[5:8, 5:8] = True
    lat, lon = g.latlon(np.full(50, 6), np.full(50, 6))
    rows = v.score_mask(g, mask, lat, lon, "t", dists=(50,), draws=50)
    assert rows[0]["hit_rate"] == 1.0 and rows[0]["lift"] > 5
    assert abs(rows[0]["area_share"] - 9 / 1600) < 1e-4
    rlat, rlon = v.random_land_points(g, 2000, np.random.default_rng(1))
    rows = v.score_mask(g, mask, rlat, rlon, "t", dists=(50,), draws=50, null_lat=rlat, null_lon=rlon)
    assert 0.7 < rows[0]["lift"] < 1.3
    assert rows[0]["lift_vs_inlets"] == 1.0


def test_chance_is_reproducible():
    g = _grid()
    mask = np.zeros(g.shape, bool)
    mask[20, 20] = True
    a = v.chance_hit(g, mask, 30, (100,), draws=20)[100][0]
    b = v.chance_hit(g, mask, 30, (100,), draws=20)[100][0]
    assert a == b


def test_neighborhood_table_spearman():
    g = _grid()
    depth = np.zeros(g.shape, np.float32)
    depth[:, :10] = 0.5   # west strip floods
    depth[:, 10:15] = 0.5

    def names(lat, lon, nearest=True):   # four vertical strips = four neighborhoods
        _, c = g.rc(lat, lon)
        return [f"N{min(int(x) // 10, 3)}" for x in np.atleast_1d(c)]

    cols = np.r_[np.full(30, 2), np.full(10, 12), np.full(3, 25)]
    lat, lon = g.latlon(np.full(len(cols), 5), cols)
    tab, sp = v.neighborhood_table(g, depth, lat, lon, names_fn=names)
    assert list(tab["neighborhood"][:3]) == ["N0", "N1", "N2"]
    assert tab.set_index("neighborhood").loc["N3", "incidents"] == 0
    assert sp["n_neighborhoods"] == 4 and sp["rho"] > 0.9


def test_flood_zone_mask(tmp_path):
    g = _grid()
    (la0, lo0), (la1, lo1) = g.bounds()
    ring = [[lo0, la0], [lo0 + (lo1 - lo0) / 2, la0], [lo0 + (lo1 - lo0) / 2, la1], [lo0, la1], [lo0, la0]]
    p = tmp_path / "zone.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection",
                             "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}}]}))
    m = v.flood_zone_mask(g, p)
    assert m[:, :19].all() and not m[:, 21:].any()
    assert v.flood_zone_mask(g, tmp_path / "missing.geojson") is None


def test_evaluate_and_sentence():
    g = _grid()
    depth = np.zeros(g.shape, np.float32)
    depth[5:8, 5:8] = 0.4
    res = FloodResult(depth_m=depth, grid=g, rain=RainEvent(1.0, 6.0, np.ones(24)))
    lat, lon = g.latlon(np.r_[np.full(20, 6), 100], np.r_[np.full(20, 6), 6])   # last one is off-grid
    rows, la, lo, notes = v.evaluate(res, pd.DataFrame({"lat": lat, "lon": lon}), zone=False)
    assert len(rows) == 6 and len(la) == 20 and "20 of 21" in notes[0]
    s = v.sentence(rows)
    assert "better than chance" in s
    flat = [dict(r, lift=1.02, chance_hit_rate=r["hit_rate"]) for r in rows]
    assert "doesn't beat chance" in v.sentence(flat)
