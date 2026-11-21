"""Load the drain rankings and read/write volunteer data (via src/store.py: Supabase or local CSVs)."""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src import store

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DRAINS_CSV = PROCESSED / "drain_scores.csv"
RESULTS_CSV = PROCESSED / "results_table.csv"
RESULTS_JSON = PROCESSED / "results.json"

TZ = ZoneInfo("America/Los_Angeles")
TIERS = ["Urgent", "High", "Watch"]
TIER_COLORS = {"Urgent": "#7B2CBF", "High": "#D64545", "Watch": "#E8892B"}  # purple, red, orange
CHECKED_COLOR = "#2E9E5B"   # green = checked
OTHER_DRAIN_COLOR = "#9AA0A6"  # gray = every other mapped drain
EN_ROUTE = "Volunteer en route"
STATUSES = ["Not checked", EN_ROUTE, "Checked - clear", "Blocked", "Blocked - reported to 311", "Couldn't reach"]
DONE_STATUSES = {"Checked - clear", "Blocked", "Blocked - reported to 311"}  # someone actually looked at it
OPEN_STATUSES = {"Not checked", "Couldn't reach"}                            # still needs a visit
ROUND_ID = "__ROUND__"   # a status row with this asset_id marks "new check round started" (resets all checks)
VOLUNTEER = "Volunteer"  # volunteers are anonymous: every report is logged under this label
EN_ROUTE_BUFFER_MIN = 20  # an en-route drain stays hidden for the walk there + this many minutes
CONDITIONS = ["Leaves", "Trash", "Sediment / dirt", "Broken or missing grate", "Standing water", "Other"]

# SF 311 "Flooding, sewer and water leak issues" form. The city site can't be embedded (it blocks framing),
# so we open it in a new tab with the category picked, the description written and the drain's location set.
# The form only takes a location from the link when address, latitude and longitude are all given; it then
# skips its map step and checks the spot is inside SF itself ("Out of service area" otherwise). With the
# "Catch basin" type set too, the link opens on the last step (optional contact info), ready to submit.
SF311_SEWER_FORM = "https://mobile311.sfgov.org/tickets/new"


def sf311_description(drain):
    """The text a volunteer sends to the city for a blocked drain. Kept in English for city staff."""
    lat, lon = round(float(drain["lat"]), 6), round(float(drain["lon"]), 6)
    hood = drain.get("neighborhood")
    where = f", {hood}" if isinstance(hood, str) and hood.strip() else ""
    return (f"Blocked storm drain (catch basin) at {lat}, {lon}{where}. "
            f"Map: https://www.google.com/maps?q={lat},{lon} . "
            f"Reported by a DrainGuard volunteer, drain ID {drain['asset_id']}. "
            "This drain is on the flood-risk list; please clear it before the next storm.")


def sf311_location_label(drain):
    """What the city form shows as the address: we have no street address, so use the drain's coordinates."""
    lat, lon = round(float(drain["lat"]), 6), round(float(drain["lon"]), 6)
    hood = drain.get("neighborhood")
    where = f" ({hood})" if isinstance(hood, str) and hood.strip() else ""
    return f"Storm drain at {lat}, {lon}{where}"


def sf311_report_url(drain):
    """Link to the SF 311 sewer form with category, type, description and location all filled in."""
    from urllib.parse import urlencode
    lat, lon = round(float(drain["lat"]), 6), round(float(drain["lon"]), 6)
    return SF311_SEWER_FORM + "?" + urlencode({
        "submission[ticket_type_code]": "input:Sewer",
        "submission[details][spot:description]": sf311_description(drain),
        "submission[details][spot:location][address]": sf311_location_label(drain),
        "submission[details][spot:location][latitude]": lat,
        "submission[details][spot:location][longitude]": lon,
        "submission[details][input.puc_sewer.Nature_of_request]": "catch_basin",
    })

# Rough neighborhood centers, used when a volunteer picks a neighborhood instead of clicking the map.
NEIGHBORHOOD_CENTERS = {
    "Mission": (37.7625, -122.4155),
    "Bayview Hunters Point": (37.7310, -122.3900),
    "Sunset/Parkside": (37.7500, -122.4950),
    "Inner Sunset": (37.7600, -122.4680),
    "South of Market": (37.7780, -122.4030),
    "Haight Ashbury": (37.7700, -122.4470),
    "Excelsior": (37.7240, -122.4280),
    "Bernal Heights": (37.7410, -122.4150),
}


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def stamp():
    """Timestamp stored with each row (to the millisecond, so a new round cleanly splits before/after)."""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def now():
    return pd.Timestamp(datetime.now(TZ).replace(tzinfo=None))


def _ts(series):
    return pd.to_datetime(series, errors="coerce")


def setting(name, default=""):
    """Read a setting from Streamlit secrets (Streamlit Cloud) or environment variables (local .env)."""
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    return os.environ.get(name, default)


def _live_run():
    """Download + rank from public data once, cached for 6 hours on the server."""
    import streamlit as st

    @st.cache_data(ttl=6 * 3600, show_spinner="Downloading SF flood reports and storm drains (first load takes ~30 s)...")
    def _run(code_version):  # code_version: re-run automatically when live.py changes
        from src import live
        return live.run()

    import hashlib
    version = hashlib.md5((ROOT / "src" / "live.py").read_bytes()).hexdigest()
    return _run(version)


def _write_live_outputs(results, summary, test_incidents):
    import json
    PROCESSED.mkdir(parents=True, exist_ok=True)
    if len(results):
        pct = results.copy()
        for c in pct.columns[1:]:
            pct[c] = (pct[c] * 100).round(1).astype(str) + "%"
        pct.to_csv(RESULTS_CSV, index=False)
        k25 = results[results["K drains"] == 25]
        hit_col = "Hit rate: DrainGuard drains at a Nov 18 flood spot"
        if len(k25) and hit_col in k25.columns:
            headline = f"{k25.iloc[0][hit_col]:.0%} hit"
            RESULTS_JSON.write_text(json.dumps({"headline": headline, **summary}))
    else:
        RESULTS_JSON.write_text(json.dumps({"headline": "no Nov 18 data", **summary}))
    test_incidents.to_csv(PROCESSED / "nov18_incidents.csv", index=False)


def load_all():
    """Returns (ranked drains, all drains, source) where source is 'file', 'live' or 'fake'."""
    empty = pd.DataFrame(columns=["asset_id", "lat", "lon"])
    if DRAINS_CSV.exists():
        d = pd.read_csv(DRAINS_CSV)
        if "is_fake" in d.columns and not d["is_fake"].astype(str).str.lower().eq("true").all():
            return d.sort_values("rank").reset_index(drop=True), empty, "file"
    try:
        ranked, inlets, results, summary, test, _cells = _live_run()
        _write_live_outputs(results, summary, test)
        return ranked, inlets, "live"
    except Exception as e:  # network down etc.: fall back to the fake starter data
        LAST_ERROR["live"] = repr(e)
        if DRAINS_CSV.exists():
            d = pd.read_csv(DRAINS_CSV)
            d["is_fake"] = True
            return d.sort_values("rank").reset_index(drop=True), empty, "fake"
        return pd.DataFrame(columns=["asset_id", "lat", "lon", "neighborhood", "risk", "rank", "tier",
                                     "reason", "is_fake"]), empty, "fake"


LAST_ERROR = {}


def load_drains():
    return load_all()[0]


def all_drains():
    return load_all()[1]


def data_source():
    return load_all()[2]


def neighborhoods_geojson():
    """SF's 41 Analysis Neighborhoods (DataSF j2bu-swwd), cached for a day. None if unavailable."""
    import streamlit as st

    @st.cache_data(ttl=24 * 3600, show_spinner=False)
    def _get():
        import requests
        r = requests.get("https://data.sf.gov/resource/j2bu-swwd.geojson", params={"$limit": 100}, timeout=60)
        r.raise_for_status()
        return r.json()

    try:
        return _get()
    except Exception:
        return None


def flood_cells():
    """Flood-risk % squares from the live run (empty if live data is unavailable)."""
    try:
        return _live_run()[5]
    except Exception:
        return pd.DataFrame()


def check_ttl_days():
    """A check counts as current for this many days (setting CHECK_TTL_DAYS, default 7)."""
    try:
        return max(1, int(setting("CHECK_TTL_DAYS", "7")))
    except ValueError:
        return 7


def round_start():
    """When the crew last started a new check round, or None."""
    s = store.read("drain_status")
    t = _ts(s.loc[s["asset_id"] == ROUND_ID, "time"]).dropna()
    return t.max() if len(t) else None


def check_cutoff():
    """Checks logged before this time no longer count (older than the TTL, or before the current round)."""
    cutoff = now() - pd.Timedelta(days=check_ttl_days())
    r = round_start()
    return max(cutoff, r) if r is not None else cutoff


def start_new_round(by="City crew"):
    store.append("drain_status", {"asset_id": ROUND_ID, "status": "Round start", "volunteer": by, "time": stamp()})


def load_status():
    """Every drain report, without round markers, with a parsed time column 't'."""
    s = store.read("drain_status")
    s = s[s["asset_id"] != ROUND_ID].copy()
    s["t"] = _ts(s["time"])
    return s


def current_reports():
    """Reports that still count: newer than the TTL and after the current round started."""
    s = load_status()
    r = round_start()
    keep = s["t"] >= now() - pd.Timedelta(days=check_ttl_days())
    if r is not None:
        keep &= s["t"] > r
    return s[keep]


def latest_status():
    """One row per drain: its current status, who/when, and how many failed tries since the last real check."""
    s = current_reports()
    cols = ["asset_id", "status", "volunteer", "time", "condition", "note", "photo", "attempts"]
    if s.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for _, g in s.sort_values("t", kind="stable").groupby("asset_id", sort=False):
        last = g.iloc[-1].to_dict()
        tail = 0  # failed tries since the last non-"Couldn't reach" report
        for st_ in reversed(g["status"].tolist()):
            if st_ != "Couldn't reach":
                break
            tail += 1
        last["attempts"] = tail
        out.append(last)
    return pd.DataFrame(out)[cols]


def drains_with_status():
    """Every ranked drain with its current status. Open drains someone is walking to show as EN_ROUTE,
    with 'en_route_until' and 'en_route_token' (the walker's private token, never shown)."""
    d = load_drains().copy()
    s = latest_status().rename(columns={"volunteer": "checked_by", "time": "checked_at"})
    extra = ["status", "checked_by", "checked_at", "condition", "note", "photo", "attempts"]
    if s.empty:
        for c in extra:
            d[c] = ""
        d["status"], d["attempts"] = "Not checked", 0
    else:
        d = d.merge(s[["asset_id"] + extra], on="asset_id", how="left")
        d["status"] = d["status"].fillna("Not checked")
        d["attempts"] = d["attempts"].fillna(0).astype(int)
        d[extra[1:-1]] = d[extra[1:-1]].fillna("")
    d["en_route_until"], d["en_route_token"] = "", ""
    c = active_claims()
    if len(c):
        c = c.sort_values("until").drop_duplicates("asset_id", keep="last").set_index("asset_id")
        hit = d["asset_id"].isin(c.index) & d["status"].isin(OPEN_STATUSES)
        d.loc[hit, "en_route_until"] = d.loc[hit, "asset_id"].map(c["until"])
        d.loc[hit, "en_route_token"] = d.loc[hit, "asset_id"].map(c["token"])
        d.loc[hit, "status"] = EN_ROUTE
    return d


def log_status(asset_id, status, volunteer=VOLUNTEER, token="", condition="", note="", photo=""):
    store.append("drain_status", {"asset_id": asset_id, "status": status, "volunteer": volunteer,
                                  "volunteer_token": token, "time": stamp(), "condition": condition,
                                  "note": note, "photo": photo})
    if token:
        release_claim(asset_id, token)


# ---------- En route: a volunteer is walking to a drain ----------
def active_claims():
    c = store.read("claims")
    if c.empty:
        return c
    return c[_ts(c["until"]) > now()]


def claim(asset_id, token, walk_min):
    """Hide this drain from everyone else for the walk there plus EN_ROUTE_BUFFER_MIN minutes."""
    release_claim(asset_id, token)
    until = now() + pd.Timedelta(minutes=int(walk_min) + EN_ROUTE_BUFFER_MIN)
    store.append("claims", {"asset_id": asset_id, "token": token, "until": until.strftime("%Y-%m-%d %H:%M:%S")})
    return until


def release_claim(asset_id, token):
    store.delete("claims", asset_id=asset_id, token=token)


def open_for_volunteers(drains, token=None):
    """Urgent/High drains that still need a visit. Drains someone else is walking to are left out;
    drains this volunteer is walking to stay in."""
    d = drains[drains["tier"].isin(["Urgent", "High"])]
    mine = (d["status"] == EN_ROUTE) & (d["en_route_token"] == (token or "\0"))
    return d[d["status"].isin(OPEN_STATUSES) | mine]


def visible_on_maps(drains, token=None):
    """Drains to draw on a map: everything except drains someone else is walking to."""
    hidden = (drains["status"] == EN_ROUTE) & (drains["en_route_token"] != (token or "\0"))
    return drains[~hidden]


# ---------- Volunteers (anonymous: no names, just a private token) ----------
def load_volunteers():
    v = store.read("volunteers")
    if len(v):
        v.loc[v["token"] == "", "token"] = v.loc[v["token"] == "", "topic"]  # rows from before tokens existed
        for c in ("lat", "lon", "radius_m"):
            v[c] = pd.to_numeric(v[c], errors="coerce")
        v = v.drop_duplicates("token", keep="last")
    return v


def get_volunteer(token):
    if not token:
        return None
    v = load_volunteers()
    hit = v[v["token"] == token]
    return hit.iloc[-1] if len(hit) else None


def save_volunteer(lat, lon, radius_m, lang="en", adult="yes", token=None, topic=None):
    """Create (or, with token, update) an anonymous volunteer. Returns the private token."""
    import secrets
    token = token or secrets.token_urlsafe(9)
    topic = topic or f"drainguard-vol-{secrets.token_hex(5)}"
    store.delete("volunteers", token=token)
    store.append("volunteers", {"token": token, "topic": topic, "lat": round(float(lat), 6),
                                "lon": round(float(lon), 6), "radius_m": int(radius_m), "lang": lang,
                                "adult": adult, "created": stamp()})
    return token


def volunteer_stats(token):
    """(drains this volunteer has checked ever, checked in the current round)."""
    s = load_status()
    mine = s[(s["volunteer_token"] == token) & s["status"].isin(DONE_STATUSES)]
    current = set(current_reports().index)
    return len(mine), int(mine.index.isin(current).sum())
