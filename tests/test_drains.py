import numpy as np
import pandas as pd
import pytest

from src.flood import Grid
from src.flood import drains


def tiny_grid(n=20, cell_deg=0.0003):
    z = np.zeros((n, n), np.float32)
    return Grid(z=z, lat_top=37.80, lon_left=-122.45, dlat=cell_deg, dlon=cell_deg, land=np.ones((n, n), bool))


def fake_inlets(grid, cells, types):
    lat, lon = grid.latlon(np.array([rc[0] for rc in cells]), np.array([rc[1] for rc in cells]))
    df = pd.DataFrame({"asset_id": [f"N-{i}" for i in range(len(cells))], "lat": lat, "lon": lon,
                       "drain_type": types, "system_use": "Storm"})
    return drains._add_capacity(df)


def test_capacity_table():
    assert drains.capacity_for("Catch Basin Drain") == drains.CATCH_BASIN_CFS
    assert drains.capacity_for("Some new type") == drains.CATCH_BASIN_CFS
    assert drains.CAPACITY_CFS["LID / Other surface flow outlet point"] == 0
    assert drains.CAPACITY_CFS["Tree Well; drains soil of tree planter"] == 0


def test_pipe_limit():
    assert drains.pipe_limit_mm_hr() == pytest.approx(1.24 * 25.4 / 3)


def test_intake_and_clog():
    g = tiny_grid()
    inl = fake_inlets(g, [(5, 5), (5, 5), (15, 15)], ["Catch Basin Drain"] * 3)
    it = drains.intake_grid(g, inl)
    assert it[5, 5] == pytest.approx(2 * 2.0 * drains.CFS_TO_M3S, rel=1e-3)
    assert it.sum() == pytest.approx(3 * 2.0 * drains.CFS_TO_M3S, rel=1e-3)
    assert drains.intake_grid(g, inl, clog_pct=50).sum() == pytest.approx(it.sum() / 2)


def test_capacity_grid_min_and_area():
    g = tiny_grid()
    inl = fake_inlets(g, [(5, 5), (15, 15)], ["Catch Basin Drain"] * 2)
    pipe = drains.pipe_grid(g, inl, max_service_m=None)
    rate = drains.pipe_limit_mm_hr() / 1000 / 3600 * 0.85
    assert pipe.sum() == pytest.approx(rate * 400 * g.cell_area_m2)     # all land is served
    cap = drains.capacity_grid(g, inl, max_service_m=None)
    assert np.count_nonzero(cap) == 2
    assert cap[5, 5] == pytest.approx(min(pipe[5, 5], drains.intake_grid(g, inl)[5, 5]))
    assert cap[5, 5] < pipe[5, 5]          # 200 cells served: intake binds here


def test_offland_and_zero_capacity():
    g = tiny_grid()
    g.land[:, :10] = False
    inl = fake_inlets(g, [(5, 5), (5, 15), (10, 15)], ["Catch Basin Drain", "Catch Basin Drain",
                                                       "LID / Other surface flow outlet point"])
    cap = drains.capacity_grid(g, inl)
    assert cap[5, 5] == 0 and cap[10, 15] == 0 and cap[5, 15] > 0
    assert cap[:, :10].sum() == 0


def test_service_distance_cap():
    g = tiny_grid(n=40)
    inl = fake_inlets(g, [(0, 0)], ["Catch Basin Drain"])
    near = drains.pipe_grid(g, inl, max_service_m=100).sum()
    full = drains.pipe_grid(g, inl, max_service_m=None).sum()
    assert 0 < near < full


def test_overwhelmed():
    g = tiny_grid()
    inl = fake_inlets(g, [(2, 2), (2, 2), (8, 8), (12, 12)],
                      ["Catch Basin Drain", "Catch Basin Drain", "Catch Basin Drain", "Wetwell"])
    inflow = np.zeros(g.shape)
    inflow[2, 2] = 0.2     # > 2 x 0.0566
    inflow[8, 8] = 0.01    # < 0.0566
    inflow[12, 12] = 5.0   # zero-capacity inlet never counts
    out = drains.overwhelmed(inl, g, inflow)
    assert out.tolist() == [True, True, False, False]


def test_load_from_cache(tmp_path, monkeypatch):
    p = tmp_path / "inlets.csv"
    pd.DataFrame({"asset_id": ["N-1"], "lat": [37.77], "lon": [-122.42], "drain_type": ["Drop Inlet"],
                  "system_use": ["Storm"], "capacity_cfs": [99.0]}).to_csv(p, index=False)
    monkeypatch.setattr(drains, "CACHE", str(p))
    df = drains.load_inlets()
    assert list(df.columns) == drains.COLUMNS
    assert df["capacity_cfs"].iloc[0] == 3.0


def test_to_frame_filters():
    rows = [{"puc_maximo_asset_id": "N-1", "drain_type": "Catch Basin Drain", "system_use": "Storm",
             "location": {"coordinates": [-122.42, 37.77]}},
            {"puc_maximo_asset_id": "N-2", "drain_type": "Psuedo Drain for Model ONLY",
             "location": {"coordinates": [-122.42, 37.77]}},
            {"puc_maximo_asset_id": "N-3", "drain_type": "Catch Basin Drain",
             "location": {"coordinates": [-121.0, 37.77]}},
            {"puc_maximo_asset_id": "N-4", "drain_type": "Catch Basin Drain"}]
    df = drains._to_frame(rows)
    assert df["asset_id"].tolist() == ["N-1"]
