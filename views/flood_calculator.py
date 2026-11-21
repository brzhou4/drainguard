"""Flood Calculator: where would water pond across SF for a given storm?"""
import datetime as dt
import json
import math

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src.flood.validate import validation_panel
from src.maps import BASEMAPS

SHAPES = {"Typical Bay Area storm": "scs1", "Steady rain": "uniform", "Heavy start": "front",
          "Sudden downpour": "burst"}
GRIDS = {"30 m (more detail)": 30.0, "60 m (faster)": 60.0}


# ---------- cached data / model ----------
@st.cache_data(ttl=1800, show_spinner="Checking today's weather...")
def _today():
    from src.flood import weather_input
    return weather_input.from_today()


@st.cache_data(show_spinner="Looking up rain for that day...")
def _on_date(d):
    from src.flood import weather_input
    return weather_input.from_date(d)


@st.cache_data(show_spinner=False)
def _manual(depth_in, duration_hr, shape):
    from src.flood import weather_input
    return weather_input.from_manual(depth_in, duration_hr, shape)


@st.cache_data(ttl=86400, show_spinner=False)
def _storms():
    from src.flood import weather_input
    return weather_input.notable_storms(n=10)


@st.cache_resource
def _runs():
    """Process-wide memo of finished scenarios (a plain dict so the progress bar can update live)."""
    return {}


def _run(rain, cell_m, clog_pct, runoff, progress=None):
    """calc.run memoised on the RainEvent's fields (the dataclass itself isn't hashable)."""
    from src.flood import calc
    key = (float(rain.depth_in), float(rain.duration_hr),
           tuple(np.round(np.asarray(rain.intensity_mm_hr, float), 4).tolist()), float(rain.step_min),
           rain.source, rain.label, rain.date, float(cell_m), float(clog_pct), float(runoff))
    memo = _runs()
    if key not in memo:
        if len(memo) >= 8:
            memo.pop(next(iter(memo)))
        memo[key] = calc.run(rain, cell_m=cell_m, clog_pct=clog_pct, runoff_coeff=runoff, progress=progress)
    return memo[key]


def _how_common(rp):
    if rp is None or (isinstance(rp, float) and (math.isnan(rp))):
        return "n/a"
    if math.isinf(rp):
        return "Over 1,000 yrs"
    if rp < 1:
        return "Several times a year"
    return f"1 in {rp:,.0f} yrs" if rp >= 2 else "About yearly"


# ---------- page ----------
st.title("Flood Calculator")
st.write("Estimates where water would pond across San Francisco for a storm, using the city's elevation, "
         "every mapped storm drain's flow capacity, and NOAA rain statistics.")

mode = st.segmented_control("Rain to model", ["Today's weather", "Past storm", "Enter rain yourself"],
                            default="Enter rain yourself", required=True, key="fc_mode") or "Enter rain yourself"

rain, rain_err = None, None
try:
    if mode == "Today's weather":
        rain = _today()
        st.caption(rain.label or "Today in San Francisco")
        if not rain.depth_in or rain.depth_in <= 0:
            st.info("No rain expected or measured in San Francisco today, so nothing would flood. "
                    "Try a past storm or enter rain yourself.", icon=":material/sunny:")
    elif mode == "Past storm":
        c1, c2 = st.columns([2, 1])
        try:
            storms = _storms()
        except Exception as e:
            storms = pd.DataFrame(columns=["date", "prcp_in"])
            st.warning(f"Couldn't load the list of big past storms ({e}). You can still pick a date.")
        opts = {f"{pd.Timestamp(r.date):%b %d, %Y} · {r.prcp_in:.2f} in": pd.Timestamp(r.date).date()
                for r in storms.itertuples()}
        pick = c1.selectbox("Biggest rain days on record (SF Downtown)", list(opts) or ["(none loaded)"],
                            key="fc_storm")
        any_day = c2.date_input("…or any date", value=None, min_value=dt.date(1900, 1, 1),
                                max_value=dt.date.today(), key="fc_date",
                                help="Uses that day's NOAA rain total at SF Downtown")
        day = any_day or opts.get(pick)
        if day is not None:
            rain = _on_date(day)
            st.caption(rain.label)
            if not rain.depth_in or rain.depth_in <= 0:
                st.info("No rain was recorded in San Francisco that day. Try another date.")
    else:
        c1, c2, c3 = st.columns(3)
        depth = c1.number_input("Total rain (inches)", 0.1, 20.0, 2.0, 0.1, key="fc_depth")
        hours = c2.number_input("Over how many hours", 0.5, 72.0, 6.0, 0.5, key="fc_hours")
        shape = c3.selectbox("How it falls", list(SHAPES), key="fc_shape",
                             help="Typical Bay Area storms build to a peak in the middle; "
                                  "a sudden downpour drops most of the rain in a short burst.")
        rain = _manual(float(depth), float(hours), SHAPES[shape])
except Exception as e:
    rain_err = e
    st.error(f"Couldn't get rain data: {e}", icon=":material/error:")

with st.expander("Model settings"):
    s1, s2, s3 = st.columns(3)
    clog = s1.slider("Blocked drains (%)", 0, 75, 0, 5, key="fc_clog",
                     help="Share of drain capacity lost to leaves and trash. Fall storms often start with many "
                          "drains partly blocked.")
    runoff = s2.slider("Share of rain that runs off", 0.50, 0.95, 0.85, 0.05, key="fc_runoff",
                       help="SF is mostly pavement and roofs, so most rain runs off instead of soaking in.")
    cell_label = s3.selectbox("Grid resolution", list(GRIDS), key="fc_grid")
    cell_m = GRIDS[cell_label]

go = st.button("Run flood map", type="primary", icon=":material/flood:", disabled=rain is None)

if go and rain is not None:
    bar = st.progress(0.0, text="Starting...")

    def _progress(*args, **kwargs):
        frac = args[0] if args else kwargs.get("frac", 0.0)
        text = args[1] if len(args) > 1 else kwargs.get("text", kwargs.get("msg", "Simulating water flow..."))
        try:
            bar.progress(min(1.0, max(0.0, float(frac))), text=str(text))
        except Exception:
            pass

    try:
        with st.spinner("Simulating water flowing over the city..."):
            scn = _run(rain, cell_m, float(clog), float(runoff), progress=_progress)
        st.session_state["fc_scn"] = scn
    except Exception as e:
        st.error(f"The flood model couldn't run: {e}", icon=":material/error:")
    bar.empty()

scn = st.session_state.get("fc_scn")
if scn is None:
    st.caption("Pick the rain, then press **Run flood map**. The first run downloads elevation and drain data "
               "and can take a minute; later runs are faster.")
else:
    from src.flood import calc, render

    s, res, comm = scn.summary, scn.result, scn.commonality
    st.subheader(res.rain.label or "Your storm")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Rain", f"{s.get('rain_in', 0):.2f} in", help=f"Over {s.get('duration_hr', 0):g} hours, "
              f"peak {s.get('peak_in_hr', 0):.2f} in/h")
    k2.metric("How common", _how_common(s.get("return_period_yr")))
    chance = s.get("annual_chance_pct")
    if chance is not None and not (isinstance(chance, float) and math.isnan(chance)):
        k2.caption(f"{chance:.0f}% chance in any year" if chance >= 1 else f"{chance:.1f}% chance in any year")
    k3.metric("Flooded acres", f"{s.get('flooded_acres', 0):,.0f}", help="Ground under 6 in of water or more")
    k3.caption("6 in deep or more")
    deep_in = float(s.get("max_depth_in", 0) or 0)
    k4.metric("Deepest water", f"{deep_in:.0f} in" if deep_in < 36 else f"{deep_in / 12:.0f} ft",
              help="Deepest spot is usually a low pocket in the terrain grid (pit, reservoir, construction site)")
    k5.metric("Drains overwhelmed", f"{s.get('inlets_overwhelmed', 0):,}",
              help="Drains that got more water than they were built to take")
    k5.caption(f"of {s.get('inlets_total', 0):,} mapped drains")
    if s.get("sentence") or comm.sentence:
        st.info(s.get("sentence") or comm.sentence, icon=":material/water_drop:")
    for note in res.rain.notes or []:
        st.caption(note)

    map_col, side_col = st.columns([2, 1])
    with map_col:
        b1, b2, b3 = st.columns([2, 1, 1])
        basemap = b1.selectbox("Map style", list(BASEMAPS), index=list(BASEMAPS).index("Terrain shading (USGS)"),
                               key="fc_basemap")
        show_hot = b2.toggle("Deepest spots", value=True, key="fc_hot")
        show_over = b3.toggle("Full drains", value=False, key="fc_over",
                              help="Dark dots: drains that got more water than they can take")
        try:
            st_folium(render.flood_map(scn, basemap=basemap, show_hotspots=show_hot, show_overwhelmed=show_over),
                      key="flood-map", height=560, use_container_width=True, returned_objects=[])
        except Exception as e:
            st.error(f"Couldn't draw the map: {e}")
    with side_col:
        st.markdown("**Rain over time** (in/h)")
        st.bar_chart(render.hyetograph_chart(res.rain), height=200, color="#3A9AD9")
        tl = render.timeline_chart(res)
        if len(tl):
            st.markdown("**Flooded area over time** (acres, 6+ in deep)")
            st.area_chart(tl, height=200, color="#1F66B8")
        worst = s.get("worst_neighborhoods") or []
        if worst:
            st.markdown("**Most affected neighborhoods**")
            st.write(", ".join(str(w.get("neighborhood", w)) if isinstance(w, dict) else str(w) for w in worst))

    st.subheader("Deepest spots")
    hot = scn.hotspots
    if hot is None or hot.empty:
        st.write("No spots with 6 inches of water or more in this storm.")
    else:
        t = hot.copy()
        t["map"] = [f"https://www.google.com/maps?q={la:.5f},{lo:.5f}" for la, lo in zip(t["lat"], t["lon"])]
        cols = [c for c in ["neighborhood", "max_depth_in", "area_acres", "lat", "lon", "map"] if c in t]
        st.dataframe(t[cols], hide_index=True, width="stretch", column_config={
            "neighborhood": "Neighborhood",
            "max_depth_in": st.column_config.NumberColumn("Deepest (in)", format="%.0f"),
            "area_acres": st.column_config.NumberColumn("Area (acres)", format="%.1f"),
            "lat": st.column_config.NumberColumn("Lat", format="%.5f"),
            "lon": st.column_config.NumberColumn("Lon", format="%.5f"),
            "map": st.column_config.LinkColumn("Map", display_text="Open in Maps"),
        })

    tag = (res.rain.date or "custom").replace("-", "")
    d1, d2, d3 = st.columns(3)
    try:
        d1.download_button("Flood map (GeoJSON)", json.dumps(calc.geojson(scn)).encode(),
                           f"sf_flood_{tag}.geojson", "application/geo+json", width="stretch")
    except Exception as e:
        d1.caption(f"GeoJSON export failed: {e}")
    if hot is not None and len(hot):
        d2.download_button("Deepest spots (CSV)", hot.to_csv(index=False).encode(), f"sf_flood_hotspots_{tag}.csv",
                           "text/csv", width="stretch")
    d3.download_button("Depth image (PNG)", render.depth_png(res), f"sf_flood_depth_{tag}.png", "image/png",
                       width="stretch",
                       help=f"One pixel per grid cell, north up. Bounds [[S, W], [N, E]]: {res.grid.bounds()}")

try:
    validation_panel()
except Exception as e:
    st.caption(f"Validation results unavailable: {e}")

with st.expander("How this works & limits"):
    st.markdown(
        "**Method**\n"
        "1. **Rain**: today's NWS observations/forecast, a NOAA daily total for a past date, or your numbers, "
        "spread over time in a storm shape (hyetograph).\n"
        "2. **Ground**: a 30 m (or 60 m) elevation grid of San Francisco from public terrain tiles (USGS 3DEP).\n"
        "3. **Drains**: every mapped SF storm drain inlet gets a design flow rate; blocked drains lower it.\n"
        "4. **Water flow**: rain that runs off flows downhill cell by cell; drains take what they can and the rest "
        "ponds in low spots or flows to the bay and ocean.\n"
        "5. **How common**: the rain total for that duration is compared with NOAA Atlas 14 rain-frequency tables "
        "for San Francisco and the city's daily rain record.\n\n"
        "**Limits**\n"
        "- Pipe capacity is simplified: the city doesn't publish its sewer pipe network, so each drain gets a "
        "typical inlet capacity and pipes are assumed to keep up.\n"
        "- A 30 m elevation grid misses curbs, gutters, and small dips, so street-level depths are rough.\n"
        "- No tides, storm surge, or sea-level rise.\n"
        "- One rain gauge stands in for the whole city; real storms vary block to block.\n"
        "- For planning and education, **not emergency decisions**. In a storm, follow "
        "[SF72](https://www.sf72.org) and the [National Weather Service](https://www.weather.gov/mtr/).")
