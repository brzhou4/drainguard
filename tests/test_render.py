"""Render tests: synthetic flood result, no network."""
import io
from types import SimpleNamespace

import numpy as np
import pandas as pd
from PIL import Image

from src.flood import FloodResult, Grid, RainEvent
from src.flood import render


def _result():
    z = np.zeros((20, 30), np.float32)
    grid = Grid(z=z, lat_top=37.80, lon_left=-122.50, dlat=0.001, dlon=0.001, land=np.ones_like(z, bool))
    depth = np.zeros_like(z)
    depth[2, 3] = 0.05      # 2 in
    depth[5, 5] = 0.2
    depth[6, 6] = 0.4
    depth[7, 7] = 1.0
    depth[0, 0] = np.nan
    depth[1, 1] = 0.01      # below threshold -> dry
    rain = RainEvent(depth_in=2.0, duration_hr=2.0, intensity_mm_hr=np.array([5.0, 20.0, 10.0, 0.0] * 2))
    tl = [{"t_hr": t, "flooded_km2": 0.1 * t, "ponded_m3": 100 * t} for t in (0.5, 1.0, 1.5)]
    return FloodResult(depth_m=depth, grid=grid, rain=rain, timeline=tl)


def _scenario():
    r = _result()
    hot = pd.DataFrame({"lat": [37.795], "lon": [-122.495], "max_depth_m": [1.0], "max_depth_in": [39.4],
                        "area_m2": [5000.0], "area_acres": [1.24], "neighborhood": ["Mission"]})
    inl = pd.DataFrame({"asset_id": ["A", "B"], "lat": [37.79, 37.791], "lon": [-122.49, -122.491],
                        "drain_type": ["inlet", "inlet"], "capacity_cfs": [1.0, 1.0], "overwhelmed": [True, False]})
    return SimpleNamespace(result=r, hotspots=hot, inlets=inl, summary={}, params={})


def test_depth_png():
    img = Image.open(io.BytesIO(render.depth_png(_result())))
    assert img.mode == "RGBA" and img.size == (30, 20)
    a = np.array(img)
    assert a[0, 0, 3] == 0 and a[1, 1, 3] == 0 and a[10, 10, 3] == 0
    for rc in [(2, 3), (5, 5), (6, 6), (7, 7)]:
        assert a[rc][3] > 150
    # deeper is darker
    assert a[7, 7, :3].sum() < a[6, 6, :3].sum() < a[5, 5, :3].sum() < a[2, 3, :3].sum()


def test_depth_classes_shape():
    assert len(render.DEPTH_CLASSES) == 4
    assert all(len(c) == 4 and c[2].startswith("#") for c in render.DEPTH_CLASSES)


def test_flood_map_renders():
    for bm in ["Terrain shading (USGS)", "Streets", "not-a-basemap"]:
        html = render.flood_map(_scenario(), basemap=bm).get_root().render()
        assert "L.imageOverlay" in html and "data:image/png;base64" in html
        assert "Mission" in html and "Water depth" in html


def test_flood_map_handles_empty():
    s = _scenario()
    s.hotspots = pd.DataFrame(columns=s.hotspots.columns)
    s.inlets = None
    assert "L.imageOverlay" in render.flood_map(s).get_root().render()


def test_charts():
    r = _result()
    h = render.hyetograph_chart(r.rain)
    assert list(h.columns) == ["Rain (in/h)"] and len(h) == 8
    assert abs(h["Rain (in/h)"].iloc[1] - 20 / 25.4) < 1e-3
    t = render.timeline_chart(r)
    assert list(t.columns) == ["Flooded acres"] and len(t) == 3 and t.index.name == "Hours"
    assert t["Flooded acres"].notna().all() and abs(t["Flooded acres"].iloc[-1] - 0.15 * 247.105) < 0.1
    r.timeline = []
    assert render.timeline_chart(r).empty


def test_heat_points_weights_and_cap():
    assert len(render.heat_points(_result())) == 3            # 6 in+ only: 0.2, 0.4, 1.0 m
    pts = render.heat_points(_result(), min_depth_m=0.03)
    assert len(pts) == 4                      # 4 wet cells >= 3 cm; NaN and 1 cm cells skipped
    w = sorted(p[2] for p in pts)
    assert w[-1] == 1.0 and 0 < w[0] < 0.1    # 1 m capped at full heat, 5 cm faint
    top = render.heat_points(_result(), max_points=2)
    assert len(top) == 2 and min(p[2] for p in top) >= 0.4 / render.HEAT_FULL_M - 1e-3  # deepest kept


def test_flood_map_heatmap_layer():
    html = render.flood_map(_scenario(), show_heat=True).get_root().render()
    assert "L.heatLayer" in html and "Flood heat" in html and "L.imageOverlay" in html
    html = render.flood_map(_scenario(), show_heat=True, show_depth=False).get_root().render()
    assert "L.heatLayer" in html and "L.imageOverlay" not in html
    assert "L.heatLayer" not in render.flood_map(_scenario()).get_root().render()
