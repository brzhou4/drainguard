"""Command Center: the city crew lead's view (laptop)."""
import json

import streamlit as st
from streamlit_folium import st_folium

from src.alerts import send_push
from src.data import (DONE_STATUSES, RESULTS_JSON, STATUSES, TIERS, all_drains, check_cutoff, check_ttl_days,
                      data_source, drains_with_status, load_volunteers, log_status, neighborhoods_geojson, now_str,
                      open_for_volunteers, round_start, setting, start_new_round, visible_on_maps)
from src.store import backend
from src.geo import nearest_drains
from src.maps import BASEMAPS, drain_map
from src.weather import current_storm

CITY_TOPIC = setting("CITY_TOPIC", "drainguard-city-demo")
APP_URL = setting("APP_URL", "")

drains = drains_with_status()

# Map-first layout (inspired by electionmapsf.com): drain list on the left, the map in the middle with the
# storm alert on top of it, and the city-wide numbers on the right.
st.markdown("""<style>
.block-container{padding-top:3.2rem;max-width:100%}
.dg-panel-title{font-family:'Roboto Slab',serif;font-weight:600;font-size:22px;margin:0 0 2px}
.dg-big{font-size:40px!important;font-weight:600;line-height:1.1!important;margin:2px 0 8px!important}
.dg-label{font-size:14px!important;font-weight:600;margin:14px 0 0!important;color:#EDEDED}
.dg-muted{font-size:13px!important;color:#A1A1AA;margin:2px 0!important;line-height:1.5}
.dg-bar{height:10px;border-radius:5px;background:#2E2E33;overflow:hidden;margin:4px 0 6px}
.dg-bar span{display:block;height:100%;background:#4EA1F3;border-radius:5px}
</style>""", unsafe_allow_html=True)

# ---------- Storm alert (sits on top of the map) ----------
storm = current_storm()  # NWS forecast for the banner (STORM_OVERRIDE can force it for demos)
ACCENT, LABEL = {  # accent color, label. Weather-warning colors: no purple.
    "storm": ("#F5A524", "STORM ALERT"),
    "watch": ("#FB923C", "RAIN WATCH"),
    "clear": ("#22C55E", "ALL CLEAR"),
    "unknown": ("#A1A1AA", "FORECAST"),
}[storm["level"]]
unchecked = int(((drains["tier"] == "Urgent") & ~drains["status"].isin(DONE_STATUSES)).sum())
action = (f"{unchecked} Urgent drains still need checking before the rain." if storm["level"] in ("storm", "watch")
          else f"{unchecked} Urgent drains not yet checked.")
alert_html = f"""<div role="status" style="background:#1A1A1D;border:1px solid #2E2E33;border-left:5px solid {ACCENT};
  border-radius:12px;padding:12px 16px;margin:0 0 10px">
  <div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px">
    <span style="color:{ACCENT};font-size:12px;font-weight:700;letter-spacing:.12em">{LABEL} · SAN FRANCISCO</span>
    <span style="font-family:'Roboto Slab',serif;font-size:21px;font-weight:600;color:#FFFFFF">{storm['headline']}</span>
    <span style="font-size:15px;font-weight:600;color:{ACCENT}">{action}</span>
  </div>
  <details style="margin-top:4px;font-size:13px;color:#A1A1AA"><summary style="cursor:pointer">Forecast details</summary>
  {storm['detail']}<br>Source: National Weather Service forecast, updated every 30 min.</details>
</div>"""

# ---------- Shared numbers ----------
urgent = drains[drains["tier"] == "Urgent"]
checked_urgent = int(urgent["status"].isin(DONE_STATUSES).sum())
result_text, summary = "pending", None
if RESULTS_JSON.exists():
    try:
        summary = json.loads(RESULTS_JSON.read_text())
        result_text = summary.get("headline", "pending")
    except Exception:
        pass
source = data_source()

left, center_col, right = st.columns([1.3, 2.75, 1.0], gap="medium")

# ---------- Left: filters + urgency list (like the race list) ----------
with left:
    st.markdown('<p class="dg-panel-title">Drains to check</p>', unsafe_allow_html=True)
    st.markdown('<p class="dg-muted">Ranked by how likely each is to need checking</p>', unsafe_allow_html=True)
    tier_pick = st.pills("Tier", TIERS, selection_mode="multi", default=["Urgent", "High"], key="f_tier")
    hoods = ["All neighborhoods"] + sorted(drains["neighborhood"].dropna().unique())
    hood_pick = st.selectbox("Neighborhood", hoods, key="f_hood")
    status_pick = st.pills("Status", STATUSES, selection_mode="multi", default=STATUSES, key="f_status")

    view = drains[drains["tier"].isin(tier_pick or TIERS)]
    if hood_pick != "All neighborhoods":
        view = view[view["neighborhood"] == hood_pick]
    view = view[view["status"].isin(status_pick or STATUSES)].reset_index(drop=True)
    filter_key = f"{sorted(tier_pick or TIERS)}|{hood_pick}|{sorted(status_pick or STATUSES)}"

    selected = None
    if view.empty:
        st.write("No drains match these filters. Turn a filter back on to see more.")
    else:
        st.caption(f"{len(view)} drains · click one to zoom the map")
        event = st.dataframe(
            view[["rank", "tier", "neighborhood", "status"]],
            hide_index=True, use_container_width=True, height=420,
            on_select="rerun", selection_mode="single-row",
            key=f"drain_table-{hash(filter_key)}",  # new filters = fresh table, no stale selection
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "tier": st.column_config.TextColumn("Tier", width="small"),
                "neighborhood": "Neighborhood", "status": "Status",
            },
        )
        rows = event.selection.rows if event and event.selection else []
        if rows and rows[0] < len(view):
            selected = view.iloc[rows[0]]

    # ---------- Selected drain: details + check buttons ----------
    if selected is not None:
        with st.container(border=True):
            st.markdown(f"**#{int(selected['rank'])} {selected['tier'].upper()} · {selected['neighborhood']}**  \n"
                        f"`{selected['asset_id']}` · Status: **{selected['status']}**")
            st.write(selected["reason"])
            st.link_button("Open in Maps", f"https://www.google.com/maps?q={selected['lat']},{selected['lon']}",
                           icon=":material/directions:", use_container_width=True)
            for label, status, icon in [("Clear", "Checked - clear", ":material/check_circle:"),
                                        ("Blocked", "Blocked - reported to 311", ":material/block:"),
                                        ("Can't reach", "Couldn't reach", ":material/do_not_disturb_on:")]:
                if st.button(label, key=f"cc-{status}-{selected['asset_id']}", icon=icon, use_container_width=True):
                    log_status(selected["asset_id"], status, "City crew")
                    st.toast(f"Marked {selected['asset_id']} as {status}.", icon=":material/check_circle:")
                    st.rerun()
            if st.button("Reset to Not checked", key=f"cc-reset-{selected['asset_id']}", use_container_width=True):
                log_status(selected["asset_id"], "Not checked", "City crew")
                st.rerun()

# ---------- Center: storm alert on top of the map (the main feature) ----------
with center_col:
    st.markdown(alert_html, unsafe_allow_html=True)
    if source == "fake":
        st.warning("Could not download live city data, so these drains are FAKE placeholders. Reload to retry.",
                   icon=":material/warning:")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        basemap = st.segmented_control(
            "Map style", list(BASEMAPS), default="Dark", key="basemap", label_visibility="collapsed",
            help="Topographic and terrain views show hills and low spots: water flows downhill to the low drains.") or "Dark"
        if st.button("Refresh", icon=":material/refresh:", help="Reload statuses and redraw the map"):
            st.rerun()
    with st.container(horizontal=True, gap="medium"):
        show_hoods = st.toggle("Neighborhoods", value=True, help="Neighborhood names and outlines; hover to highlight an area")
        show_all = st.toggle("Other drains", value=False, help="Show every other mapped drain (not ranked) as small gray dots")
        show_heat = st.toggle("Risk heatmap", value=False)
        show_nov18 = st.toggle("Nov 18 reports", value=False)
    show_zone = False  # SFPUC 100-year flood zone layer: not wired up yet
    if selected is not None:
        center, zoom, focus = (selected["lat"], selected["lon"]), 17, selected["asset_id"]
    elif hood_pick != "All neighborhoods" and len(view):
        center, zoom, focus = (float(view["lat"].mean()), float(view["lon"].mean())), 14, None
    else:
        center, zoom, focus = None, 12, None
    st_folium(drain_map(visible_on_maps(view), show_heat, show_zone, show_nov18,
                        center=center, zoom=zoom, focus=focus,
                        all_drains=all_drains() if show_all else None, basemap=basemap,
                        hoods=neighborhoods_geojson() if show_hoods else None, height="680px"),
              key="main-map", height=690, use_container_width=True, returned_objects=[])
    if selected is not None:
        st.caption(f"Zoomed to drain #{int(selected['rank'])} ({selected['neighborhood']}). "
                   "Deselect the row to see the whole city.")

# ---------- Right: city-wide numbers (like the turnout panel) ----------
with right:
    st.markdown('<p class="dg-panel-title">Command Center</p>', unsafe_allow_html=True)
    pct = checked_urgent / len(urgent) if len(urgent) else 0
    st.markdown(f"""<p class="dg-label">Urgent drains checked</p>
<p class="dg-big">{checked_urgent} of {len(urgent)}</p>
<div class="dg-bar" role="progressbar" aria-valuenow="{pct * 100:.0f}" aria-valuemin="0" aria-valuemax="100"><span style="width:{pct * 100:.1f}%"></span></div>
<p class="dg-muted">{int(drains["status"].isin(DONE_STATUSES).sum())} of {len(drains)} priority drains checked in all</p>
<p class="dg-label">Volunteers signed up</p><p class="dg-big" style="font-size:30px!important">{len(load_volunteers())}</p>
<p class="dg-label">Nov 18 test</p><p class="dg-big" style="font-size:30px!important">{result_text}</p>
<p class="dg-muted">Share of our 25 Urgent drains, picked with data from before Nov 18, that were at a real
flood spot on Nov 18. Random drains: about 19%. See Evidence.</p>""", unsafe_allow_html=True)
    if source == "live" and summary:
        st.markdown(f'<p class="dg-muted" style="margin-top:14px">Ranked from real SF data: {summary["reports_downloaded"]:,} '
                    f'flood reports, {summary["drains"]:,} drains, {summary["storm_days_used"]} past storm days '
                    f'(data before Nov 18 only). Loaded {now_str()}.</p>', unsafe_allow_html=True)
    st.download_button("Crew route sheet (CSV)", view.to_csv(index=False).encode(),
                       "drainguard_route_sheet.csv", "text/csv", icon=":material/download:", use_container_width=True)
    rs = round_start()
    st.caption(f"Checks count for {check_ttl_days()} days"
               + (f" or since the round started {rs:%b %d %H:%M}" if rs is not None else "")
               + f". Counting from {check_cutoff():%b %d %H:%M}. Saved to: "
               + ("Supabase" if backend() == "supabase" else "local files (reset on restart)") + ". "
               + "Statuses come from this app's buttons; the city does not publish drain-cleaning records. "
               + "Drains a volunteer is walking to are hidden from the map until they report or time runs out.")
