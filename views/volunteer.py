"""Volunteer: the neighbor's view (phone, single column)."""
import io
import secrets

import streamlit as st
from streamlit_folium import st_folium

from src.data import NEIGHBORHOOD_CENTERS, drains_with_status, load_volunteers, log_status, save_volunteer
from src.geo import nearest_drains
from src.maps import drain_map

st.warning("**Look and report only.** Never clear drains during a storm or step into pooled water. "
           "Report blocked drains to 311.", icon=":material/warning:")
st.title("Check a drain near you")

vols = load_volunteers()

# ---------- Who are you? ----------
names = vols["name"].tolist()
options = ["New volunteer"] + names
saved = st.session_state.get("me")
choice = st.selectbox("I am", options, index=options.index(saved) if saved in options else 0)

if choice == "New volunteer":
    name = st.text_input("First name (no last names or addresses)")
    hood = st.selectbox("Your area", list(NEIGHBORHOOD_CENTERS))
    radius = st.slider("How far will you walk? (meters)", 200, 800, 400, 50)
    lat, lon = NEIGHBORHOOD_CENTERS[hood]
    st.caption("Optional: tap the map to set your exact spot.")
    clicked = st_folium(drain_map(drains_with_status(), show_heat=False, center=(lat, lon), zoom=14,
                                  you=(lat, lon), radius_m=radius),
                        height=320, use_container_width=True, returned_objects=["last_clicked"])
    if clicked and clicked.get("last_clicked"):
        lat, lon = clicked["last_clicked"]["lat"], clicked["last_clicked"]["lng"]
        st.caption(f"Spot set to {lat:.4f}, {lon:.4f}")
    if st.button("Sign up", type="primary", use_container_width=True, disabled=not name.strip()):
        topic = f"drainguard-vol-{secrets.token_hex(3)}"
        save_volunteer(name.strip(), topic, lat, lon, radius)
        st.session_state["me"] = name.strip()
        st.rerun()
    st.stop()

me = vols[vols["name"] == choice].iloc[0]

# ---------- Alerts setup ----------
with st.expander("Get alerts on this phone", expanded=False):
    st.write(f"1. Install the free **ntfy** app.  2. Subscribe to topic **`{me['topic']}`**, or scan this code.")
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(f"https://ntfy.sh/{me['topic']}").save(buf)
        st.image(buf.getvalue(), width=180)
    except Exception:
        st.caption(f"Open https://ntfy.sh/{me['topic']} on this phone.")

# ---------- Nearby drains ----------
drains = drains_with_status()
open_drains = drains[drains["tier"].isin(["Urgent", "High"]) & (drains["status"] == "Not checked")]
near = nearest_drains(open_drains, me["lat"], me["lon"], me["radius_m"], limit=5)

st.subheader(f"Drains near you ({len(near)})")
if near.empty:
    st.write("Nothing to check within your walking distance right now. Thanks! We'll alert you when that changes.")

for _, r in near.iterrows():
    with st.container(border=True):
        st.markdown(f"**{r['tier'].upper()} · #{int(r['rank'])}** · {int(r['distance_m'])} m, "
                    f"about {r['walk_min']} min walk")
        st.write(r["reason"])
        st.link_button("Open in Maps", f"https://www.google.com/maps?q={r['lat']},{r['lon']}",
                       use_container_width=True)
        b1, b2, b3 = st.columns(3)
        if b1.button("Checked - clear", key=f"c-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Checked - clear", me["name"])
            st.toast("Thanks! Logged.", icon=":material/check_circle:")
            st.rerun()
        if b2.button("Blocked", key=f"b-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Blocked - reported to 311", me["name"])
            st.session_state["report_311"] = r["asset_id"]
            st.rerun()
        if b3.button("Couldn't reach", key=f"n-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Couldn't reach", me["name"])
            st.toast("Logged. Someone else will try.")
            st.rerun()

if st.session_state.get("report_311"):
    st.success(f"Drain {st.session_state['report_311']} marked blocked. Please also report it to the city:")
    st.link_button("Report to SF 311", "https://sf311.org", type="primary", use_container_width=True)
