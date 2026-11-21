"""Volunteer: the neighbor's view (phone, single column)."""
import io
import secrets

import streamlit as st
from streamlit_folium import st_folium

from src.data import drains_with_status, load_volunteers, log_status, save_volunteer
from src.geo import geocode_sf, in_sf, nearest_drains
from src.i18n import LANGS, t
from src.maps import drain_map

# ---------- Language ----------
lang = st.segmented_control("Language", list(LANGS), default="en", format_func=LANGS.get,
                            label_visibility="collapsed", key="lang") or "en"


def T(key, **kw):
    return t(key, lang, **kw)


st.warning(T("safety_banner"), icon=":material/warning:")
st.title(T("title"))

vols = load_volunteers()

# ---------- Who are you? ----------
NEW = "__new__"
names = vols["name"].tolist()
options = [NEW] + names
saved = st.session_state.get("me")
choice = st.selectbox(T("i_am"), options, index=options.index(saved) if saved in options else 0,
                      format_func=lambda o: T("new_volunteer") if o == NEW else o, key="who")

if choice == NEW:
    name = st.text_input(T("first_name"), key="first_name")
    radius = st.slider(T("how_far"), 200, 800, 400, 50, key="radius")

    # ----- Starting spot: share location, type an address, or tap the map -----
    st.subheader(T("where_title"))
    st.caption(T("where_help"))
    spot = st.session_state.get("spot")  # (lat, lon, how) once set

    if st.button(T("share_location"), icon=":material/my_location:", use_container_width=True, key="share_btn"):
        st.session_state["geo_req"] = st.session_state.get("geo_req", 0) + 1
    if st.session_state.get("geo_req"):
        from streamlit_js_eval import get_geolocation
        loc = get_geolocation(component_key=f"geo-{st.session_state['geo_req']}")
        if loc is None:
            st.caption(T("share_wait"))
        else:
            st.session_state["geo_req"] = 0
            coords = (loc or {}).get("coords") or {}
            if "latitude" in coords:
                la, lo = coords["latitude"], coords["longitude"]
                if in_sf(la, lo):
                    st.session_state["spot"] = (la, lo, T("spot_from_gps"))
                    st.rerun()
                st.session_state["spot_error"] = "outside_sf"
            else:
                st.session_state["spot_error"] = "share_failed"
            st.rerun()

    a1, a2 = st.columns([3, 1], vertical_alignment="bottom")
    addr = a1.text_input(T("address"), placeholder=T("address_ph"), key="address")
    if a2.button(T("find"), use_container_width=True, disabled=not addr.strip(), key="find_btn"):
        hit = geocode_sf(addr)
        if hit is None:
            st.session_state["spot_error"] = "address_failed"
        else:
            st.session_state["spot"] = hit
            st.session_state.pop("spot_error", None)
        st.rerun()

    if st.session_state.get("spot_error"):
        st.error(T(st.session_state.pop("spot_error")))

    st.caption(T("tap_map"))
    center = (spot[0], spot[1]) if spot else None
    clicked = st_folium(drain_map(drains_with_status(), show_heat=False, center=center, zoom=16 if spot else 12,
                                  you=center, radius_m=radius if spot else None),
                        height=320, use_container_width=True, returned_objects=["last_clicked"],
                        key=f"signup-map-{center}")
    last = (clicked or {}).get("last_clicked")
    if last:
        tap = (round(last["lat"], 6), round(last["lng"], 6))
        if tap != st.session_state.get("last_tap"):  # a new tap, not the same one returned again
            st.session_state["last_tap"] = tap
            if in_sf(*tap):
                st.session_state["spot"] = (tap[0], tap[1], T("spot_from_map"))
            else:
                st.session_state["spot_error"] = "outside_sf"
            st.rerun()

    if spot:
        st.success(T("spot_set", label=spot[2]), icon=":material/location_on:")
    else:
        st.caption(T("need_spot"))

    if st.button(T("sign_up"), type="primary", use_container_width=True, disabled=not (name.strip() and spot),
                 key="signup_btn"):
        topic = f"drainguard-vol-{secrets.token_hex(3)}"
        save_volunteer(name.strip(), topic, spot[0], spot[1], radius)
        st.session_state["me"] = name.strip()
        for k in ("spot", "last_tap", "geo_req"):
            st.session_state.pop(k, None)
        st.rerun()
    st.stop()

me = vols[vols["name"] == choice].iloc[0]

# ---------- Alerts setup ----------
with st.expander(T("alerts_title"), expanded=False):
    st.write(T("alerts_body", topic=me["topic"]))
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(f"https://ntfy.sh/{me['topic']}").save(buf)
        st.image(buf.getvalue(), width=180)
    except Exception:
        st.caption(f"https://ntfy.sh/{me['topic']}")

# ---------- Nearby drains ----------
drains = drains_with_status()
open_drains = drains[drains["tier"].isin(["Urgent", "High"]) & (drains["status"] == "Not checked")]
near = nearest_drains(open_drains, me["lat"], me["lon"], me["radius_m"], limit=5)

st.subheader(T("near_title", n=len(near)))
if near.empty:
    st.write(T("none_near"))

for _, r in near.iterrows():
    with st.container(border=True):
        tier = T(f"tier_{r['tier']}")
        st.markdown(f"**{tier.upper()} · #{int(r['rank'])}** · "
                    + T("card_dist", m=int(r["distance_m"]), mins=r["walk_min"]))
        st.write(r["reason"])
        st.link_button(T("open_maps"), f"https://www.google.com/maps?q={r['lat']},{r['lon']}",
                       use_container_width=True)
        b1, b2, b3 = st.columns(3)
        if b1.button(T("btn_clear"), key=f"c-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Checked - clear", me["name"])
            st.toast(T("thanks"), icon=":material/check_circle:")
            st.rerun()
        if b2.button(T("btn_blocked"), key=f"b-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Blocked - reported to 311", me["name"])
            st.session_state["report_311"] = r["asset_id"]
            st.rerun()
        if b3.button(T("btn_cant"), key=f"n-{r['asset_id']}", use_container_width=True):
            log_status(r["asset_id"], "Couldn't reach", me["name"])
            st.toast(T("logged_retry"))
            st.rerun()

if st.session_state.get("report_311"):
    st.success(T("blocked_msg", id=st.session_state["report_311"]))
    st.link_button(T("report_311"), "https://sf311.org", type="primary", use_container_width=True)
