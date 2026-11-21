"""Command Center: the city crew lead's view (laptop)."""
import json

import streamlit as st
from streamlit_folium import st_folium

from src.alerts import send_push
from src.data import (RESULTS_JSON, TIERS, all_drains, data_source, drains_with_status, load_volunteers, neighborhoods_geojson,
                      log_status,
                      now_str, setting)
from src.geo import nearest_drains
from src.maps import BASEMAPS, drain_map
from src.weather import get_forecast, storm_status

CITY_TOPIC = setting("CITY_TOPIC", "drainguard-city-demo")
APP_URL = setting("APP_URL", "")

drains = drains_with_status()

@st.cache_data(ttl=1800, show_spinner=False)
def _forecast():
    return get_forecast()


# ---------- Storm banner (the first thing anyone needs to know) ----------
storm = storm_status(_forecast())
STYLE = {  # background, text, label
    "storm": ("#4A1D7A", "#FFFFFF", "STORM ALERT"),
    "watch": ("#B3261E", "#FFFFFF", "RAIN WATCH"),
    "clear": ("#1E6B3A", "#FFFFFF", "ALL CLEAR"),
    "unknown": ("#5F6B7A", "#FFFFFF", "FORECAST"),
}[storm["level"]]
unchecked = int(((drains["tier"] == "Urgent") & (drains["status"] == "Not checked")).sum())
action = (f"{unchecked} Urgent drains still need checking before the rain." if storm["level"] in ("storm", "watch")
          else f"{unchecked} Urgent drains not yet checked.")
st.markdown(
    f"""<div style="background:{STYLE[0]};color:{STYLE[1]};border-radius:12px;padding:20px 24px;margin:4px 0 16px">
  <div style="font-size:14px;font-weight:700;letter-spacing:.12em;opacity:.85">{STYLE[2]} · SAN FRANCISCO</div>
  <div style="font-size:34px;font-weight:800;line-height:1.15;margin:6px 0">{storm['headline']}</div>
  <div style="font-size:20px;font-weight:600;margin-bottom:6px">{action}</div>
  <div style="font-size:14px;opacity:.85">{storm['detail']}<br>Source: National Weather Service forecast,
  updated every 30 min.</div>
</div>""",
    unsafe_allow_html=True,
)

# ---------- Title row ----------
top_l, top_r = st.columns([3, 1])
with top_l:
    st.title("DrainGuard Command Center")
    st.caption(f"Data loaded {now_str()} · Drains ranked by how likely they are to need checking")
with top_r:
    st.write("")
    send = st.button("Send storm alert", type="primary", use_container_width=True,
                     help=f"Pushes the top 10 Urgent drains to ntfy topic '{CITY_TOPIC}'")

source = data_source()
if source == "fake":
    st.warning("Could not download live city data, so these drains are FAKE placeholders. Reload to retry.",
               icon=":material/warning:")
elif source == "live" and RESULTS_JSON.exists():
    try:
        s = json.loads(RESULTS_JSON.read_text())
        st.caption(f"Ranked from real SF data: {s['reports_downloaded']:,} flood reports, {s['drains']:,} drains, "
                   f"{s['storm_days_used']} past storm days (data before Nov 18 only).")
    except Exception:
        pass

if send:
    top10 = drains[(drains["tier"] == "Urgent") & (drains["status"] == "Not checked")].head(10)
    if top10.empty:
        st.toast("No unchecked Urgent drains to send.")
    else:
        body = "\n".join(f"#{int(r['rank'])} {r['neighborhood']} ({r['asset_id']})" for _, r in top10.iterrows())
        ok = send_push(CITY_TOPIC, "DrainGuard: storm alert", f"{storm['headline']}\nCheck these drains first:\n" + body,
                       click_url=APP_URL or None)
        st.toast("Storm alert sent." if ok else "Alert failed. Check the internet connection.",
                 icon=":material/check_circle:" if ok else ":material/error:")

# ---------- KPI tiles ----------
urgent = drains[drains["tier"] == "Urgent"]
checked_urgent = (urgent["status"] != "Not checked").sum()
result_text = "pending"
if RESULTS_JSON.exists():
    try:
        result_text = json.loads(RESULTS_JSON.read_text()).get("headline", "pending")
    except Exception:
        pass

k1, k2, k3, k4 = st.columns(4)
k1.metric("Urgent drains", len(urgent))
k2.metric("Urgent checked", f"{checked_urgent} of {len(urgent)}")
k3.metric("Volunteers", len(load_volunteers()))
k4.metric("Nov 18 test", result_text, help="Share of our 25 Urgent drains (picked with data from before Nov 18) that were at a real flood spot on Nov 18. Random drains: about 19%. See Evidence.")

# ---------- Filters (full width, above map + list) ----------
STATUSES_ALL = ["Not checked", "Checked - clear", "Blocked - reported to 311", "Couldn't reach"]
f1, f2, f3 = st.columns([1.2, 1, 1.8])
with f1:
    tier_pick = st.pills("Tier", TIERS, selection_mode="multi", default=["Urgent", "High"], key="f_tier")
with f2:
    hoods = ["All neighborhoods"] + sorted(drains["neighborhood"].dropna().unique())
    hood_pick = st.selectbox("Neighborhood", hoods, key="f_hood")
with f3:
    status_pick = st.pills("Status", STATUSES_ALL, selection_mode="multi", default=STATUSES_ALL, key="f_status")

view = drains[drains["tier"].isin(tier_pick or TIERS)]
if hood_pick != "All neighborhoods":
    view = view[view["neighborhood"] == hood_pick]
view = view[view["status"].isin(status_pick or STATUSES_ALL)].reset_index(drop=True)
filter_key = f"{sorted(tier_pick or TIERS)}|{hood_pick}|{sorted(status_pick or STATUSES_ALL)}"
n_checked = int((drains["status"] != "Not checked").sum())
st.caption(f"Showing **{len(view)}** of {len(drains)} priority drains · {n_checked} checked so far "
           "(statuses come from this app's buttons; the city does not publish drain-cleaning records)")

# ---------- Map + urgency list ----------
map_col, list_col = st.columns([2, 1])

with list_col:
    st.subheader("Urgency list")
    selected = None
    if view.empty:
        st.write("No drains match these filters. Turn a filter back on to see more.")
    else:
        st.caption("Click a row to zoom the map to that drain and mark it checked.")
        event = st.dataframe(
            view[["rank", "tier", "neighborhood", "reason", "status", "checked_at"]],
            hide_index=True, use_container_width=True, height=330,
            on_select="rerun", selection_mode="single-row",
            key=f"drain_table-{hash(filter_key)}",  # new filters = fresh table, no stale selection
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "tier": st.column_config.TextColumn("Tier", width="small"),
                "neighborhood": "Neighborhood", "reason": "Why", "status": "Status",
                "checked_at": "Last checked",
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
                           use_container_width=True)
            b1, b2, b3 = st.columns(3)
            for col, label, status in [(b1, "✓ Clear", "Checked - clear"),
                                       (b2, "Blocked", "Blocked - reported to 311"),
                                       (b3, "Can't reach", "Couldn't reach")]:
                if col.button(label, key=f"cc-{status}-{selected['asset_id']}", use_container_width=True):
                    log_status(selected["asset_id"], status, "City crew")
                    st.toast(f"Marked {selected['asset_id']} as {status}.", icon=":material/check_circle:")
                    st.rerun()
            if st.button("Reset to Not checked", key=f"cc-reset-{selected['asset_id']}", use_container_width=True):
                log_status(selected["asset_id"], "Not checked", "City crew")
                st.rerun()
    st.download_button("Download crew route sheet (CSV)", view.to_csv(index=False).encode(),
                       "drainguard_route_sheet.csv", "text/csv", use_container_width=True)

    if st.button("Notify nearby volunteers", use_container_width=True):
        vols = load_volunteers()
        open_drains = drains[drains["tier"].isin(["Urgent", "High"]) & (drains["status"] == "Not checked")]
        sent = 0
        for _, v in vols.iterrows():
            near = nearest_drains(open_drains, v["lat"], v["lon"], v["radius_m"], limit=3)
            if near.empty:
                continue
            body = "\n".join(f"#{int(r['rank'])} {r['tier']} · {int(r['distance_m'])} m ({r['walk_min']} min walk)"
                             for _, r in near.iterrows())
            body += "\nLook and report only. Never clear drains in a storm."
            sent += send_push(v["topic"], "DrainGuard: drains near you", body, tags="droplet",
                              click_url=APP_URL or None)
        st.toast(f"Sent {sent} volunteer alert(s)." if len(vols) else "No volunteers yet. Sign up on the Volunteer page.")

with map_col:
    basemap = st.segmented_control(
        "Map style", list(BASEMAPS), default="Streets", key="basemap",
        help="Topographic and terrain views show hills and low spots: water flows downhill to the low drains.") or "Streets"
    cn, c0, c1, c2, c3 = st.columns(5)
    show_hoods = cn.toggle("Neighborhoods", value=True, help="Neighborhood names and outlines; hover to highlight an area")
    show_all = c0.toggle("Other drains", value=False,
                         help="Show every other mapped drain (not ranked) as small gray dots")
    show_heat = c1.toggle("Risk heatmap", value=False)
    show_zone = c2.toggle("City flood zone", value=False, disabled=True,
                          help="Coming soon: SFPUC 100-year flood zone layer")
    show_nov18 = c3.toggle("Nov 18 reports", value=False)
    if st.button("Refresh map", icon=":material/refresh:"):
        st.rerun()
    if selected is not None:
        center, zoom, focus = (selected["lat"], selected["lon"]), 17, selected["asset_id"]
        st.caption(f"Zoomed to drain #{int(selected['rank'])} ({selected['neighborhood']}). "
                   "Deselect the row to see the whole city.")
    elif hood_pick != "All neighborhoods" and len(view):
        center, zoom, focus = (float(view["lat"].mean()), float(view["lon"].mean())), 14, None
    else:
        center, zoom, focus = None, 12, None
    st_folium(drain_map(view, show_heat, show_zone, show_nov18,
                        center=center, zoom=zoom, focus=focus,
                        all_drains=all_drains() if show_all else None, basemap=basemap,
                        hoods=neighborhoods_geojson() if show_hoods else None),
              key="main-map", height=560, use_container_width=True, returned_objects=[])
