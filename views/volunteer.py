"""Volunteer: the neighbor's view (phone, single column). Volunteers are anonymous: no names, just a private link."""
import io

import streamlit as st
from streamlit_folium import st_folium

from src.data import (CONDITIONS, DONE_STATUSES, EN_ROUTE, EN_ROUTE_BUFFER_MIN, TIERS, VOLUNTEER, check_cutoff,
                      check_ttl_days, claim, drains_with_status, get_volunteer, log_status, now_str,
                      open_for_volunteers, release_claim, save_volunteer, setting, sf311_description,
                      sf311_report_url, visible_on_maps, volunteer_stats)
from src.geo import (geocode_sf, in_sf, maps_url, nearest_drains, street_view_url, walking_order,
                     walking_route_url)
from src.i18n import LANGS, condition_label, t
from src.maps import drain_map

ADOPT_A_DRAIN_URL = "https://adoptadrain.sfwater.org/"
MAX_PHOTO_MB = 8

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


def _personal_link(token):
    base = setting("APP_URL", "").rstrip("/")
    if not base:
        try:
            host = st.context.headers.get("host", "")
            base = f"https://{host}" if host and "localhost" not in host else (f"http://{host}" if host else "")
        except Exception:
            base = ""
    return f"{base}/volunteer?v={token}"


def _hhmm(ts):
    return ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)[11:16]


# ---------- Who are you? A private link (?v=token), never a name ----------
qp_token = st.query_params.get("v")
if qp_token:
    st.session_state["token"] = qp_token
token = st.session_state.get("token")
me = get_volunteer(token)
if token and me is None:
    st.session_state.pop("token", None)
    token = None

st.warning(T("safety_banner"), icon=":material/warning:")
st.title(T("title"))

# ---------- Sign up / sign back in ----------
if me is None:
    st.caption(T("anon_note"))
    tab_new, tab_back = st.tabs([T("tab_new"), T("tab_back")])
    with tab_back:
        pasted = st.text_input(T("paste_link"), key="paste_link")
        if st.button(T("open_link"), use_container_width=True, disabled=not pasted.strip(), key="open_link"):
            code = pasted.strip().split("v=")[-1].split("&")[0]
            if get_volunteer(code) is not None:
                st.session_state["token"] = code
                st.query_params["v"] = code
                st.rerun()
            st.error(T("link_not_found"))
    with tab_new:
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
        clicked = st_folium(drain_map(visible_on_maps(drains_with_status()), show_heat=False, center=center, zoom=16 if spot else 12,
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


        age = st.radio(T("age_q"), ["yes", "no"], index=None, key="age",
                       format_func=lambda a: T("age_adult") if a == "yes" else T("age_minor"))
        agreed = st.checkbox(T("safety_agree"), key="agreed")
        with st.expander(T("safety_rules_title")):
            st.markdown(T("safety_rules"))
        if st.button(T("sign_up"), type="primary", use_container_width=True, disabled=not (spot and agreed and age),
                     key="signup_btn"):
            new_token = save_volunteer(spot[0], spot[1], radius, lang=lang, adult=age)
            for k in ("spot", "last_tap", "geo_req"):
                st.session_state.pop(k, None)
            st.session_state["token"] = new_token
            st.session_state["just_signed_up"] = True
            st.query_params["v"] = new_token
            st.rerun()
    st.stop()

token = me["token"]
my_lat, my_lon, my_radius = float(me["lat"]), float(me["lon"]), int(me["radius_m"])  # plain numbers for folium
st.query_params["v"] = token  # keep the private link in the address bar so a reload stays signed in

# ---------- Your link + alerts ----------
with st.expander(T("your_link_title"), expanded=bool(st.session_state.pop("just_signed_up", False)),
                 icon=":material/key:"):
    st.write(T("your_link_body"))
    st.code(_personal_link(token), language=None, wrap_lines=True)
    if st.button(T("sign_out"), use_container_width=True, key="sign_out"):
        st.session_state.pop("token", None)
        st.query_params.clear()
        st.rerun()

if me["adult"] == "no":
    st.caption(T("minor_reminder"))

# ---------- Progress ----------
drains = drains_with_status()
mine_all, mine_round = volunteer_stats(token)
urgent = drains[drains["tier"] == "Urgent"]
area = nearest_drains(drains[drains["tier"].isin(["Urgent", "High"])], my_lat, my_lon, my_radius, limit=10_000)
m1, m2, m3 = st.columns(3)
m1.metric(T("m_you"), mine_round, help=T("m_you_help", total=mine_all, days=check_ttl_days()))
m2.metric(T("m_city"), f"{int(urgent['status'].isin(DONE_STATUSES).sum())}/{len(urgent)}")
m3.metric(T("m_area"), f"{int(area['status'].isin(DONE_STATUSES).sum())}/{len(area)}")
st.caption(T("round_note", since=check_cutoff().strftime("%b %d %H:%M")))
st.link_button(T("adopt"), ADOPT_A_DRAIN_URL, use_container_width=True, icon=":material/volunteer_activism:")

# ---------- 311 handoff after a "Blocked" report ----------
# The city form can't be shown inside DrainGuard (SF 311 blocks embedding), so we open it already
# filled in: sewer category picked and the description written. The volunteer pins the spot and submits.
pending = st.session_state.get("report_311")
if isinstance(pending, dict):
    with st.container(border=True):
        st.success(T("blocked_msg", id=pending["asset_id"]), icon=":material/report:")
        st.markdown(T("h311_steps", lat=round(float(pending["lat"]), 6), lon=round(float(pending["lon"]), 6)))
        st.link_button(T("report_311"), sf311_report_url(pending), type="primary", icon=":material/open_in_new:",
                       use_container_width=True)
        st.caption(T("h311_copy"))
        st.code(sf311_description(pending), language=None, wrap_lines=True)
        if st.button(T("h311_done"), icon=":material/check:", use_container_width=True, key="h311_done"):
            log_status(pending["asset_id"], "Blocked - reported to 311", VOLUNTEER, token,
                       condition=pending.get("condition", ""))
            st.session_state.pop("report_311", None)
            st.toast(T("thanks"), icon=":material/check_circle:")
            st.rerun()
elif pending:  # left over from an older session that stored only the ID
    st.session_state.pop("report_311", None)


# ---------- Report form ----------
@st.dialog(" ")
def report(r, preset):
    st.markdown(f"**{T('report_title', stop=int(r['stop']))}** · `{r['asset_id']}`")
    choices = ["Checked - clear", "Blocked", "Couldn't reach"]
    status = st.radio(T("what_saw"), choices, index=choices.index(preset), format_func=lambda s: T(f"st_{s}"))
    looked = status != "Couldn't reach"
    cond = st.pills(T("condition"), CONDITIONS, selection_mode="multi",
                    format_func=lambda c: condition_label(c, lang)) if looked else []
    note = st.text_input(T("note"), max_chars=200)
    photo = st.file_uploader(T("photo"), type=["jpg", "jpeg", "png", "heic", "webp"]) if looked else None
    if st.button(T("submit"), type="primary", use_container_width=True):
        photo_ref = ""
        if photo is not None:
            if photo.size > MAX_PHOTO_MB * 1024 * 1024:
                st.error(T("photo_too_big", mb=MAX_PHOTO_MB))
                st.stop()
            from src.store import save_photo
            ext = photo.name.rsplit(".", 1)[-1].lower()
            stamp = now_str().replace(" ", "_").replace(":", "")
            photo_ref = save_photo(f"{r['asset_id']}_{stamp}_{token[:4]}.{ext}", photo.getvalue(),
                                   photo.type or "image/jpeg")
        cond_s = "; ".join(cond or [])
        log_status(r["asset_id"], status, VOLUNTEER, token, condition=cond_s, note=note.strip(), photo=photo_ref)
        if status == "Blocked":
            st.session_state["report_311"] = {"asset_id": r["asset_id"], "lat": float(r["lat"]),
                                              "lon": float(r["lon"]), "neighborhood": r["neighborhood"],
                                              "condition": cond_s}
        else:
            st.toast(T("thanks") if looked else T("logged_retry"), icon=":material/check_circle:")
        st.rerun()


# ---------- Nearby drains, in walking order ----------
# Drains another volunteer is walking to are left out (and off the map) until they report or time runs out.
open_drains = open_for_volunteers(drains, token)
show_more = st.toggle(T("show_more"), value=False, key="show_more")
near = nearest_drains(open_drains, my_lat, my_lon, my_radius, limit=10 if show_more else 5)
mine_en_route = open_drains[(open_drains["status"] == EN_ROUTE) & ~open_drains["asset_id"].isin(near["asset_id"])]
if len(mine_en_route):  # drains I'm walking to always stay on my list
    import pandas as pd
    near = pd.concat([near, nearest_drains(mine_en_route, my_lat, my_lon, 100_000, limit=len(mine_en_route))])
route = walking_order(near, my_lat, my_lon) if len(near) else near

st.subheader(T("near_title", n=len(route)))
if route.empty:
    st.write(T("none_near"))
    st.stop()

st_folium(drain_map(visible_on_maps(route, token), show_heat=False, center=(my_lat, my_lon), zoom=15,
                    you=(my_lat, my_lon), radius_m=my_radius, stops=route),
          height=300, use_container_width=True, returned_objects=[], key=f"walk-map-{len(route)}")
st.caption(T("walk_total", n=len(route), mins=int(route["leg_min"].sum())))
st.link_button(T("open_walk"), walking_route_url((my_lat, my_lon), route[["lat", "lon"]].values.tolist()),
               icon=":material/directions_walk:", use_container_width=True)

for _, r in route.iterrows():
    mine = r["status"] == EN_ROUTE
    with st.container(border=True):
        tier = T(f"tier_{r['tier']}") if r["tier"] in TIERS else r["tier"]
        st.markdown(f"**{T('stop', n=int(r['stop']))} · {tier.upper()} · #{int(r['rank'])}**")
        st.write(T("leg_first" if r["stop"] == 1 else "leg", mins=int(r["leg_min"])))
        if mine:
            st.caption(":blue[" + T("en_route_until", until=str(r["en_route_until"])[11:16]) + "]")
        if r["attempts"]:
            st.caption(T("attempts", n=int(r["attempts"])))
        st.caption(r["reason"])
        l1, l2 = st.columns(2)
        l1.link_button(T("open_maps"), maps_url(r["lat"], r["lon"]), use_container_width=True)
        l2.link_button(T("street_view"), street_view_url(r["lat"], r["lon"]), use_container_width=True)
        # Hold = walk from your starting spot + EN_ROUTE_BUFFER_MIN minutes.
        hold_min = int(r["walk_min"]) + EN_ROUTE_BUFFER_MIN
        if mine:
            if st.button(T("release"), key=f"rel-{r['asset_id']}", use_container_width=True):
                release_claim(r["asset_id"], token)
                st.rerun()
        elif st.button(T("claim", mins=hold_min), key=f"claim-{r['asset_id']}", use_container_width=True,
                       icon=":material/directions_walk:"):
            until = claim(r["asset_id"], token, r["walk_min"])
            st.toast(T("claimed", until=_hhmm(until)))
            st.rerun()
        b1, b2, b3 = st.columns(3)
        if b1.button(T("btn_clear"), key=f"c-{r['asset_id']}", use_container_width=True):
            report(r, "Checked - clear")
        if b2.button(T("btn_blocked"), key=f"b-{r['asset_id']}", use_container_width=True):
            report(r, "Blocked")
        if b3.button(T("btn_cant"), key=f"n-{r['asset_id']}", use_container_width=True):
            report(r, "Couldn't reach")
