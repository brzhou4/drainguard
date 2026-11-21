"""Flood Forecast: citywide SF311 street-flood report score in three-hour windows."""
import json
from datetime import datetime, timezone

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src import forecast as fc
from src.maps import BASEMAPS, SF_CENTER

st.title("Flood Forecast")
st.caption("Rain in the next 24 hours, in three-hour windows, with a **citywide SF311 street-flood report "
           "score**. The score is about when 311 flood reports are likely across the city. It is not a "
           "street-level flood probability or an official warning. To pick which drains to check, use the "
           "historical ranking on the Command Center.")
st.page_link("views/command_center.py", label="Drains to check first: Command Center", icon=":material/dashboard:")

LIVE, REPLAY = "Live forecast", "Archived replay: Nov 18–19 storm"
mode = st.segmented_control("Data", [LIVE, REPLAY], default=LIVE, key="fc_mode") or LIVE


@st.cache_data(ttl=15 * 60, show_spinner="Getting the Open-Meteo forecast...")
def _live():
    return fc.fetch_live()  # raises on failure, and Streamlit does not cache exceptions


def _mm(v):
    return "Unavailable" if v is None else f"{v:.1f} mm"


def _table(rows):
    out = pd.DataFrame([{
        "Window (Pacific)": fc.window_label(r),
        "Central point rain": _mm(r["central_mm"]),
        "Wettest of 5 points": _mm(r["max_point_mm"]) + (
            f" ({fc.POINT_NAMES[r['max_point']]})" if r["max_point"] not in (None, 0) else ""),
        "Report score": None if r["score"] is None else round(100 * r["score"]),
    } for r in rows])
    st.dataframe(out, hide_index=True, use_container_width=True, column_config={
        "Report score": st.column_config.ProgressColumn(
            "Citywide SF311 report score", min_value=0, max_value=100, format="%d",
            help="Score from 0 to 100 from a model trained on archived day-ahead forecasts. It ranks "
                 "windows by how likely citywide SF311 street-flood reports are. Not a calibrated probability. "
                 "Blank = a required rain hour is missing."),
    })
    if any(r["score"] is None for r in rows):
        st.caption("Blank score or 'Unavailable' total: the forecast is missing at least one hour that "
                   "window needs, so no number is shown.")


data = None
if mode == LIVE:
    c1, c2 = st.columns([1, 3])
    if c1.button("Refresh forecast", icon=":material/refresh:"):
        _live.clear()
    try:
        data = _live()
    except Exception as e:
        st.error("**Forecast unavailable.** Could not reach the Open-Meteo forecast service, so there are no "
                 "live windows or scores right now. Try *Refresh forecast* in a few minutes, or switch to "
                 f"the archived replay. ({type(e).__name__})", icon=":material/cloud_off:")
    if data:
        start = fc.next_boundary(datetime.now(timezone.utc))
        c2.caption(f"Live Open-Meteo forecast (HRRR/GFS blend) · retrieved "
                   f"{fc.pacific(data['retrieved_at'])} Pacific · refreshes every 15 min")
        rows = fc.windows(data["series"], start)
else:
    data = fc.load_replay()
    if data is None:
        st.error("**Replay unavailable.** The archived forecast fixture is missing. Run "
                 "`python scripts/fetch_replay_fixture.py` to rebuild it.", icon=":material/cloud_off:")
    else:
        start = data["replay_start"]
        st.info("**Archived replay, not live.** This is the forecast issued one day ahead for the Nov 18–19 "
                "storm (Open-Meteo previous-runs archive, packaged in this repo on "
                f"{fc.pacific(data['retrieved_at'], '%b %-d, %Y')}).", icon=":material/history:")
        rows = fc.windows(data["series"], start)

if data:
    st.subheader("Next 8 windows" if mode == LIVE else "Eight windows from Wed Nov 18, 4 PM Pacific")
    _table(rows)
    with st.expander("What these numbers mean"):
        st.markdown(f"""
- **Window rain** adds the three hourly forecast values that end inside the window. Open-Meteo's value
  at a given hour is the rain that fell in the hour *ending* then.
- **Central point rain** is at {fc.POINTS[0][0]}, {fc.POINTS[0][1]} (Mission). **Wettest of 5 points**
  is the largest window total across five points spread over the city.
- **Citywide SF311 report score** uses only the central point. Inputs are the window's rain and its
  wettest hour, rain in the 6 and 24 hours before, time of day and season. The model was trained on
  archived day-ahead forecasts against citywide 311 flood reports. After a storm, rain in the last 24 hours
  keeps the score high even when the window is dry, because reports keep coming in.
- The score is **not** a street-level flood probability, a drain overflow prediction or an official
  warning. For official alerts see the National Weather Service (weather.gov/mtr).
""")

    # ---------- Map: forecast points + (replay only) modeled ponding ----------
    show_ponding = False
    if mode == REPLAY:
        if fc.PONDING_GEOJSON.exists():
            show_ponding = st.toggle("24-hour modeled ponding scenario", value=False,
                                     help="Terrain-only scenario for Nov 19 rain. Runoff assumption is uncalibrated.")
        else:
            st.caption("The modeled ponding layer is not built yet, so it is not shown. "
                       "Build it with `python scripts/build_ponding.py`.")
    m = folium.Map(location=SF_CENTER, zoom_start=12, tiles=None)
    b = BASEMAPS["Streets"]
    folium.TileLayer(tiles=b["tiles"], attr=b["attr"], max_native_zoom=b["max_native_zoom"], max_zoom=19).add_to(m)
    if show_ponding:
        gj = json.loads(fc.PONDING_GEOJSON.read_text())
        fg = folium.FeatureGroup(name="24-hour modeled ponding scenario")
        for f in gj["features"]:
            lon, lat = f["geometry"]["coordinates"]
            d = f["properties"]["scenario_depth_m"]
            folium.CircleMarker((lat, lon), radius=2 + min(d, 1.0) * 3, weight=0, fill=True,
                                fill_color="#1F66B8", fill_opacity=0.55).add_to(fg)
        fg.add_to(m)
    for i, (la, lo) in enumerate(fc.POINTS):
        folium.CircleMarker((la, lo), radius=7, color="white", weight=2, fill=True,
                            fill_color="#1F3A5F" if i == 0 else "#5F6B7A", fill_opacity=0.95,
                            popup=folium.Popup(f"<b>{fc.POINT_NAMES[i]}</b><br>{la}, {lo}", max_width=200)).add_to(m)
    st_folium(m, key=f"fc-map-{mode}-{show_ponding}", height=460, use_container_width=True, returned_objects=[])
    st.caption("Dark dot: central forecast point (used for the score). Gray dots: the other four forecast points.")
    if show_ponding:
        summary = json.loads(fc.PONDING_SUMMARY.read_text()) if fc.PONDING_SUMMARY.exists() else {}
        st.caption(
            f"**24-hour modeled ponding scenario.** Blue dots: 100 m street cells where a terrain-only model "
            f"holds at least 0.10 m of water after Nov 19 forecast rain ({len(gj['features'])} cells). "
            f"Assumes {int(100 * summary.get('runoff_fraction', 0.5))}% of rain becomes runoff (uncalibrated). "
            "It does not route spill downstream or model inlet capture, pipe capacity or confirmed water depth. "
            "This is a scenario, not observed flooding.")
