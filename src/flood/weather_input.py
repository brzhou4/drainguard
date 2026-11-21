"""Rain input for the flood calculator: today's SF weather, a past date, or rain typed in by a user -> RainEvent.

Sources (all free, no key):
- NWS observations (api.weather.gov/stations/{id}/observations): hourly METAR precipitation. Downtown SF
  (SFOC1) reports no precipitation through this API, so the nearest station with rain data is KSFO
  (SF airport, ~18 km south of downtown), then KOAK.
- NWS gridpoint forecast (api.weather.gov/gridpoints/MTR/x,y): quantitativePrecipitation (mm per interval).
- NOAA GHCN-Daily, SF Downtown USW00023272 (ncei.noaa.gov): daily totals back to 1921.
- Open-Meteo (forecast / archive): fallback only; often rate-limited (HTTP 429).
Every fetch has a timeout and a small on-disk cache under data/processed/ so the app works offline.
"""
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from src.flood import MM_PER_IN, RainEvent
from src.weather import HEADERS, SF_POINT

TZ = ZoneInfo("America/Los_Angeles")
PROCESSED = Path(__file__).resolve().parents[2] / "data" / "processed"
GHCN_CACHE = PROCESSED / "ghcn_sf_daily.csv"
HOURLY_CACHE = PROCESSED / "hourly_rain_cache.json"     # per-day hourly timing + last "today" fetch
GHCN_URL = "https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/USW00023272.csv"
NWS = "https://api.weather.gov"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
# Closest stations first. SFOC1 (downtown) has never returned precipitation via the API, kept in case it starts.
STATIONS = (("SFOC1", "SF Downtown"), ("KSFO", "SF Airport"), ("KOAK", "Oakland Airport"))
NWS_OBS_DAYS = 7          # NWS keeps about a week of observations
TIMEOUT = 20

# NRCS (SCS) Type I 24-hour rainfall distribution: cumulative fraction of the 24-h depth vs hour.
# Type I is the NRCS distribution for coastal Northern California / Pacific maritime climate.
# Source: USDA NRCS TR-55 (1986) / National Engineering Handbook Part 630 Ch. 4, Type I table.
SCS_TYPE_I = np.array([
    (0.0, 0.000), (1.0, 0.017), (2.0, 0.035), (3.0, 0.055), (4.0, 0.076), (5.0, 0.099), (6.0, 0.125),
    (7.0, 0.156), (8.0, 0.194), (9.0, 0.254), (9.5, 0.303), (9.75, 0.362), (10.0, 0.515), (10.5, 0.583),
    (11.0, 0.624), (11.5, 0.654), (12.0, 0.682), (13.0, 0.727), (14.0, 0.767), (15.0, 0.798), (16.0, 0.830),
    (17.0, 0.854), (18.0, 0.878), (19.0, 0.902), (20.0, 0.926), (21.0, 0.944), (22.0, 0.963), (23.0, 0.981),
    (24.0, 1.000)])
SHAPES = ("scs1", "uniform", "front", "burst")
MAX_DEPTH_IN, MIN_DUR_HR, MAX_DUR_HR = 30.0, 0.25, 72.0


# ---------------------------------------------------------------- hyetographs

def _cum_fraction(x, shape):
    """Cumulative fraction of rain fallen at normalised time x in [0, 1]."""
    x = np.clip(np.asarray(x, float), 0, 1)
    if shape == "scs1":       # stretch the 24-h Type I curve over the event duration
        return np.interp(x * 24.0, SCS_TYPE_I[:, 0], SCS_TYPE_I[:, 1])
    if shape == "uniform":
        return x
    if shape == "front":      # rate falls off linearly-squared from the start: ~70% in the first third
        return 1 - (1 - x) ** 3
    if shape == "burst":      # 60% of the depth in the middle 20% of the event, the rest spread evenly
        return np.where(x < 0.4, x * 0.5, np.where(x < 0.6, 0.2 + (x - 0.4) * 3.0, 0.8 + (x - 0.6) * 0.5))
    raise ValueError(f"Unknown shape {shape!r}; use one of {SHAPES}")


def hyetograph(depth_in, duration_hr, shape="scs1", step_min=15):
    """Rain rate per step (mm/h) for `depth_in` inches over `duration_hr`; sum(i)*step/60 == depth_in*25.4."""
    if depth_in < 0 or duration_hr <= 0 or step_min <= 0:
        raise ValueError("depth must be >= 0, duration and step > 0")
    n = max(1, math.ceil(duration_hr * 60 / step_min - 1e-9))
    edges = np.minimum(np.arange(n + 1) * step_min / 60.0, duration_hr) / duration_hr
    frac = np.diff(_cum_fraction(edges, shape))
    frac = frac / frac.sum()
    return frac * depth_in * MM_PER_IN / (step_min / 60.0)


def from_manual(depth_in, duration_hr, shape="scs1", step_min=15):
    """RainEvent from user-typed rain: 0 < depth <= 30 in, 0.25 <= duration <= 72 h."""
    depth_in, duration_hr = float(depth_in), float(duration_hr)
    if not 0 < depth_in <= MAX_DEPTH_IN:
        raise ValueError(f"Rain depth must be more than 0 and at most {MAX_DEPTH_IN:g} inches.")
    if not MIN_DUR_HR <= duration_hr <= MAX_DUR_HR:
        raise ValueError(f"Duration must be between {MIN_DUR_HR:g} and {MAX_DUR_HR:g} hours.")
    names = {"scs1": "typical NorCal storm (NRCS Type I)", "uniform": "steady rain",
             "front": "heaviest at the start", "burst": "short intense burst"}
    return RainEvent(depth_in=depth_in, duration_hr=duration_hr,
                     intensity_mm_hr=hyetograph(depth_in, duration_hr, shape, step_min), step_min=float(step_min),
                     source="manual", label=f"{depth_in:g} in over {duration_hr:g} h ({names.get(shape, shape)})",
                     notes=[f"Rain timing: {names.get(shape, shape)}."])


# ---------------------------------------------------------------- time helpers

def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _iso_dur_s(d):
    """ISO-8601 duration like PT6H, P1D, P1DT6H, PT30M -> seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", d)
    if not m:
        raise ValueError(f"bad duration {d}")
    dd, hh, mm, ss = (int(g or 0) for g in m.groups())
    return dd * 86400 + hh * 3600 + mm * 60 + ss


def _day_bounds(day):
    """Local calendar day (date) -> (start, end) epoch seconds, DST-aware."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    nxt = start.date() + timedelta(days=1)
    return start.timestamp(), datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).timestamp()


def _spread(intervals, t0, t1, step_min):
    """[(start_s, end_s, mm)] -> mm per step on the grid [t0, t1), each amount spread uniformly over its span."""
    step = step_min * 60.0
    n = max(1, math.ceil((t1 - t0) / step - 1e-9))
    e0 = t0 + np.arange(n) * step
    e1 = np.minimum(e0 + step, t1)
    out = np.zeros(n)
    for a, b, mm in intervals:
        if mm <= 0 or b <= a:
            continue
        ov = np.clip(np.minimum(b, e1) - np.maximum(a, e0), 0, None)
        out += mm * ov / (b - a)
    return out


def _clip(intervals, lo, hi):
    """Keep the part of each interval inside [lo, hi], amount scaled by overlap."""
    out = []
    for a, b, mm in intervals:
        ov = min(b, hi) - max(a, lo)
        if ov > 0 and b > a:
            out.append((max(a, lo), min(b, hi), mm * ov / (b - a)))
    return out


def _total(intervals):
    return float(sum(mm for _, _, mm in intervals))


# ---------------------------------------------------------------- NWS fetchers

def _get(url, params=None, timeout=TIMEOUT):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _val(p, key):
    return (p.get(key) or {}).get("value")


def parse_observations(features):
    """NWS observation features -> ([(start_s, end_s, mm)], n_missing_hours, last_obs_s).

    Uses only routine hourly METARs (the report minute that occurs most often, e.g. :56 at KSFO) because
    special reports (SPECI) carry partial accumulations that would double count. Hours whose hourly value is
    null are filled from the 3-h / 6-h totals when those are reported; any still missing count as 0.
    """
    obs = sorted(((_ts(f["properties"]["timestamp"]), f["properties"]) for f in features), key=lambda o: o[0])
    if not obs:
        return [], 0, None
    minutes = pd.Series([datetime.fromtimestamp(t, timezone.utc).minute for t, _ in obs])
    routine_min = int(minutes.mode().iloc[0])
    routine = [(t, p) for t, p in obs if datetime.fromtimestamp(t, timezone.utc).minute == routine_min]
    hourly = {t: _val(p, "precipitationLastHour") for t, p in routine}
    filled = dict(hourly)
    for key, hrs in (("precipitationLast3Hours", 3), ("precipitationLast6Hours", 6)):
        for t, p in routine:
            tot = _val(p, key)
            if tot is None:
                continue
            win = [s for s in filled if t - hrs * 3600 + 600 <= s <= t]
            missing = [s for s in win if filled[s] is None]
            if missing:
                resid = max(0.0, tot - sum(filled[s] for s in win if filled[s] is not None))
                for s in missing:
                    filled[s] = resid / len(missing)
    n_missing = sum(v is None for v in filled.values())
    intervals = [(t - 3600, t, float(v)) for t, v in filled.items() if v]
    return intervals, n_missing, routine[-1][0]


def _observed(t0, t1):
    """Hourly observed rain in [t0, t1] from the nearest NWS station that reports precipitation.

    Returns dict(station, name, intervals, missing, last) or raises RuntimeError.
    """
    start = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = datetime.fromtimestamp(t1, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    errors = []
    for sid, name in STATIONS:
        try:
            feats = _get(f"{NWS}/stations/{sid}/observations", {"start": start, "end": end}).get("features", [])
        except Exception as e:  # noqa: BLE001 - try the next station
            errors.append(f"{sid}: {e}")
            continue
        has_precip = any(_val(f["properties"], k) is not None for f in feats
                         for k in ("precipitationLastHour", "precipitationLast3Hours", "precipitationLast6Hours"))
        # A METAR omits the rain group when it is dry, so all-null at an airport means no rain. The downtown
        # station (SFOC1) never reports rain through the API, so it is only used if it actually has amounts.
        if not feats or (sid == "SFOC1" and not has_precip):
            continue
        iv, miss, last = parse_observations(feats)
        return {"station": sid, "name": name, "intervals": iv, "missing": miss, "last": last,
                "all_null": not has_precip}
    raise RuntimeError("No NWS station returned observations. " + "; ".join(errors))


def _gridpoint_url():
    pt = _get(f"{NWS}/points/{SF_POINT}")["properties"]
    return f"{NWS}/gridpoints/{pt['gridId']}/{pt['gridX']},{pt['gridY']}"


def _forecast_qpf():
    """NWS gridpoint quantitativePrecipitation -> [(start_s, end_s, mm)]."""
    qpf = _get(_gridpoint_url())["properties"]["quantitativePrecipitation"]
    scale = 25.4 if "in" in str(qpf.get("uom", "")).split(":")[-1].lower() else 1.0
    out = []
    for v in qpf["values"]:
        s, d = v["validTime"].split("/")
        a = _ts(s)
        out.append((a, a + _iso_dur_s(d), float(v["value"] or 0) * scale))
    return out


def _open_meteo_hourly(url, params):
    """Open-Meteo hourly precipitation (local time) -> [(start_s, end_s, mm)] (value = rain in the hour before)."""
    js = requests.get(url, params=dict(params, latitude=37.7749, longitude=-122.4194, hourly="precipitation",
                                       timezone="America/Los_Angeles"), timeout=10)
    js.raise_for_status()
    h = js.json()["hourly"]
    out = []
    for t, v in zip(h["time"], h["precipitation"]):
        end = datetime.fromisoformat(t).replace(tzinfo=TZ).timestamp()
        out.append((end - 3600, end, float(v or 0)))
    return out


# ---------------------------------------------------------------- cache

def _cache_read():
    try:
        return json.loads(HOURLY_CACHE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _cache_write(key, value):
    try:
        c = _cache_read()
        c[key] = value
        HOURLY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        HOURLY_CACHE.write_text(json.dumps(c, indent=1))
    except Exception:  # noqa: BLE001 - cache is best effort
        pass


# ---------------------------------------------------------------- event builders

def _event(intervals, t0, t1, step_min, min_hr=1.0):
    """Intervals on a day -> (intensity mm/h, duration_hr, start_s) trimmed to the wet span (>= min_hr)."""
    mm = _spread(intervals, t0, t1, step_min)
    wet = np.flatnonzero(mm > 1e-9)
    if not len(wet):
        n = max(1, round(min_hr * 60 / step_min))
        return np.zeros(n), n * step_min / 60, t0
    i0, i1 = wet[0], wet[-1] + 1
    need = max(1, math.ceil(min_hr * 60 / step_min))
    if i1 - i0 < need:
        i1 = min(len(mm), i0 + need)
        i0 = max(0, i1 - need)
    seg = mm[i0:i1]
    return seg / (step_min / 60.0), len(seg) * step_min / 60.0, t0 + i0 * step_min * 60


def _fmt_day(d):
    return f"{d:%b} {d.day}, {d.year}"


def _hhmm(t):
    return datetime.fromtimestamp(t, TZ).strftime("%-I:%M %p").lower()


def from_today(now=None, step_min=15):
    """Today's SF rain (America/Los_Angeles day): observed so far + NWS forecast for the rest of today."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    day = now.date()
    t0, t1 = _day_bounds(day)
    tn = now.timestamp()
    notes, err = [], []

    obs = None
    try:
        obs = _observed(t0, min(tn, t1))
    except Exception as e:  # noqa: BLE001
        err.append(f"observations: {e}")
    fc = None
    try:
        fc = _forecast_qpf()
    except Exception as e:  # noqa: BLE001
        err.append(f"forecast: {e}")

    provider = "NWS"
    if obs is None and fc is None:
        cached = _cache_read().get("today")
        try:
            iv = _open_meteo_hourly(OPEN_METEO, {"past_days": 1, "forecast_days": 2})
            o_iv, f_iv = _clip(iv, t0, tn), _clip(iv, tn, t1)
            provider = "Open-Meteo"
            notes.append("NWS was unreachable; using the Open-Meteo model (estimates, not gauge readings).")
        except Exception as e:  # noqa: BLE001
            if cached and cached.get("date") == day.isoformat():
                o_iv = [tuple(x) for x in cached["observed"]]
                f_iv = [tuple(x) for x in cached["forecast"]]
                provider = cached.get("provider", "NWS")
                notes.append(f"Weather services unreachable; using data saved at {cached.get('fetched', '?')}.")
            else:
                raise RuntimeError("Couldn't get today's weather (National Weather Service and Open-Meteo are "
                                   "both unreachable). Try again in a few minutes, or type in a rain amount.") from e
    else:
        o_iv, cut = [], tn
        if obs is not None:
            o_iv = _clip(obs["intervals"], t0, t1)
            cut = obs["last"] if obs["last"] else tn
            stn = f"{obs['name']} ({obs['station']})"
            if obs["station"] != "SFOC1":
                notes.append(f"Observed rain is from {stn}, the closest NWS station that reports rainfall "
                             "(the downtown station doesn't publish rain amounts).")
            if obs["all_null"]:
                notes.append(f"{stn} reported no rain amounts today; treated as 0.")
            elif obs["missing"]:
                notes.append(f"{obs['missing']} hourly report(s) at {obs['station']} had no rain amount "
                             "(normally means dry); counted as 0.")
            if obs["last"]:
                notes.append(f"Observed through {_hhmm(obs['last'])}; forecast after that.")
        else:
            notes.append("Observations unavailable; using the forecast only. " + "; ".join(err))
        f_iv = _clip(fc, max(cut, t0), t1) if fc is not None else []
        if fc is None:
            notes.append("NWS forecast unavailable; rest of today assumed dry.")
        _cache_write("today", {"date": day.isoformat(), "observed": o_iv, "forecast": f_iv, "provider": provider,
                               "fetched": now.strftime("%Y-%m-%d %H:%M %Z")})

    obs_in, fc_in = _total(o_iv) / MM_PER_IN, _total(f_iv) / MM_PER_IN
    depth = obs_in + fc_in
    source = "today-forecast" if fc_in > 0 else "today-observed"
    label = f"Today, {_fmt_day(day)} — {obs_in:.2f} in observed + {fc_in:.2f} in forecast ({provider})"
    if depth <= 0:
        notes.insert(0, "No rain observed or forecast today")
        inten, dur = np.zeros(max(1, round(60 / step_min))), 1.0
    else:
        inten, dur, start = _event(o_iv + f_iv, t0, t1, step_min)
        notes.append(f"Rain window starts {_hhmm(start)}.")
    return RainEvent(depth_in=depth, duration_hr=dur, intensity_mm_hr=inten, step_min=float(step_min),
                     source=source, label=label, date=day.isoformat(), notes=notes)


# ---------------------------------------------------------------- NOAA daily record

def load_ghcn(cache=True, max_age_days=1):
    """NOAA GHCN-Daily SF Downtown (USW00023272): DataFrame[date datetime64, prcp_in float], NaN days dropped.

    cache=True reads data/processed/ghcn_sf_daily.csv if younger than max_age_days; otherwise downloads
    (~7.5 MB) and rewrites the cache. On download failure falls back to the cache at any age.
    """
    fresh = GHCN_CACHE.exists() and (time.time() - GHCN_CACHE.stat().st_mtime) < max_age_days * 86400
    if cache and fresh:
        return _read_ghcn_cache()
    try:
        r = requests.get(GHCN_URL, timeout=180)
        r.raise_for_status()
        from io import StringIO
        raw = pd.read_csv(StringIO(r.text), usecols=["DATE", "PRCP"], dtype={"PRCP": float})
        df = pd.DataFrame({"date": pd.to_datetime(raw["DATE"]), "prcp_in": raw["PRCP"] / 254.0})
        df = df.dropna().sort_values("date").reset_index(drop=True)
        GHCN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.assign(prcp_in=df.prcp_in.round(3)).to_csv(GHCN_CACHE, index=False, date_format="%Y-%m-%d")
        return _read_ghcn_cache()
    except Exception as e:  # noqa: BLE001
        if GHCN_CACHE.exists():
            return _read_ghcn_cache()
        raise RuntimeError("Couldn't download the NOAA daily rain record for SF and no saved copy exists.") from e


def _read_ghcn_cache():
    df = pd.read_csv(GHCN_CACHE, parse_dates=["date"])
    df["prcp_in"] = df["prcp_in"].astype(float)
    return df


def _hourly_for_day(day, now_s=None):
    """Real hourly timing for a past local day: cache, NWS observations (last ~7 days), Open-Meteo archive.

    Returns dict(intervals, source_text) or None.
    """
    key = f"day:{day.isoformat()}"
    hit = _cache_read().get(key)
    if hit:
        return {"intervals": [tuple(x) for x in hit["intervals"]], "source": hit["source"],
                "station": hit.get("station"), "missing": hit.get("missing", 0)}
    t0, t1 = _day_bounds(day)
    now_s = now_s or time.time()
    if t1 > now_s:
        return None   # day not finished: don't cache
    res = None
    if now_s - t0 < NWS_OBS_DAYS * 86400:
        try:
            o = _observed(t0, t1 + 3600)
            if o["intervals"] or o["all_null"]:
                res = {"intervals": _clip(o["intervals"], t0, t1), "station": o["station"], "missing": o["missing"],
                       "source": f"NWS hourly observations at {o['name']} ({o['station']})"}
        except Exception:  # noqa: BLE001
            res = None
    if res is None:
        try:
            iv = _open_meteo_hourly(OPEN_METEO_ARCHIVE, {"start_date": day.isoformat(), "end_date": day.isoformat()})
            res = {"intervals": _clip(iv, t0, t1), "station": None, "missing": 0,
                   "source": "Open-Meteo hourly reanalysis (ERA5)"}
        except Exception:  # noqa: BLE001
            res = None
    if res is not None:
        _cache_write(key, res)
    return res


def from_date(date, step_min=15, ghcn=None):
    """RainEvent for a past SF day. Depth from NOAA GHCN-Daily SF Downtown; real hourly timing when available.

    Days newer than the GHCN record (it lags ~1 week) use the NWS hourly gauge total instead, source "nws-observed".
    """
    day = pd.Timestamp(date).date()
    if ghcn is None:
        ghcn = load_ghcn()
    row = ghcn.loc[ghcn["date"].dt.date == day, "prcp_in"]
    t0, t1 = _day_bounds(day)
    notes = []
    if len(row) and pd.notna(row.iloc[0]):
        depth = float(row.iloc[0])
        source, label = "noaa-daily", f"{_fmt_day(day)} at SF Downtown (NOAA GHCN): {depth:.2f} in"
        hourly = _hourly_for_day(day) if depth > 0 else None
        if hourly and _total(hourly["intervals"]) > 0:
            scale = depth * MM_PER_IN / _total(hourly["intervals"])
            iv = [(a, b, mm * scale) for a, b, mm in hourly["intervals"]]
            inten, dur, start = _event(iv, t0, t1, step_min)
            notes.append(f"Hourly timing from {hourly['source']}, scaled to the downtown daily total.")
        else:
            dur = 24.0
            inten = hyetograph(depth, dur, "scs1", step_min) if depth > 0 else np.zeros(int(24 * 60 / step_min))
            notes.append("Daily total only: hourly timing assumed (NRCS Type I).")
    else:
        last = ghcn["date"].max()
        hourly = _hourly_for_day(day) if last is not None and pd.Timestamp(day) > last else None
        if not hourly or hourly.get("station") is None:
            span = f"{ghcn['date'].min():%b %-d, %Y} – {last:%b %-d, %Y}" if len(ghcn) else "empty"
            raise ValueError(f"No rain record for {day.isoformat()} at SF Downtown (NOAA record covers {span}).")
        depth = _total(hourly["intervals"]) / MM_PER_IN
        source = "nws-observed"
        label = f"{_fmt_day(day)} at {_station_name(hourly['station'])} (NWS hourly gauge): {depth:.2f} in"
        notes.append(f"NOAA's downtown daily record isn't published for this date yet (it runs through "
                     f"{_fmt_day(last)}); using {hourly['source']}.")
        if hourly["station"] != "SFOC1":
            notes.append("The airport gauge is ~18 km south of downtown; city totals can differ.")
        if hourly.get("missing"):
            notes.append(f"{hourly['missing']} hourly report(s) had no rain amount (normally means dry); counted as 0.")
        if depth > 0:
            inten, dur, _ = _event(hourly["intervals"], t0, t1, step_min)
        else:
            inten, dur = np.zeros(max(1, round(60 / step_min))), 1.0
            notes.insert(0, "No rain recorded that day")
    return RainEvent(depth_in=depth, duration_hr=dur, intensity_mm_hr=inten, step_min=float(step_min),
                     source=source, label=label, date=day.isoformat(), notes=notes)


def _station_name(sid):
    return dict(STATIONS).get(sid, sid) + f" ({sid})"


def notable_storms(n=10, ghcn=None, include=("2026-11-18",)):
    """The n wettest days on record (date, prcp_in, source) for a "pick a famous storm" list, wettest first.

    Dates in `include` are always added: from GHCN if present, else from the saved NWS hourly gauge total.
    """
    if ghcn is None:
        ghcn = load_ghcn()
    top = ghcn.nlargest(n, "prcp_in")[["date", "prcp_in"]].assign(source="noaa-daily")
    cache = _cache_read()
    extra = []
    for d in include:
        ts = pd.Timestamp(d)
        if (top["date"] == ts).any():
            continue
        hit = ghcn.loc[ghcn["date"] == ts]
        if len(hit):
            extra.append({"date": ts, "prcp_in": float(hit["prcp_in"].iloc[0]), "source": "noaa-daily"})
        elif f"day:{ts.date().isoformat()}" in cache:
            c = cache[f"day:{ts.date().isoformat()}"]
            if c.get("station"):
                extra.append({"date": ts, "prcp_in": _total(c["intervals"]) / MM_PER_IN, "source": "nws-observed"})
    if extra:
        top = pd.concat([top.iloc[: max(0, n - len(extra))], pd.DataFrame(extra)], ignore_index=True)
    top = top.sort_values("prcp_in", ascending=False).reset_index(drop=True)
    top["label"] = [f"{_fmt_day(d)} — {p:.2f} in" + (" (SF Airport gauge)" if s == "nws-observed" else "")
                    for d, p, s in zip(top["date"], top["prcp_in"], top["source"])]
    return top
