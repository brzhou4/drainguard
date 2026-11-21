"""Folium map builders."""
import json

import folium
from folium.plugins import HeatMap

from src.data import CHECKED_COLOR, OTHER_DRAIN_COLOR, PROCESSED, TIER_COLORS

SF_CENTER = (37.758, -122.44)


def legend_html():
    items = "".join(
        f'<div><span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
        f'background:{c};margin-right:6px"></span>{t.upper()}</div>'
        for t, c in TIER_COLORS.items()
    )
    items += (f'<div><span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
              f'background:{CHECKED_COLOR};margin-right:6px"></span>CHECKED</div>'
              f'<div><span style="display:inline-block;width:6px;height:6px;border-radius:50%;'
              f'background:{OTHER_DRAIN_COLOR};margin:0 9px 2px 3px"></span>OTHER DRAINS</div>')
    return ('<div style="position:fixed;bottom:24px;left:24px;z-index:9999;background:white;'
            'padding:8px 10px;border:1px solid #ccc;border-radius:6px;font:12px sans-serif">'
            f'{items}</div>')


def drain_map(drains, show_heat=True, show_zone=False, show_nov18=False, center=None, zoom=12, you=None,
              radius_m=None, focus=None, all_drains=None):
    m = folium.Map(location=center or SF_CENTER, zoom_start=zoom, tiles="OpenStreetMap", prefer_canvas=True)

    # Every mapped drain as a small gray dot (one compact GeoJSON layer so 24k points stay fast).
    if all_drains is not None and len(all_drains):
        feats = [{"type": "Feature", "properties": {},
                  "geometry": {"type": "Point", "coordinates": [round(lo, 5), round(la, 5)]}}
                 for la, lo in zip(all_drains["lat"], all_drains["lon"])]
        folium.GeoJson({"type": "FeatureCollection", "features": feats}, name="All drains",
                       marker=folium.CircleMarker(radius=2, weight=0, fill=True,
                                                  fill_color=OTHER_DRAIN_COLOR, fill_opacity=0.7)).add_to(m)

    if show_zone:
        zone = PROCESSED / "flood_zone.geojson"
        if zone.exists():
            folium.GeoJson(json.loads(zone.read_text()), name="SFPUC 100-year flood zone",
                           style_function=lambda _: {"color": "#3B6EA8", "weight": 1, "fillOpacity": 0.15}).add_to(m)

    if show_nov18:
        inc = PROCESSED / "nov18_incidents.csv"
        if inc.exists():
            import pandas as pd
            fg = folium.FeatureGroup(name="Nov 18 flood reports")
            for _, r in pd.read_csv(inc).iterrows():
                folium.CircleMarker((r["lat"], r["lon"]), radius=2, color="#1F3A5F", fill=True,
                                    fill_opacity=0.6, weight=0).add_to(fg)
            fg.add_to(m)

    if show_heat and len(drains):
        HeatMap(drains[["lat", "lon", "risk"]].values.tolist(), radius=25, blur=20, name="Risk").add_to(m)

    for _, r in drains.iterrows():
        checked = r.get("status", "Not checked") != "Not checked"
        color = CHECKED_COLOR if checked else TIER_COLORS.get(r["tier"], OTHER_DRAIN_COLOR)
        folium.CircleMarker(
            (r["lat"], r["lon"]), radius={"Urgent": 9, "High": 7}.get(r["tier"], 5),
            color="white", weight=1, fill=True, fill_color=color, fill_opacity=0.95,
            tooltip=f"#{int(r['rank'])} {r['tier'].upper()} · {r['neighborhood']}",
            popup=folium.Popup(f"<b>#{int(r['rank'])} {r['tier']}</b><br>{r['asset_id']}<br>"
                               f"{r['reason']}<br>Status: {r.get('status', 'Not checked')}", max_width=260),
        ).add_to(m)

    if focus is not None:
        hit = drains[drains["asset_id"] == focus]
        if len(hit):
            r = hit.iloc[0]
            folium.Marker((r["lat"], r["lon"]), tooltip=f"Selected: #{int(r['rank'])}",
                          icon=folium.Icon(color="black", icon="star")).add_to(m)

    if you:
        folium.Marker(you, tooltip="You", icon=folium.Icon(color="blue", icon="user")).add_to(m)
        if radius_m:
            folium.Circle(you, radius=radius_m, color="#1F3A5F", weight=1, fill=False).add_to(m)

    m.get_root().html.add_child(folium.Element(legend_html()))
    return m
