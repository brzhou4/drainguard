"""Ask DrainGuard: a read-only, rule-based helper (no LLM, no API key).

It answers from direct lookups only: app FAQs, the public drain inventory, the app's historical ranked
drains plus their latest status, and the Open-Meteo forecast at a point. It never writes drain status
and never sends alerts.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone

import requests

from src import forecast as fc
from src.data import NEIGHBORHOOD_CENTERS
from src.geo import haversine_m

INVENTORY_URL = "https://data.sf.gov/resource/jtgq-b7c5.geojson"
SF_BBOX = (37.70, 37.835, -122.53, -122.35)   # lat min, lat max, lon min, lon max
TERRAIN_RADIUS_M = 150

# Extra place names on top of src.data.NEIGHBORHOOD_CENTERS.
EXTRA_PLACES = {
    "San Francisco": (37.7749, -122.4194),
    "Downtown": (37.7880, -122.4075),
    "Richmond": (37.7800, -122.4800),
    "Sunset": NEIGHBORHOOD_CENTERS["Sunset/Parkside"],
    "Bayview": NEIGHBORHOOD_CENTERS["Bayview Hunters Point"],
    "SoMa": NEIGHBORHOOD_CENTERS["South of Market"],
}
PLACES = {**NEIGHBORHOOD_CENTERS, **EXTRA_PLACES}
PLACE_ALIASES = {"sf": "San Francisco", "the city": "San Francisco", "soma": "SoMa", "the mission": "Mission",
                 "mission district": "Mission", "bayview hunters point": "Bayview Hunters Point",
                 "south of market": "South of Market", "outer sunset": "Sunset", "richmond district": "Richmond"}

FAQS = [
    (("forecast score", "report score", "sf311 score", "311 score", "citywide score", "what does the score",
      "forecast mean", "flood forecast"),
     "**Flood Forecast score.** For each three-hour window, the Flood Forecast page shows rain at a central "
     "Mission point, the wettest of five points around the city, and a *citywide SF311 street-flood report "
     "score* from 0 to 100. The score comes from a model trained on archived day-ahead forecasts. It says when "
     "311 flood reports across the city are more likely. It is not a street-level flood probability, a drain "
     "overflow prediction or an official warning."),
    (("risk score", "historical risk", "ranking", "rank mean", "tier", "urgent mean", "how are drains ranked",
      "why is this drain"),
     "**Historical drain ranking.** The Command Center ranks drains using 311 flood reports from *before* "
     "Nov 18. It picks drains, one at a time, that cover the most past storm-day flood incidents within "
     "100 m. Ranks 1–25 are Urgent, 26–100 High and 101–250 Watch. It means \"likely to need checking\", "
     "not \"will clog\". Use this ranking to decide which drains to inspect."),
    (("command center", "how do i use the app", "how to use", "crew"),
     "**Command Center.** The banner shows the National Weather Service outlook. Filter by tier, "
     "neighborhood and status, then click a row in the urgency list to zoom the map to that drain. Use "
     "*Download crew route sheet* for a CSV. The alert buttons there are for crew leads."),
    (("volunteer", "sign up", "neighbor"),
     "**Volunteer page.** Sign up with a first name and area, pick how far you will walk, and the page lists "
     "nearby priority drains that are not checked yet. Look and report only. Never clear drains during a "
     "storm or step into pooled water. Report blocked drains to 311."),
    (("mark", "checked", "check a drain", "status", "mark a drain"),
     "**Marking a drain checked.** On the Command Center, click the drain's row in the urgency list and use "
     "*✓ Clear*, *Blocked* or *Can't reach* (or *Reset to Not checked*). Volunteers use the buttons on the "
     "Volunteer page. I can look up a drain's latest status, but I can't change it."),
    (("layer", "toggle", "map style", "heatmap", "basemap", "ponding layer"),
     "**Map layers.** On the Command Center map, use *Map style* to switch basemaps (streets, USGS topo, "
     "contours, terrain shading, satellite) and the toggles for Neighborhoods, Other drains, Risk heatmap and "
     "Nov 18 reports. On Flood Forecast, the *24-hour modeled ponding scenario* toggle appears only in the "
     "archived replay."),
    (("evidence", "test result", "how accurate", "hit rate", "recall", "baseline"),
     "**Evidence page.** It tests the ranking on the Nov 18–19 storm using only earlier data. It shows the "
     "share of Nov 18–19 flood incidents within 100 m of the top K drains, and the share of top drains that "
     "sat at a Nov 18 flood spot. It compares these with random drains and with ranking by raw past-report "
     "count."),
    (("predict", "can't do", "cannot do", "limit", "limitation", "accurate depth", "how deep", "passable",
      "overflow", "will it flood"),
     "**What DrainGuard can't predict.** It can't predict flood depth, whether a specific drain will overflow "
     "or clog, whether a road is passable, or pipe capacity. 311 reports are resident reports, not measured "
     "floods. The forecast score is citywide report timing, and the terrain layers are screening only. For "
     "emergencies call 911; for official warnings see weather.gov/mtr."),
]

HELP = ("I can answer questions about the app and look things up (read-only). Try:\n"
        "- *What does the forecast score mean?*\n"
        "- A drain ID, like *N-11029*\n"
        "- *Which unchecked drains are in the Mission?*\n"
        "- *Rain near 37.7625, -122.4155* or *Rain in SoMa*")

WEATHER_RE = re.compile(r"\b(rain|raining|rainfall|rainy|weather|wet|precip\w*|drizzle|showers?)\b"
                        r"|\bforecast (for|near|at|in)\b")
UNCHECKED_WORDS = ("unchecked", "not checked", "need checking", "needs checking", "haven't been checked",
                   "still need", "to check in", "drains in", "drains near")


def map_link(lat, lon):
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"


# ---------- Data access (cached in Streamlit when available) ----------
def _cache(ttl):
    try:
        import streamlit as st
        return st.cache_data(ttl=ttl, show_spinner=False)
    except Exception:  # plain Python (tests)
        from functools import lru_cache
        return lru_cache(maxsize=64)


def _fetch_inventory():
    """Paginated snapshot of the DataSF inventory: {asset_id: (lat, lon, neighborhood)}."""
    out, offset, page = {}, 0, 50000
    while True:
        r = requests.get(INVENTORY_URL, params={"$select": "puc_maximo_asset_id,analysis_neighborhood,location",
                                                "$order": "puc_maximo_asset_id", "$limit": page,
                                                "$offset": offset}, timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        for f in feats:
            p, g = f.get("properties") or {}, f.get("geometry") or {}
            aid, coords = p.get("puc_maximo_asset_id"), g.get("coordinates")
            if aid is not None and g.get("type") == "Point" and coords and len(coords) == 2:
                out.setdefault(str(aid), (float(coords[1]), float(coords[0]),
                                          p.get("analysis_neighborhood") or "Unknown"))
        if len(feats) < page:
            return out
        offset += page


inventory = _cache(24 * 3600)(_fetch_inventory)


def _fetch_point(lat, lon):
    return fc.fetch_point(lat, lon)


point_forecast = _cache(15 * 60)(_fetch_point)


def terrain_points():
    if not fc.TERRAIN_GEOJSON.exists():
        return None
    return [tuple(f["geometry"]["coordinates"]) for f in json.loads(fc.TERRAIN_GEOJSON.read_text())["features"]]


def _status_lookup():
    from src.data import latest_status
    s = latest_status()
    if s.empty:
        return {}
    return {str(a): (st, t) for a, st, t in zip(s["asset_id"], s["status"], s.get("time", [""] * len(s)))}


# ---------- Parsing ----------
COORD_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)")
ID_RE = re.compile(r"\b[A-Za-z]{1,4}-?\d{2,8}\b")


def find_coords(text):
    m = COORD_RE.search(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    if abs(a) > 90 and abs(b) <= 90:  # lon, lat order
        a, b = b, a
    return a, b


def in_sf(lat, lon):
    return SF_BBOX[0] <= lat <= SF_BBOX[1] and SF_BBOX[2] <= lon <= SF_BBOX[3]


FOLLOW_OK = {"", "today", "tomorrow", "tonight", "this", "next", "now", "right", "please", "district", "area",
             "neighborhood", "and", "or", "over", "during", "for", "in", "at", "on", "the", "weekend", "later",
             "soon", "morning", "afternoon", "evening", "overnight", "drains", "drain", "s"}


def find_place(text):
    """A known place name in the text, or None. 'Mission Bay' or 'Richmond Heights' are NOT the Mission or the
    Richmond: a place followed by another name word is treated as an unknown location."""
    t = " " + re.sub(r"[^a-z0-9/ ]", " ", text.lower()) + " "
    names = sorted(list(PLACES) + list(PLACE_ALIASES), key=len, reverse=True)
    for n in names:
        key = f" {n.lower()} "
        pos = t.find(key)
        if pos < 0:
            continue
        nxt = t[pos + len(key):].split(" ", 1)[0]
        if nxt not in FOLLOW_OK:
            return None
        return PLACE_ALIASES.get(n.lower(), n)
    return None


def find_drain_id(text, inv):
    """Exact (then case-insensitive) match of a token to an inventory asset ID, as stored."""
    for tok in ID_RE.findall(text):
        if tok in inv:
            return tok
    lower = {k.lower(): k for k in inv} if inv else {}
    for tok in ID_RE.findall(text):
        if tok.lower() in lower:
            return lower[tok.lower()]
    return None


def looks_like_id(text):
    return bool(re.search(r"\b[A-Za-z]{1,4}-\d{2,8}\b", text))


def location_phrase(text):
    m = re.search(r"\b(?:in|near|at|around|for)\s+([A-Za-z][A-Za-z .'/-]{1,40})", text)
    if not m:
        return None
    phrase = m.group(1).strip(" .?!")
    if phrase.lower() in ("the next", "next", "today", "tomorrow", "tonight", "the city"):
        return None
    return phrase


# ---------- Answers ----------
def answer_faq(text):
    t = text.lower().replace("\u2019", "'")
    best, hits = None, 0
    for keys, ans in FAQS:
        n = sum(k in t for k in keys)
        if n > hits:
            best, hits = ans, n
    return best


def answer_drain(aid, inv):
    lat, lon, hood = inv[aid]
    status, when = _status_lookup().get(aid, ("Not checked", ""))
    when = f" (updated {when})" if when else ""
    return (f"**Drain `{aid}`**\n- Neighborhood: {hood}\n- Status: **{status}**{when}\n"
            f"- Location: {lat:.5f}, {lon:.5f} · [Open in Maps]({map_link(lat, lon)})\n\n"
            "Status comes from this app's check buttons. I can't change it here.")


def answer_unchecked(hood_name):
    from src.data import drains_with_status
    d = drains_with_status()
    if "is_fake" in d.columns:
        d = d[~d["is_fake"].astype(str).str.lower().eq("true")]
    hoods = {h.lower(): h for h in d["neighborhood"].dropna().unique()}
    key = hood_name.lower()
    match = hoods.get(key) or next((v for k, v in hoods.items() if key in k or k in key), None)
    if match is None:
        if d.empty:
            return ("The historical drain ranking isn't loaded right now (only placeholder rows are available), "
                    "so I can't list real drains. Try again after the Command Center loads live data.")
        return f"I couldn't find **{hood_name}** among the ranked drains' neighborhoods."
    sel = d[(d["neighborhood"] == match) & (d["status"] == "Not checked")].sort_values("rank").head(5)
    if sel.empty:
        return f"No unchecked ranked drains in **{match}** right now."
    lines = [f"{i}. #{int(r['rank'])} {r['tier']} · `{r['asset_id']}` · [map]({map_link(r['lat'], r['lon'])})"
             for i, (_, r) in enumerate(sel.iterrows(), 1)]
    return (f"**Unchecked ranked drains in {match}** (historical ranking, top {len(sel)}):\n" + "\n".join(lines)
            + "\n\nRanks come from the Command Center's historical ranking.")


def rain_summary(series, now=None):
    """Next 24 complete forecast hours and the wettest three-hour block. Returns dict or None if incomplete."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    top = now.replace(minute=0, second=0, microsecond=0)
    if top < now:
        top += timedelta(hours=1)
    ends = [top + timedelta(hours=k) for k in range(1, 25)]   # each hour (end-1h, end] is entirely in the future
    vals = [series.get(t) for t in ends]
    if any(v is None for v in vals):
        return None
    blocks = [(sum(vals[i:i + 3]), i) for i in range(len(vals) - 2)]
    best, i = max(blocks, key=lambda b: (b[0], -b[1]))
    return {"total": sum(vals), "first_start": ends[0] - timedelta(hours=1), "last_end": ends[-1],
            "block_mm": best, "block_start": ends[i] - timedelta(hours=1), "block_end": ends[i + 2]}


def answer_weather(lat, lon, label):
    try:
        data = point_forecast(round(lat, 5), round(lon, 5))
    except Exception:
        return ("**Forecast unavailable.** I couldn't reach the Open-Meteo forecast service. "
                "Try again in a few minutes.")
    s = rain_summary(data["series"])
    t = "%a %-I %p"
    head = f"**Rain near {label}** ({lat:.4f}, {lon:.4f})\n"
    if s is None:
        body = "- The forecast is missing some of the next 24 hours, so I can't give a total.\n"
    else:
        body = (f"- Next 24 complete hours ({fc.pacific(s['first_start'], t)} – {fc.pacific(s['last_end'], t)} "
                f"Pacific): **{s['total']:.1f} mm**\n")
        if s["block_mm"] > 0:
            body += (f"- Wettest three hours: **{s['block_mm']:.1f} mm**, {fc.pacific(s['block_start'], t)} – "
                     f"{fc.pacific(s['block_end'], t)} Pacific\n")
        else:
            body += "- No rain in any three-hour block.\n"
    body += f"- Retrieved {fc.pacific(data['retrieved_at'])} Pacific (Open-Meteo HRRR/GFS blend)\n"
    pts = terrain_points()
    if pts is not None:
        n = sum(1 for plon, plat in pts if haversine_m(lat, lon, plat, plon) <= TERRAIN_RADIUS_M)
        body += (f"- Static terrain screening: **{n}** potential ponding cell center{'s' if n != 1 else ''} "
                 f"within {TERRAIN_RADIUS_M} m (low spots ≥ 0.25 m below their spill point, from terrain alone)\n")
    body += ("\nThis is rain and terrain context only. It doesn't tell you flood depth, drain overflow or "
             "whether a road is passable.")
    return head + body


def respond(text):
    """Answer one message. Returns Markdown. Read-only: never writes status or sends alerts."""
    text = (text or "").strip()
    if not text:
        return HELP
    low = text.lower().replace("\u2019", "'")
    wants_weather = bool(WEATHER_RE.search(low))

    coords = find_coords(text)
    if coords:
        lat, lon = coords
        if not in_sf(lat, lon):
            return f"{lat}, {lon} is outside San Francisco. Send a latitude, longitude inside the city."
        return answer_weather(lat, lon, "your point")

    inv = None
    if looks_like_id(text):
        try:
            inv = inventory()
        except Exception:
            return "I couldn't reach the DataSF drain inventory right now, so I can't look up that ID."
        aid = find_drain_id(text, inv)
        if aid is None:
            return ("I couldn't find that drain ID in the DataSF inventory. IDs look like `N-11029`. "
                    "Check the ID on the Command Center list.")
        if wants_weather:
            lat, lon, _ = inv[aid]
            return answer_weather(lat, lon, f"drain {aid}")
        return answer_drain(aid, inv)

    place = find_place(text)
    if any(w in low for w in UNCHECKED_WORDS) and "drain" in low and not wants_weather:
        if place is None:
            phrase = location_phrase(text)
            return (f"I don't know the neighborhood **{phrase}**. Try one like Mission or South of Market."
                    if phrase else "Which neighborhood? For example: *Which unchecked drains are in the Mission?*")
        return answer_unchecked(place)

    if wants_weather:
        if place:
            lat, lon = PLACES[place]
            return answer_weather(lat, lon, place)
        phrase = location_phrase(text)
        if phrase:
            return (f"I don't know where **{phrase}** is. Send a latitude, longitude inside San Francisco "
                    "(for example *37.7625, -122.4155*), a drain ID, or a neighborhood like Mission or SoMa.")
        return answer_weather(*PLACES["San Francisco"], "San Francisco")

    faq = answer_faq(text)
    if faq:
        return faq
    return HELP
