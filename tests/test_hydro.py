"""Tests for src/flood/hydro.py (synthetic terrain, no network)."""
import numpy as np
import pytest

from src.flood import DEFAULT_CELL_M, M_PER_DEG_LAT, M_PER_DEG_LON, Grid, RainEvent
from src.flood import hydro

DLAT = DEFAULT_CELL_M / M_PER_DEG_LAT
DLON = DEFAULT_CELL_M / M_PER_DEG_LON


def make_grid(z, land=None):
    z = np.asarray(z, float)
    return Grid(z=z.astype(np.float32), lat_top=37.8, lon_left=-122.5, dlat=DLAT, dlon=DLON,
                land=np.isfinite(z) if land is None else land)


def make_rain(total_in=2.0, hours=6.0, step_min=15.0, shape="peak"):
    n = int(round(hours * 60 / step_min))
    t = np.arange(n)
    w = np.exp(-((t - n * 0.4) / (n * 0.15 + 1e-9)) ** 2) if shape == "peak" else np.ones(n)
    inten = w / (w.sum() * step_min / 60) * total_in * 25.4
    return RainEvent(depth_in=total_in, duration_hr=hours, intensity_mm_hr=inten, step_min=step_min)


def sf_like(rows=150, cols=160, seed=0):
    """Hills, valleys, closed bowls, 0.5 m DEM quantisation (flats), a coastline at z <= 0, NaN strip."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols].astype(float)
    z = 30 + 0.05 * (cols - xx)
    for _ in range(10):
        cy, cx, h, s = rng.uniform(0, rows), rng.uniform(0, cols), rng.uniform(20, 90), rng.uniform(8, 25)
        z += h * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    for _ in range(6):
        cy, cx, h, s = rng.uniform(20, rows - 20), rng.uniform(20, cols - 40), rng.uniform(3, 8), rng.uniform(3, 6)
        z -= h * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    z = np.round(z + rng.normal(0, 0.2, z.shape), 0) * 1.0
    coast = xx > cols * 0.85 + 6 * np.sin(yy / 12)
    z[coast] = -3.0
    land = ~coast
    z[:, :4] = np.nan
    land[:, :4] = False
    return make_grid(z, land)


def bowl_grid():
    """25x25: bowl (z 4..4.6) inside a ring at 12 m with an east notch at 9 m; outside is 5 m (drains to edge)."""
    n = 25
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    r = np.hypot(yy - 12, xx - 12)
    z = np.full((n, n), 5.0)
    z[r < 8.5] = 12.0
    bowl = r < 6
    z[bowl] = 4 + 0.1 * r[bowl]
    notch = (yy == 12) & (xx > 12) & (r >= 6) & (r < 8.5)
    z[notch] = 9.0
    return make_grid(z), bowl


def chain_reaches_outlet(rec):
    """Follow every active cell downstream; True if all reach -1 without looping."""
    cur = np.flatnonzero(rec >= -1)
    pos = cur.copy()
    for _ in range(rec.size + 1):
        nxt = rec[pos]
        if np.all(nxt == -1):
            return True
        if np.any(nxt == -2):
            return False
        pos = np.where(nxt >= 0, nxt, -1)
        keep = pos >= 0
        pos = pos[keep]
    return False


def test_fill_simple_pit():
    z = np.full((5, 5), 10.0)
    z[1:4, 1:4] = 8.0
    z[2, 2] = 5.0
    z[0, 2] = 7.0               # outlet notch on the edge row (edge cells are outlets themselves)
    f = hydro.fill_depressions(z)
    assert f[2, 2] == pytest.approx(8.0)
    assert np.all(f >= z)
    labels, deps = hydro.depression_ids(z, f, cell_area_m2=900.0)
    assert len(deps) == 1 and labels[2, 2] == 1
    assert deps[0]["volume_full_m3"] == pytest.approx(3.0 * 900.0)
    assert deps[0]["spill_m"] == pytest.approx(8.0)


def test_flats_resolved_everywhere():
    g = sf_like()
    z = g.z.astype(float)
    f = hydro.fill_depressions(z, g.land)
    rec = hydro.flow_directions(f, z, g.land)
    act = g.land & np.isfinite(z) & (z > 0)
    assert np.array_equal(rec.reshape(z.shape) >= -1, act)
    assert chain_reaches_outlet(rec)
    order, level = hydro.flow_order(rec)
    assert order.size == act.sum()
    down = rec[order]
    ok = down >= 0
    assert np.all(level[down[ok]] > level[order[ok]])       # strictly upstream -> downstream
    # a completely flat plateau also drains
    flat = np.full((30, 30), 7.0)
    rec2 = hydro.flow_directions(hydro.fill_depressions(flat), flat)
    assert chain_reaches_outlet(rec2)


def test_mass_balance_realistic():
    g = sf_like()
    rng = np.random.default_rng(3)
    cap = np.where(rng.random(g.shape) < 0.3, 0.02, 0.0)
    rain = make_rain(3.0, 6.0)
    res = hydro.simulate(g, cap, rain)
    rhs = res.volume_drained_m3 + res.volume_ponded_end_m3 + res.volume_to_bay_m3
    assert res.volume_runoff_m3 > 0
    assert abs(rhs - res.volume_runoff_m3) / res.volume_runoff_m3 < 0.01
    assert res.volume_runoff_m3 == pytest.approx(0.85 * res.volume_rain_m3)
    assert res.volume_ponded_m3 >= res.volume_ponded_end_m3 > 0
    assert np.all(np.isnan(res.depth_m[~g.land]))
    assert np.all(res.depth_m[g.land] >= 0)
    assert res.peak_inflow_m3s.shape == g.shape
    assert len(res.timeline) == rain.intensity_mm_hr.size
    assert set(res.timeline[0]) == {"t_hr", "flooded_km2", "ponded_m3"}
    assert res.drains_overwhelmed > 0


def test_bowl_fills_then_spills_at_rim():
    g, bowl = bowl_grid()
    area = g.cell_area_m2
    full = float(np.sum(9.0 - g.z[bowl].astype(float))) * area
    # small storm: stays below the notch, no water leaves the bowl
    small = hydro.simulate(g, 0.0, make_rain(0.5, 2.0), max_pond_depth_m=None)
    assert 0 < small.volume_ponded_end_m3 < full
    lvl_small = 4.0 + small.depth_m[12, 12]
    assert 4.0 < lvl_small < 9.0
    # huge storm: fills to the notch level exactly and spills the rest
    big = hydro.simulate(g, 0.0, make_rain(200.0, 2.0), max_pond_depth_m=None)
    assert big.volume_ponded_end_m3 == pytest.approx(full, rel=1e-6)
    assert big.depth_m[12, 12] == pytest.approx(9.0 - 4.0, abs=1e-4)
    assert big.depth_m[12, 15] == pytest.approx(9.0 - g.z[12, 15], abs=1e-4)
    assert big.volume_to_bay_m3 > 0
    rhs = big.volume_drained_m3 + big.volume_ponded_end_m3 + big.volume_to_bay_m3
    assert rhs == pytest.approx(big.volume_runoff_m3, rel=1e-6)


def test_deep_pit_is_breached():
    """A 5 m deep bowl (reservoir / culverted valley) is breached by default: no storage, water drains away."""
    g, bowl = bowl_grid()
    res = hydro.simulate(g, 0.0, make_rain(2.0, 2.0))
    assert res.n_breached_pits == 1
    assert res.volume_ponded_m3 == 0
    assert res.volume_culvert_m3 > 0
    assert res.volume_drained_m3 == pytest.approx(res.volume_culvert_m3)
    assert np.nanmax(res.depth_m) < 0.5
    rhs = res.volume_drained_m3 + res.volume_ponded_end_m3 + res.volume_to_bay_m3
    assert rhs == pytest.approx(res.volume_runoff_m3, rel=1e-9)
    # a threshold above the bowl depth keeps it as a pond
    kept = hydro.simulate(g, 0.0, make_rain(2.0, 2.0), max_pond_depth_m=6.0)
    assert kept.n_breached_pits == 0 and kept.volume_ponded_m3 > 0


def test_pond_depth_capped_realistic():
    g = sf_like()
    res = hydro.simulate(g, 0.0, make_rain(20.0, 6.0), max_pond_depth_m=1.0)
    pond = res.pond_labels > 0
    assert np.all(res.depth_m[pond] <= 1.0 + 1e-6)
    assert res.n_breached_pits > 0
    rhs = res.volume_drained_m3 + res.volume_ponded_end_m3 + res.volume_to_bay_m3
    assert rhs == pytest.approx(res.volume_runoff_m3, rel=1e-9)
    filled = hydro.fill_depressions(g.z.astype(float), g.land)
    assert np.nanmax(filled - g.z) > 1.0          # the raw terrain did have deeper pits


def test_pond_drains_shrink_after_burst():
    g, bowl = bowl_grid()
    cap = np.zeros(g.shape)
    cap[12, 12] = 0.05
    rain = make_rain(1.0, 1.0)
    rain = RainEvent(1.0, 4.0, np.r_[rain.intensity_mm_hr, np.zeros(12)], 15.0)
    res = hydro.simulate(g, cap, rain, max_pond_depth_m=None)
    pond = [x["ponded_m3"] for x in res.timeline]
    assert max(pond) > 0
    assert pond[-1] < max(pond)
    assert res.overwhelmed_mask[12, 12]


def test_inclined_plane_no_ponding():
    rows, cols = 40, 50
    z = 2.0 + 0.03 * 30 * np.arange(cols)[None, :] * np.ones((rows, 1))
    g = make_grid(z)
    res = hydro.simulate(g, 0.0, make_rain(2.0, 3.0))
    assert res.volume_ponded_m3 == 0
    assert res.volume_to_bay_m3 == pytest.approx(res.volume_runoff_m3, rel=1e-9)
    assert np.nanmax(res.depth_m) > 0                    # moving water exists...
    assert np.nanmax(res.depth_m) < 0.5                  # ...but no deep standing water


def test_big_drains_no_flooding():
    g = sf_like()
    res = hydro.simulate(g, 1e3, make_rain(3.0, 6.0))
    assert np.nanmax(res.depth_m) == 0
    assert res.volume_drained_m3 == pytest.approx(res.volume_runoff_m3 - res.volume_to_bay_m3, rel=1e-9)
    assert res.drains_overwhelmed == 0
    assert all(x["flooded_km2"] == 0 for x in res.timeline)


def test_zero_rain_dry():
    g = sf_like()
    rain = RainEvent(0.0, 2.0, np.zeros(8))
    res = hydro.simulate(g, 0.01, rain)
    assert np.nanmax(res.depth_m) == 0
    assert np.all(np.isnan(res.depth_m[~g.land]))
    assert res.volume_runoff_m3 == 0 and res.volume_to_bay_m3 == 0 and res.drains_overwhelmed == 0


def test_more_rain_more_flooding():
    g = sf_like()
    cap = np.full(g.shape, 0.01)
    areas, ponds = [], []
    for depth in (0.5, 1.0, 2.0, 4.0, 8.0):
        res = hydro.simulate(g, cap, make_rain(depth, 6.0))
        areas.append(int(np.sum(res.depth_m >= 0.15)))
        ponds.append(res.volume_ponded_m3)
    assert all(b >= a for a, b in zip(areas, areas[1:]))
    assert all(b >= a for a, b in zip(ponds, ponds[1:]))
    assert areas[-1] > areas[0]


def test_progress_callback():
    g, _ = bowl_grid()
    seen = []
    hydro.simulate(g, 0.0, make_rain(1.0, 1.0), progress=seen.append)
    assert seen[-1] == 1.0 and all(0 <= f <= 1 for f in seen) and seen == sorted(seen)


def test_level_routing_partition():
    """Every node's outflow goes to exactly one place: the bay, a culvert sink, or a downstream node."""
    g = sf_like(seed=2)
    hydro._CACHE.clear()
    hydro.simulate(g, 0.0, make_rain(1.0, 1.0), max_pond_depth_m=0.5)
    prep = list(hydro._CACHE.values())[-1]
    assert prep["n_breached"] > 0
    seen = 0
    for lv in prep["levels"]:
        parts = [set(lv["bay"].tolist()), set(lv["culv"].tolist()), set(lv["perm"].tolist())]
        assert sum(len(p) for p in parts) == lv["nodes"].size == len(set().union(*parts))
        assert np.all(prep["down"][lv["nodes"][lv["perm"]]] >= 0)
        seen += lv["culv"].size
    assert seen == prep["n_breached"]
