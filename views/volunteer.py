"""Volunteer: the neighbor's view (phone, single column).

Set where you are (share location, type an address, or tap the map), see the closest priority drain on a
map with walking directions, and report sewer problems or flooding to SF 311. No sign-up.
"""
import streamlit as st
from streamlit_folium import st_folium

from src.data import DONE_STATUSES, SF311_SEWER_FORM, drains_with_status
from src.geo import geocode_sf, in_sf, nearest_drains, walking_order, walking_route_url
from src.i18n import LANGS, t
from src.maps import drain_map

SF311_SEWER_URL = SF311_SEWER_FORM + "?submission%5Bticket_type_code%5D=input%3ASewer"

# ---------- Language: one small button in the top-right corner; each tap moves to the next language ----------
_ORDER = list(LANGS)
if st.session_state.get("lang") not in LANGS:
    st.session_state["lang"] = "en"


def _next_lang():
    i = _ORDER.index(st.session_state["lang"])
    st.session_state["lang"] = _ORDER[(i + 1) % len(_ORDER)]


lang = st.session_state["lang"]
with st.container(horizontal=True, horizontal_alignment="right"):
    st.button(LANGS[lang], icon=":material/translate:", type="tertiary", on_click=_next_lang, key="lang_btn",
              help=" → ".join(LANGS.values()))


def T(key, **kw):
    return t(key, lang, **kw)


st.warning(T("safety_banner"), icon=":material/warning:")
st.title(T("title"))

# ---------- Where are you? Share location, type an address, or tap the map ----------
spot = st.session_state.get("spot")  # (lat, lon, label) once set

if st.button(T("share_location"), icon=":material/my_location:", type="primary", use_container_width=True,
             key="share_btn"):
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
            else:
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
    st.rerun()

if st.session_state.get("spot_error"):
    st.error(T(st.session_state.pop("spot_error")))

# ---------- Closest priority drain ----------
drains = drains_with_status()
todo = drains[drains["tier"].isin(["Urgent", "High"]) & ~drains["status"].isin(DONE_STATUSES)]
closest = None
if spot:
    hit = nearest_drains(todo, spot[0], spot[1], radius_m=100_000, limit=1)
    if len(hit):
        closest = walking_order(hit, spot[0], spot[1]).iloc[0]

if closest is not None:
    st.subheader(T("closest_title"))
    tier = T(f"tier_{closest['tier']}")
    st.markdown(f"**{tier.upper()} · #{int(closest['rank'])}** · "
                + T("card_dist", m=int(closest["distance_m"]), mins=int(closest["walk_min"])))
    st.caption(closest["reason"])
    st.link_button(T("walk_there"), walking_route_url((spot[0], spot[1]), [(closest["lat"], closest["lon"])]),
                   type="primary", icon=":material/directions_walk:", use_container_width=True)
    mid = ((spot[0] + closest["lat"]) / 2, (spot[1] + closest["lon"]) / 2)
    d = float(closest["distance_m"])
    zoom = 17 if d < 250 else 16 if d < 600 else 15 if d < 1200 else 14 if d < 2500 else 13
    center, stops = (float(mid[0]), float(mid[1])), walking_order(hit, spot[0], spot[1])
else:
    center, zoom, stops = ((spot[0], spot[1]) if spot else None), (16 if spot else 12), None
    if spot:
        st.write(T("none_near"))

st.caption(T("tap_map") if spot else T("where_help_short"))
you = (float(spot[0]), float(spot[1])) if spot else None
shown = drains[drains["asset_id"].isin([closest["asset_id"]])] if closest is not None else drains.iloc[0:0]
clicked = st_folium(drain_map(shown, show_heat=False, center=center, zoom=zoom, you=you, stops=stops),
                    height=380, use_container_width=True, returned_objects=["last_clicked"],
                    key=f"vol-map-{center}-{zoom}")
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

# ---------- Report to the city ----------
st.divider()
st.link_button(T("report_311_general"), SF311_SEWER_URL, icon=":material/open_in_new:", use_container_width=True)
st.caption(T("report_311_help"))
