"""Draw a flood result: depth PNG, folium map, and chart-ready frames."""
import base64
import io

import folium
from folium.plugins import HeatMap
import numpy as np
import pandas as pd
from PIL import Image

from src.flood import MM_PER_IN
from src.maps import BASEMAPS, FONT_LINK, TIP_CSS, TOOLTIP_RESET, add_basemap

KM2_TO_ACRES = 247.105
SF_FIT = [[37.708, -122.515], [37.812, -122.357]]
OVERWHELMED_COLOR = "#D93025"
MAX_INLET_DOTS = 3000
MAX_HEAT_POINTS = 20000
HEAT_FULL_M = 0.6  # water this deep (2 ft) or more shows at full heat
HEAT_MIN_M = 0.15  # only "flooded" water (6 in+) heats up; shallow sheet flow everywhere would wash it out

# (min depth m, max depth m, color, label). Water blues like src.maps.RISK_BINS, darker = deeper.
DEPTH_CLASSES = [
    (0.03, 0.15, "#9FD3F0", "1–6 in"),
    (0.15, 0.30, "#4FA8DE", "6–12 in"),
    (0.30, 0.60, "#1F66B8", "1–2 ft"),
    (0.60, np.inf, "#0B2A5E", "Over 2 ft"),
]
# Same water blues for the night map, flipped so deeper = brighter (dark water on a dark map would disappear).
DEPTH_CLASSES_DARK = [
    (0.03, 0.15, "#1D4ED8", "1–6 in"),
    (0.15, 0.30, "#3B82F6", "6–12 in"),
    (0.30, 0.60, "#7DD3FC", "1–2 ft"),
    (0.60, np.inf, "#E0F2FE", "Over 2 ft"),
]
HEAT_GRADIENT_DARK = {0.2: "#1E3A8A", 0.45: "#2563EB", 0.7: "#38BDF8", 1.0: "#E0F2FE"}
ALPHA = 0.75


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def depth_rgba(depth_m, min_depth_m=0.03, classes=None):
    """(rows, cols) depth array -> (rows, cols, 4) uint8 RGBA, transparent where dry or NaN."""
    d = np.nan_to_num(np.asarray(depth_m, float), nan=0.0)
    out = np.zeros(d.shape + (4,), np.uint8)
    a = int(round(ALPHA * 255))
    for i, (lo, hi, color, _) in enumerate(classes or DEPTH_CLASSES):
        lo = max(lo, min_depth_m) if i == 0 else lo
        mask = (d >= lo) & (d < hi)
        out[mask] = (*_rgb(color), a)
    return out


def depth_png(result, min_depth_m=0.03, classes=None):
    """RGBA PNG bytes of result.depth_m, one pixel per grid cell, row 0 = north (image top)."""
    buf = io.BytesIO()
    Image.fromarray(depth_rgba(result.depth_m, min_depth_m, classes), "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _legend_html(show_depth=True, show_heat=False, dark=False):
    rows = "" if not show_depth else "".join(
        f'<div><span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:{c};'
        f'opacity:{ALPHA};margin-right:6px"></span>{label}</div>'
        for _, _, c, label in (DEPTH_CLASSES_DARK if dark else DEPTH_CLASSES))
    if show_heat:
        rows += ('<div style="margin-top:3px"><span style="display:inline-block;width:46px;height:8px;'
                 'border-radius:4px;background:linear-gradient(90deg,' + (",".join(HEAT_GRADIENT_DARK.values()) if dark else "blue,cyan,lime,yellow,red") + ');'
                 'margin-right:6px"></span>Flood heat</div>')
    rows += (f'<div style="margin-top:3px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
             f'background:{OVERWHELMED_COLOR};margin:0 8px 1px 2px"></span>Drain overwhelmed</div>')
    bg, fg, line = ('#1A1A1D', '#EDEDED', '#2E2E33') if dark else ('white', '#1B1F24', '#ccc')
    return (f'<div style="position:absolute;bottom:24px;left:24px;z-index:9999;background:{bg};color:{fg};'
            f'padding:8px 10px;border:1px solid {line};border-radius:8px;font:12px Roboto Flex,sans-serif">'
            f'<div style="font-weight:600;margin-bottom:3px">Water depth</div>{rows}</div>')


def heat_points(result, min_depth_m=HEAT_MIN_M, max_points=MAX_HEAT_POINTS):
    """[[lat, lon, weight], ...] for every cell with 6 in+ of water; weight = depth / 2 ft, capped at 1 (deepest kept if too many)."""
    d = np.nan_to_num(np.asarray(result.depth_m, float), nan=0.0)
    r, c = np.nonzero(d >= min_depth_m)
    if not len(r):
        return []
    w = np.minimum(d[r, c] / HEAT_FULL_M, 1.0)
    if len(r) > max_points:
        keep = np.argsort(-d[r, c], kind="stable")[:max_points]
        r, c, w = r[keep], c[keep], w[keep]
    lat, lon = result.grid.latlon(r, c)
    return np.c_[np.round(lat, 5), np.round(lon, 5), np.round(w, 3)].tolist()


def flood_map(scenario, basemap="Dark", show_hotspots=True, show_overwhelmed=True, height=560,
              show_heat=False, show_depth=True):
    """Folium map: basemap + depth overlay and/or flood heatmap + hotspot circles + overwhelmed inlets + legend."""
    result = scenario.result
    m = folium.Map(location=(37.758, -122.44), zoom_start=12, tiles=None, prefer_canvas=True, max_zoom=19,
                   width="100%", height=f"{height}px")
    m.get_root().header.add_child(folium.Element(FONT_LINK + TOOLTIP_RESET))
    dark = basemap == "Dark"
    add_basemap(m, basemap)
    if basemap == "Terrain shading (USGS)":  # faint streets so people can find their block
        folium.TileLayer(tiles=BASEMAPS["Streets"]["tiles"], attr=BASEMAPS["Streets"]["attr"],
                         name="Streets", opacity=0.35, max_zoom=19, max_native_zoom=19).add_to(m)

    if show_depth:
        uri = "data:image/png;base64," + base64.b64encode(depth_png(result, classes=DEPTH_CLASSES_DARK if dark else None)).decode()
        # Lat/lon grid stretched linearly in Web Mercator: < 5 m error over SF, so no reprojection needed.
        folium.raster_layers.ImageOverlay(uri, bounds=result.grid.bounds(), name="Flood depth", opacity=1.0,
                                          interactive=False, pixelated=True).add_to(m)

    if show_heat:  # same style as the Command Center risk heatmap: hot = more and deeper water nearby
        pts = heat_points(result)
        if pts:
            HeatMap(pts, radius=22, blur=18, min_opacity=0.3, max_zoom=14, name="Flood heatmap",
                    **({"gradient": HEAT_GRADIENT_DARK} if dark else {})).add_to(m)

    inlets = getattr(scenario, "inlets", None)
    if show_overwhelmed and inlets is not None and len(inlets) and "overwhelmed" in inlets:
        over = inlets[inlets["overwhelmed"].astype(bool)]
        if len(over) > MAX_INLET_DOTS:
            over = over.sample(MAX_INLET_DOTS, random_state=0)
        feats = [{"type": "Feature", "properties": {},
                  "geometry": {"type": "Point", "coordinates": [round(float(lo), 5), round(float(la), 5)]}}
                 for la, lo in zip(over["lat"], over["lon"])]
        if feats:
            folium.GeoJson({"type": "FeatureCollection", "features": feats}, name="Overwhelmed drains",
                           marker=folium.CircleMarker(radius=1.6, weight=0, fill=True, fill_color=OVERWHELMED_COLOR,
                                                      fill_opacity=0.6)).add_to(m)

    hotspots = getattr(scenario, "hotspots", None)
    if show_hotspots and hotspots is not None and len(hotspots):
        fg = folium.FeatureGroup(name="Deepest spots")
        for r in hotspots.itertuples():
            depth_in = getattr(r, "max_depth_in", None)
            if depth_in is None:
                depth_in = float(r.max_depth_m) * 1000 / MM_PER_IN
            acres = float(getattr(r, "area_acres", 0.0) or 0.0)
            hood = getattr(r, "neighborhood", "") or "San Francisco"
            tip = (f'<div style="opacity:.7;font-size:12px">{hood}</div>'
                   f'<div style="font-size:14px">Up to <b>{depth_in:.0f} in</b> of water</div>'
                   f'<div style="opacity:.6;font-size:12px">About {acres:.1f} acres ponded</div>')
            folium.CircleMarker((float(r.lat), float(r.lon)), radius=7 + min(8.0, acres ** 0.5 * 1.5),
                                color="#FFFFFF", weight=2, fill=True, fill_color="#7DD3FC" if dark else "#0B2A5E", fill_opacity=0.85,
                                tooltip=folium.Tooltip(tip, style=TIP_CSS, sticky=True)).add_to(fg)
        fg.add_to(m)

    m.get_root().html.add_child(folium.Element(_legend_html(show_depth, show_heat, dark)))
    m.fit_bounds(SF_FIT)
    return m


def hyetograph_chart(rain):
    """DataFrame indexed by elapsed time label with one column 'Rain (in/h)' for st.bar_chart."""
    rate = np.asarray(rain.intensity_mm_hr, float) / MM_PER_IN
    t_hr = np.arange(len(rate)) * float(rain.step_min) / 60.0
    labels = [f"{int(t)}:{int(round((t % 1) * 60)):02d}" for t in t_hr]
    df = pd.DataFrame({"Time": labels, "Rain (in/h)": np.round(rate, 3)})
    df["Time"] = pd.Categorical(df["Time"], categories=labels, ordered=True)  # keep time order on the axis
    return df.set_index("Time")


def timeline_chart(result):
    """DataFrame indexed by 'Hours' with 'Flooded acres' for st.area_chart (empty frame if no timeline)."""
    tl = pd.DataFrame(result.timeline or [])
    if tl.empty or "t_hr" not in tl:
        return pd.DataFrame({"Flooded acres": []}, index=pd.Index([], name="Hours"))
    acres = tl["flooded_km2"].to_numpy(float) * KM2_TO_ACRES if "flooded_km2" in tl else np.zeros(len(tl))
    return pd.DataFrame({"Flooded acres": np.round(acres, 1)},
                        index=pd.Index(tl["t_hr"].to_numpy(float).round(2), name="Hours"))
