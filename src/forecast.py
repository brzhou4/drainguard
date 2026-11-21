"""Citywide SF311 street-flood report score from Open-Meteo hourly rain (HRRR/GFS blend).

The score is a frozen logistic regression trained on archived day-ahead forecasts. It estimates how
likely a three-hour window is to produce citywide SF311 street-flood reports. It is NOT a calibrated
street-level flood probability and NOT an official warning. Which drains to inspect still comes from
the historical ranking on the Command Center.

Rain convention: Open-Meteo's hourly precipitation at timestamp T is the rain in the hour ENDING at T.
A window ending at E therefore uses the values stamped E-2h, E-1h and E.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
REPLAY_FIXTURE = ROOT / "data" / "fixtures" / "open_meteo_previous_day1_2026-11-18_20.json"
PONDING_GEOJSON = ROOT / "data" / "processed" / "ponding_nov19_scenario.geojson"
TERRAIN_GEOJSON = ROOT / "data" / "processed" / "terrain_depressions.geojson"
PONDING_SUMMARY = ROOT / "data" / "processed" / "ponding_build_summary.json"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
HOUR = timedelta(hours=1)
WINDOW_HOURS = 3
N_WINDOWS = 8

# The first point is the central (Mission) point used for the citywide score.
POINTS = [
    (37.76328, -122.41286),
    (37.77484, -122.48776),
    (37.78207, -122.45487),
    (37.72286, -122.46946),
    (37.73730, -122.40375),
]
POINT_NAMES = ["Point 1 (central)", "Point 2", "Point 3", "Point 4", "Point 5"]

FORECAST_PARAMS = {
    "models": "gfs_hrrr", "hourly": "precipitation", "precipitation_unit": "mm",
    "timezone": "GMT", "past_days": 1, "forecast_days": 3,
}

# ---- Frozen model (do not edit: these are the trained parameters) ----
FEATURES = [
    "rain", "peak", "rain_squared", "peak_squared",
    "rain_prior_6h", "rain_prior_24h",
    "missing_prior_6h", "missing_prior_24h",
    "hour_sin", "hour_cos", "season_sin", "season_cos",
]
CENTER = [
    0.07270850838370385, 0.061725124293340886,
    0.11368572941984505, 0.08199819504423402,
    0.120528624607038, 0.31922507464666033,
    0.0006021375884389584, 0.0024085503537558334,
    -1.4857506518975085e-05, -0.0004086124819154503,
    0.09937565324466456, 0.040612977492506794,
]
SCALE = [
    0.32924034113161454, 0.2796215372162981,
    0.6903009476928579, 0.5145835447160023,
    0.45167526666449986, 0.8071336979210062,
    0.024531103089008188, 0.049017846127193856,
    0.7070022399915052, 0.7072111887280123,
    0.7172505589750094, 0.6884959703959246,
]
COEFFICIENTS = [
    -1.5932638626530762,
    -0.2154603564716711, 0.2548822437538072,
    0.3447229255414343, -0.2904260669260262,
    0.1462330089452231, 0.3489589561980208,
    -0.007301762009911806, 0.011983440677604961,
    -0.5945267313059641, -0.5141795453515742,
    0.029300829993767336, 0.05833167207420855,
]
assert len(FEATURES) == len(CENTER) == len(SCALE) == len(COEFFICIENTS) - 1


# ---------- Parsing ----------
def parse_time(s):
    """Open-Meteo GMT timestamp 'YYYY-MM-DDTHH:MM' -> aware UTC datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)


def to_series(location, variable="precipitation"):
    """One Open-Meteo location payload -> {utc datetime: mm or None}."""
    h = location["hourly"]
    return {parse_time(t): (None if v is None else float(v)) for t, v in zip(h["time"], h[variable])}


def _as_list(payload):
    return payload if isinstance(payload, list) else [payload]


# ---------- Fetching ----------
def _coord_params():
    return {"latitude": ",".join(str(p[0]) for p in POINTS),
            "longitude": ",".join(str(p[1]) for p in POINTS)}


def fetch_live(timeout=20):
    """Live forecast for the five points. Raises on any failure (callers show the unavailable state).
    Returns {"series": [5 dicts], "retrieved_at": aware UTC datetime, "source": "live"}."""
    r = requests.get(FORECAST_URL, params={**_coord_params(), **FORECAST_PARAMS}, timeout=timeout)
    r.raise_for_status()
    locs = _as_list(r.json())
    if len(locs) != len(POINTS):
        raise ValueError(f"expected {len(POINTS)} locations, got {len(locs)}")
    return {"series": [to_series(loc) for loc in locs], "retrieved_at": datetime.now(UTC), "source": "live"}


def fetch_point(lat, lon, timeout=20):
    """Live forecast for one point (used by Ask DrainGuard). Raises on failure."""
    params = {**FORECAST_PARAMS, "latitude": lat, "longitude": lon}
    r = requests.get(FORECAST_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return {"series": to_series(_as_list(r.json())[0]), "retrieved_at": datetime.now(UTC)}


def load_replay():
    """Archived Nov 18-20 previous-day-1 forecast fixture, or None if it is missing."""
    if not REPLAY_FIXTURE.exists():
        return None
    fx = json.loads(REPLAY_FIXTURE.read_text())
    series = [{parse_time(t): (None if v is None else float(v)) for t, v in zip(fx["time"], vals)}
              for vals in fx["precipitation_mm"]]
    return {"series": series, "retrieved_at": datetime.fromisoformat(fx["retrieved_at_utc"]),
            "source": "replay", "replay_start": parse_time(fx["replay_start_utc"]), "meta": fx}


# ---------- Windows ----------
def next_boundary(now, hours=WINDOW_HOURS):
    """The next UTC time on a multiple of `hours` (now itself if it is exactly on one)."""
    now = now.astimezone(UTC)
    floor = now.replace(minute=0, second=0, microsecond=0)
    floor -= timedelta(hours=floor.hour % hours)
    return floor if floor == now else floor + timedelta(hours=hours)


def _values(series, end, n):
    """The n hourly values ending at `end` (inclusive), oldest first; None where missing."""
    return [series.get(end - k * HOUR) for k in range(n - 1, -1, -1)]


def window_total(series, end, n=WINDOW_HOURS):
    vals = _values(series, end, n)
    return None if any(v is None for v in vals) else sum(vals)


def features(series, end):
    """Feature vector for the window ending at `end`, or None if a window hour is missing."""
    win = _values(series, end, WINDOW_HOURS)
    if any(v is None for v in win):
        return None
    rain, peak = math.log1p(sum(win)), math.log1p(max(win))
    prior = {}
    for L in (6, 24):
        vals = _values(series, end - WINDOW_HOURS * HOUR, L)  # L hours ending at E-3h
        if any(v is None for v in vals):
            prior[L] = (0.0, 1.0)
        else:
            prior[L] = (math.log1p(sum(vals)), 0.0)
    local = end.astimezone(PACIFIC)
    h = 2 * math.pi * local.hour / 24
    s = 2 * math.pi * local.timetuple().tm_yday / 365.25
    return [rain, peak, rain ** 2, peak ** 2, prior[6][0], prior[24][0], prior[6][1], prior[24][1],
            math.sin(h), math.cos(h), math.sin(s), math.cos(s)]


def score(x):
    """Sigmoid of the frozen logistic model; None passes through."""
    if x is None:
        return None
    z = COEFFICIENTS[0] + sum(c * (v - m) / sc for c, v, m, sc in zip(COEFFICIENTS[1:], x, CENTER, SCALE))
    return 1 / (1 + math.exp(-z))


def windows(series_list, start, n=N_WINDOWS):
    """n consecutive three-hour windows starting at `start` (UTC). Each row:
    start, end (UTC), central_mm, max_point_mm, max_point (index), score; None = unavailable."""
    rows = []
    for i in range(n):
        s = start + i * WINDOW_HOURS * HOUR
        e = s + WINDOW_HOURS * HOUR
        totals = [window_total(ser, e) for ser in series_list]
        known = [t for t in totals if t is not None]
        max_mm = max(known) if len(known) == len(totals) and known else None
        rows.append({
            "start": s, "end": e, "central_mm": totals[0],
            "max_point_mm": max_mm,
            "max_point": totals.index(max_mm) if max_mm is not None else None,
            "score": score(features(series_list[0], e)),
        })
    return rows


def pacific(dt, fmt="%a %b %-d, %-I:%M %p"):
    return dt.astimezone(PACIFIC).strftime(fmt)


def window_label(row):
    s, e = row["start"].astimezone(PACIFIC), row["end"].astimezone(PACIFIC)
    same_day = s.date() == e.date()
    return (f"{s.strftime('%a %b %-d, %-I %p')}–{e.strftime('%-I %p') if same_day else e.strftime('%a %-I %p')}"
            .replace(":00", ""))
