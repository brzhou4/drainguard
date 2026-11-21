"""Tests for src.flood.calc and src.flood.cli with fake pipeline modules (no network)."""
import json
import sys
import types

import numpy as np
import pandas as pd
import pytest

from src.flood import Commonality, FloodResult, Grid, RainEvent, calc, cli

# 20x20 grid of ~30 m cells near the Mission.
DLAT, DLON = 30 / 110540.0, 30 / (111320.0 * np.cos(np.radians(37.76)))
LAT_TOP, LON_LEFT = 37.77, -122.42


def make_grid():
    z = np.full((20, 20), 10.0, np.float32)
    z[:, -2:] = np.nan
    return Grid(z=z, lat_top=LAT_TOP, lon_left=LON_LEFT, dlat=DLAT, dlon=DLON, land=~np.isnan(z))


def make_inlets():
    return pd.DataFrame({"asset_id": ["A", "B", "C"], "lat": [37.769, 37.768, 37.767],
                         "lon": [-122.419, -122.418, -122.417], "drain_type": "inlet",
                         "system_use": "combined", "capacity_cfs": 1.0, "capacity_m3s": 0.0283})


def rain(depth=2.5, dur=6.0):
    n = int(dur * 4)
    mm_hr = np.full(n, depth * 25.4 / dur) if depth else np.zeros(n)
    return RainEvent(depth_in=depth, duration_hr=dur, intensity_mm_hr=mm_hr, label="test storm")


def fake_simulate(grid, cap, rn, runoff_coeff=0.85, n_manning=0.016, progress=None):
    d = np.zeros(grid.shape, np.float32)
    d[2:5, 2:5] = 0.4          # big pond, 9 cells, deepest 0.8 at (3,3)
    d[3, 3] = 0.8
    d[10, 10] = 1.0            # single deep cell
    d[15:17, 1] = 0.2          # shallow 2-cell strip
    d[12, 12] = 0.1            # below 0.15, not a hotspot
    d[np.isnan(grid.z)] = np.nan
    if progress:
        progress(1.0)
    r = FloodResult(depth_m=d, grid=grid, rain=rn, volume_rain_m3=1000, volume_runoff_m3=800,
                    volume_drained_m3=200, volume_ponded_m3=300, volume_to_bay_m3=100)
    r.peak_inflow_m3s = np.ones(grid.shape)
    return r


# Two neighborhoods split down the middle of the grid.
MID = LON_LEFT + 10 * DLON
BOT = LAT_TOP - 20 * DLAT


def _box(x0, x1, y0, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


FAKE_NHOODS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"nhood": "West Side"},
     "geometry": {"type": "MultiPolygon", "coordinates": [_box(LON_LEFT, MID, BOT, LAT_TOP)]}},
    {"type": "Feature", "properties": {"nhood": "East Side"},
     "geometry": {"type": "Polygon", "coordinates": _box(MID, LON_LEFT + 17 * DLON, BOT, LAT_TOP)}},
]}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    calls = {}
    drains = types.ModuleType("src.flood.drains")

    def capacity_grid(grid, inlets, clog_pct=0.0, runoff_coeff=0.85):
        calls["clog"] = clog_pct
        return np.zeros(grid.shape)
    drains.capacity_grid = capacity_grid
    drains.overwhelmed = lambda inlets, grid, inflow: pd.Series([True, False, True], index=inlets.index)
    drains.load_inlets = lambda cache=True: make_inlets()

    hydro = types.ModuleType("src.flood.hydro")
    hydro.simulate = fake_simulate

    freq = types.ModuleType("src.flood.frequency")
    freq.commonality = lambda rn, table=None, ghcn=None: Commonality(
        5.0, 20.0, rn.duration_hr, rn.depth_in, "NOAA Atlas 14", "About a 1-in-5-year storm.")

    terrain = types.ModuleType("src.flood.terrain")
    terrain.load_dem = lambda cell_m=30.0, bbox=None, cache=True: make_grid()

    wi = types.ModuleType("src.flood.weather_input")
    wi.from_manual = lambda d, h, shape="scs1", step_min=15: rain(d, h)
    wi.from_date = lambda date, step_min=15: rain(1.0, 24)
    wi.from_today = lambda now=None, step_min=15: rain(0.0, 24)
    wi.notable_storms = lambda n=10: pd.DataFrame({"date": ["2026-11-18"], "depth_in": [3.1]})

    for name, m in [("drains", drains), ("hydro", hydro), ("frequency", freq), ("terrain", terrain),
                    ("weather_input", wi)]:
        monkeypatch.setitem(sys.modules, f"src.flood.{name}", m)
    monkeypatch.setattr(calc, "load_neighborhoods_geojson", lambda cache=True: FAKE_NHOODS)
    calc._neighborhoods.cache_clear()
    calc.cached_grid.cache_clear()
    calc.cached_inlets.cache_clear()
    yield calls
    calc._neighborhoods.cache_clear()
    calc.cached_grid.cache_clear()
    calc.cached_inlets.cache_clear()


def test_run_end_to_end(fakes):
    sc = calc.run(rain(), clog_pct=20, grid=make_grid(), inlets=make_inlets())
    assert fakes["clog"] == 20
    assert sc.inlets["overwhelmed"].tolist() == [True, False, True]
    s = sc.summary
    cell = make_grid().cell_area_m2
    assert s["flooded_km2"] == pytest.approx(12 * cell / 1e6, abs=1e-4)
    assert s["deep_km2"] == pytest.approx(2 * cell / 1e6, abs=1e-4)
    assert s["max_depth_in"] == pytest.approx(1.0 / 0.0254, abs=0.1)
    assert s["drained_pct"] == 25.0
    assert s["inlets_overwhelmed"] == 2 and s["inlets_total"] == 3
    assert s["return_period_yr"] == 5.0 and s["annual_chance_pct"] == 20.0
    assert s["sentence"].startswith("About")
    assert s["worst_neighborhoods"] == ["West Side", "East Side"]
    for k in ["flooded_acres", "rain_in", "duration_hr", "peak_in_hr", "ponded_m3", "to_bay_m3"]:
        assert k in s
    assert len(sc.hotspots) == 3


def test_run_uses_cached_loaders():
    sc = calc.run(rain())
    assert sc.inlets["overwhelmed"].sum() == 2
    assert calc.cached_grid(30.0) is calc.cached_grid(30.0)


def test_dry_path_skips_simulation(monkeypatch):
    monkeypatch.setattr(sys.modules["src.flood.hydro"], "simulate",
                        lambda *a, **k: pytest.fail("simulate should not run"))
    sc = calc.run(rain(0.0), grid=make_grid(), inlets=make_inlets())
    assert np.nanmax(sc.result.depth_m) == 0
    assert np.isnan(sc.result.depth_m[0, -1])
    assert sc.hotspots.empty
    assert sc.summary["flooded_km2"] == 0 and not sc.inlets["overwhelmed"].any()
    assert "no rain" in sc.summary["sentence"].lower()


def test_hotspots_grouping_and_ordering():
    res = fake_simulate(make_grid(), None, rain())
    hs = calc.hotspots(res)
    assert hs["cells"].tolist() == [9, 1, 2]     # 9*0.8 > 1*1.0 > 2*0.2
    top = hs.iloc[0]
    lat, lon = make_grid().latlon(3, 3)
    assert top["lat"] == pytest.approx(lat, abs=1e-6) and top["lon"] == pytest.approx(lon, abs=1e-6)
    assert top["max_depth_m"] == 0.8 and top["max_depth_in"] == pytest.approx(31.5, abs=0.1)
    assert top["area_acres"] == pytest.approx(top["area_m2"] / 4046.856, abs=0.01)
    assert hs["neighborhood"].tolist() == ["West Side", "East Side", "West Side"]
    assert len(calc.hotspots(res, n=2)) == 2
    assert len(calc.hotspots(res, min_depth_m=0.5)) == 2


def test_hotspots_diagonal_is_connected():
    g = make_grid()
    d = np.zeros(g.shape)
    d[5, 5] = d[6, 6] = 0.3
    hs = calc.hotspots(FloodResult(depth_m=d, grid=g, rain=rain()))
    assert len(hs) == 1 and hs["cells"][0] == 2


def test_neighborhood_nearest_fallback():
    # A point just east of East Side box falls back to the nearest polygon.
    assert calc.neighborhood_of([LAT_TOP - 5 * DLAT], [LON_LEFT + 19 * DLON]) == ["East Side"]
    assert calc.neighborhood_of([LAT_TOP - 5 * DLAT], [LON_LEFT + 19 * DLON], nearest=False) == [""]


def test_geojson_valid():
    sc = calc.run(rain(), grid=make_grid(), inlets=make_inlets())
    gj = json.loads(json.dumps(calc.geojson(sc)))
    assert gj["type"] == "FeatureCollection"
    classes = {f["properties"]["depth_class"] for f in gj["features"]}
    assert len(classes) == 4                 # 0.1, 0.2, 0.4, 0.8/1.0
    g = make_grid()
    total = 0
    for f in gj["features"]:
        assert f["geometry"]["type"] == "MultiPolygon"
        for poly in f["geometry"]["coordinates"]:
            ring = poly[0]
            assert ring[0] == ring[-1] and len(ring) == 5
            for lon, lat in ring:
                assert LON_LEFT - 1e-6 <= lon <= LON_LEFT + 20 * DLON + 1e-6
                assert BOT - 1e-6 <= lat <= LAT_TOP + 1e-6
        total += f["properties"]["area_m2"]
    assert total == pytest.approx(13 * g.cell_area_m2, rel=1e-3)
    # the 3x3 pond's 0.4 m ring is 8 cells but merges into few rectangles
    mid = [f for f in gj["features"] if f["properties"]["min_depth_m"] == 0.3][0]
    assert len(mid["geometry"]["coordinates"]) <= 4


def test_rects_cover_mask():
    rng = np.random.default_rng(0)
    m = rng.random((15, 12)) > 0.6
    cover = np.zeros_like(m)
    for r0, r1, c0, c1 in calc._rects(m):
        assert not cover[r0:r1, c0:c1].any()
        cover[r0:r1, c0:c1] = True
    assert (cover == m).all()


def test_cli_parsing():
    p = cli.build_parser()
    a = p.parse_args(["--depth", "2.5", "--duration", "6", "--shape", "burst", "--clog", "20"])
    assert a.depth == 2.5 and a.duration == 6 and a.shape == "burst" and a.clog == 20 and a.cell == 30
    a = p.parse_args(["--date", "2026-11-18", "--geojson", "o.geojson", "--png", "o.png", "--csv", "h.csv"])
    assert a.date == "2026-11-18" and a.geojson == "o.geojson"
    with pytest.raises(SystemExit):
        p.parse_args(["--today", "--date", "2026-11-18"])
    with pytest.raises(SystemExit):
        p.parse_args(["--depth", "1", "--shape", "weird"])


def test_cli_run_prints_report(capsys, tmp_path):
    gj, csv = tmp_path / "o.geojson", tmp_path / "h.csv"
    rc = cli.main(["--depth", "2.5", "--duration", "6", "--geojson", str(gj), "--csv", str(csv)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "test storm" in out and "2.50 in over 6 h" in out
    assert "About a 1-in-5-year storm." in out and "1 in 5.0 years" in out
    assert "Inlets overwhelmed:      2 of 3" in out
    assert "Top 3 hotspots" in out and "West Side" in out
    assert json.loads(gj.read_text())["type"] == "FeatureCollection"
    assert len(pd.read_csv(csv)) == 3


def test_cli_dry_today(capsys):
    assert cli.main(["--today"]) == 0
    out = capsys.readouterr().out
    assert "No rain" in out and "No flooded hotspots" in out


def test_cli_friendly_errors(capsys):
    assert cli.main(["--depth", "2"]) == 1
    assert "Error: --depth needs --duration" in capsys.readouterr().err


def test_cli_list_storms(capsys):
    assert cli.main(["--list-storms"]) == 0
    assert "2026-11-18" in capsys.readouterr().out
