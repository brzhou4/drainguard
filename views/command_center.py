"""Command Center: the city crew lead's view (laptop)."""
import json

import streamlit as st
from streamlit_folium import st_folium

from src.alerts import send_push
from src.data import (RESULTS_JSON, TIERS, all_drains, data_source, drains_with_status, load_volunteers, log_status,
                      now_str, setting)
from src.geo import nearest_drains
from src.maps import drain_map

CITY_TOPIC = setting("CITY_TOPIC", "drainguard-city-demo")
APP_URL = setting("APP_URL", "")

drains = drains_with_status()

# ---------- Status bar ----------
top_l, top_r = st.columns([3, 1])
with top_l:
    st.title("DrainGuard Command Center")
    st.caption(f"Storm expected this weekend · Data loaded {now_str()} · "
               "Drains ranked by how likely they are to need checking")
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
        ok = send_push(CITY_TOPIC, "DrainGuard: storm alert", "Check these drains first:\n" + body,
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
k4.metric("Nov 18 test", result_text, help="Share of Nov 18 flooding near our top 50 drains. See Evidence.")

# ---------- Map + urgency list ----------
map_col, list_col = st.columns([2, 1])

with list_col:
    st.subheader("Urgency list")
    f1, f2 = st.columns(2)
    tier_pick = f1.multiselect("Tier", TIERS, default=["Urgent", "High"])
    hoods = sorted(drains["neighborhood"].dropna().unique())
    hood_pick = f2.multiselect("Neighborhood", hoods)
    status_pick = st.multiselect("Status", ["Not checked", "Checked - clear", "Blocked - reported to 311",
                                            "Couldn't reach"], default=["Not checked", "Checked - clear", "Blocked - reported to 311", "Couldn't reach"])

    view = drains[drains["tier"].isin(tier_pick or TIERS)]
    if hood_pick:
        view = view[view["neighborhood"].isin(hood_pick)]
    if status_pick:
        view = view[view["status"].isin(status_pick)]

    view = view.reset_index(drop=True)
    selected = None
    if view.empty:
        st.write("No drains match these filters. Clear a filter to see more.")
    else:
        st.caption("Click a row to zoom the map to that drain and mark it checked.")
        event = st.dataframe(
            view[["rank", "tier", "neighborhood", "reason", "status", "checked_at"]],
            hide_index=True, use_container_width=True, height=330,
            on_select="rerun", selection_mode="single-row", key="drain_table",
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
    c0, c1, c2, c3 = st.columns(4)
    show_all = c0.toggle("All drains", value=True, help="Every mapped storm drain as a small gray dot")
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
    else:
        center, zoom, focus = None, 12, None
    st_folium(drain_map(view if len(view) else drains, show_heat, show_zone, show_nov18,
                        center=center, zoom=zoom, focus=focus,
                        all_drains=all_drains() if show_all else None),
              key=f"map-{focus}", height=560, use_container_width=True, returned_objects=[])
