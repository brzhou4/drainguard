"""How common is this storm? NOAA Atlas 14 return periods + the SF Downtown daily record.

- atlas14_table(): NOAA Atlas 14 Vol 6 (partial-duration series) point depths for SF, fetched from
  NOAA's Precipitation Frequency Data Server (PFDS) and cached to data/processed/atlas14_sf.csv.
- return_period(): log-log interpolation of that table (between durations and between return periods).
- critical_window(): the part of the storm (15 min ... 48 h, or the whole event) that is rarest.
- empirical(): how often the downtown gauge (GHCN daily) has seen a day at least this wet.
- commonality(): all of the above as a contract `Commonality` with a plain-English sentence.
"""
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.flood import MM_PER_IN, Commonality

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
CACHE_CSV = PROCESSED / "atlas14_sf.csv"

SF_LAT, SF_LON = 37.7749, -122.4194
PFDS_URL = "https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text_mean.csv"
METHOD = "NOAA Atlas 14 (NOAA PFDS), SF Downtown"
RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
WINDOWS_HR = [0.25, 0.5, 1, 2, 3, 6, 12, 24, 48]    # standard durations scanned in a hyetograph
MIN_T = 0.5                                          # floor: "several times a year"
DAILY_COMPARABLE_MAX_HR = 30                         # daily-gauge comparison only means much for <= ~1 day


def _duration_hours(label):
    """'5-min' -> 5/60, '2-hr' -> 2, '3-day' -> 72."""
    m = re.fullmatch(r"\s*(\d+)-(min|hr|day)\s*", label)
    if not m:
        raise ValueError(f"unknown duration label {label!r}")
    n, unit = int(m.group(1)), m.group(2)
    return n / 60 if unit == "min" else n if unit == "hr" else n * 24.0


def parse_pfds(text):
    """Parse PFDS fe_text_mean.csv text -> DataFrame (index duration_hr, int return-period columns)."""
    lines = text.splitlines()
    try:
        i = next(k for k, ln in enumerate(lines) if ln.lower().startswith("by duration for ari"))
    except StopIteration:
        raise ValueError("PFDS text has no 'by duration for ARI (years)' block") from None
    rps = [int(x) for x in lines[i].split(":", 1)[1].strip(" ,").split(",")]
    rows = {}
    for ln in lines[i + 1:]:
        if not ln.strip() or ":" not in ln:
            break
        lab, vals = ln.split(":", 1)
        rows[_duration_hours(lab)] = [float(v) for v in vals.strip(" ,").split(",")]
    if not rows:
        raise ValueError("PFDS depth block is empty")
    df = pd.DataFrame.from_dict(rows, orient="index", columns=rps).sort_index()
    df.index.name = "duration_hr"
    return df


def _load_csv(path):
    df = pd.read_csv(path, index_col="duration_hr")
    df.columns = [int(c) for c in df.columns]
    df.index = np.round(df.index.values * 60) / 60   # durations are whole minutes; undo CSV rounding
    df.index.name = "duration_hr"
    return df


def atlas14_table(lat=SF_LAT, lon=SF_LON, cache=True):
    """NOAA Atlas 14 PDS depth table (inches): index duration in hours, columns return period (years).

    Loads the on-disk cache when cache=True; otherwise (or if no cache) fetches PFDS and writes it.
    If the fetch fails, falls back to the cache; raises if there is none.
    """
    path = CACHE_CSV if (round(lat, 4), round(lon, 4)) == (SF_LAT, SF_LON) else \
        PROCESSED / f"atlas14_{lat:.4f}_{lon:.4f}.csv"
    if cache and path.exists():
        return _load_csv(path)
    try:
        r = requests.get(PFDS_URL, params={"lat": lat, "lon": lon, "data": "depth", "units": "english",
                                           "series": "pds"}, timeout=30)
        r.raise_for_status()
        df = parse_pfds(r.text)
    except (requests.RequestException, ValueError) as e:
        if path.exists():
            return _load_csv(path)
        raise RuntimeError(f"NOAA Atlas 14 fetch failed and no cache at {path}: {e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, float_format="%.4g")
    return df


def _depths_at(duration_hr, table):
    """Depths (in) for every return period at this duration, log-log interpolated between rows."""
    d = np.log(np.clip(duration_hr, table.index.min(), table.index.max()))
    logd = np.log(table.index.values.astype(float))
    return np.array([np.exp(np.interp(d, logd, np.log(table[c].values))) for c in table.columns])


def design_depth(return_period_yr, duration_hr, table=None):
    """Atlas 14 depth (in) for a return period and duration, e.g. design_depth(5, 3) = SFPUC design storm."""
    table = atlas14_table() if table is None else table
    depths = _depths_at(duration_hr, table)
    rps = np.log(np.array(table.columns, float))
    return float(np.exp(np.interp(np.log(return_period_yr), rps, np.log(depths))))


def return_period(depth_in, duration_hr, table=None):
    """Return period (years, PDS average recurrence interval) of `depth_in` falling in `duration_hr`.

    0 for no rain; floored at 0.5 (several times a year) below the 1-year depth; inf above 1000-year.
    """
    if depth_in is None or depth_in <= 0 or duration_hr <= 0:
        return 0.0
    table = atlas14_table() if table is None else table
    depths = _depths_at(duration_hr, table)
    rps = np.array(table.columns, float)
    ld, lt = np.log(depths), np.log(rps)
    x = np.log(depth_in)
    if x > ld[-1] + 1e-9:
        return float("inf")
    if x < ld[0]:   # gentle extrapolation with the 1-2 yr slope, floored
        t = np.exp(lt[0] + (x - ld[0]) * (lt[1] - lt[0]) / (ld[1] - ld[0]))
        return float(max(MIN_T, t))
    return float(np.exp(np.interp(x, ld, lt)))


def window_scan(rain, table=None):
    """Heaviest rolling total for each standard duration within the event, plus the full event.

    Returns a list of {"duration_hr", "depth_in", "return_period_yr", "full_event"}.
    """
    table = atlas14_table() if table is None else table
    out = []
    inten = np.asarray(rain.intensity_mm_hr, float)
    step = float(rain.step_min)
    if len(inten) and step > 0:
        per_step_in = np.nan_to_num(inten) * step / 60.0 / MM_PER_IN
        csum = np.concatenate([[0.0], np.cumsum(per_step_in)])
        event_hr = len(inten) * step / 60.0
        for w in WINDOWS_HR:
            n = int(round(w * 60 / step))
            if n < 1 or abs(n * step / 60 - w) > 1e-6 or w > event_hr + 1e-9:
                continue   # window not resolvable at this step, or longer than the storm
            depth = float(np.max(csum[n:] - csum[:-n]))
            out.append({"duration_hr": float(w), "depth_in": depth,
                        "return_period_yr": return_period(depth, w, table), "full_event": False})
    if rain.depth_in and rain.duration_hr:
        out.append({"duration_hr": float(rain.duration_hr), "depth_in": float(rain.depth_in),
                    "return_period_yr": return_period(rain.depth_in, rain.duration_hr, table),
                    "full_event": True})
    return out


def _critical(scan, table):
    """Pick the rarest window; ties (e.g. all floored or all inf) go to depth relative to the 1-yr depth."""
    def score(w):
        d = _depths_at(w["duration_hr"], table)
        return (w["return_period_yr"], round(w["depth_in"] / d[0], 6), w["full_event"])
    return max(scan, key=score)


def critical_window(rain, table=None):
    """(duration_hr, depth_in) of the most unusual part of the storm (largest return period)."""
    table = atlas14_table() if table is None else table
    scan = window_scan(rain, table)
    if not scan:
        return float(rain.duration_hr or 0.0), 0.0
    w = _critical(scan, table)
    return w["duration_hr"], w["depth_in"]


def empirical(depth_in, ghcn=None):
    """How often the SF Downtown daily gauge (GHCN) has recorded a day with >= depth_in.

    Meaningful mainly for ~24 h totals (daily totals are calendar days, not rolling 24 h).
    """
    if ghcn is None:
        from src.flood.weather_input import load_ghcn   # lazy: another module, may need network
        ghcn = load_ghcn()
    df = ghcn[["date", "prcp_in"]].dropna().copy()
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"])
    first, last = df["date"].min(), df["date"].max()
    years = max((last - first).days + 1, 1) / 365.25
    hit = df[df["prcp_in"] >= depth_in]
    wet = df.loc[df["prcp_in"].idxmax()]
    return {
        "depth_in": float(depth_in),
        "days_at_or_above": int(len(hit)),
        "years_of_record": round(years, 1),
        "first_year": int(first.year),
        "last_year": int(last.year),
        "per_year": len(hit) / years,
        "rank_if_daily": int((df["prcp_in"] > depth_in).sum()) + 1,
        "n_days": int(len(df)),
        "wettest_day": {"date": wet["date"].date().isoformat(), "prcp_in": float(wet["prcp_in"])},
        "last_time_at_or_above": hit["date"].max().date().isoformat() if len(hit) else None,
        "note": "Compares against calendar-day totals; only meaningful for roughly 24-hour storms.",
    }


def label(T):
    """Plain-English return period: 'several times a year' ... 'rarer than 1-in-1000-year'."""
    if T is None or not np.isfinite(T):
        return "rarer than 1-in-1000-year"
    if T < 1:
        return "several times a year"
    if T < 1.5:
        return "about once a year"
    if T < 100:
        n = round(T) if T < 30 else int(5 * round(T / 5))
        return f"about once every {n} years"
    return f"1-in-{int(float(f'{T:.2g}'))}-year"


def _fmt_hr(h):
    if h < 1:
        return f"{round(h * 60)} min"
    if h >= 48 and h % 24 == 0:
        return f"{int(h // 24)} days"
    return f"{h:g} h" if h == int(h) else f"{h:.1f} h"


def _pct(T):
    p = 100.0 if T < 1 else 100.0 / T
    txt = f"{p:.0f}%" if p >= 0.95 else f"{p:.1f}%"
    return ("an " if txt.startswith(("8", "11", "18")) else "a ") + txt


def commonality(rain, table=None, ghcn=None):
    """How common is this storm in SF? -> Commonality (return period, annual chance, sentence, empirical)."""
    table = atlas14_table() if table is None else table
    if not rain.depth_in or rain.depth_in <= 0:
        return Commonality(0.0, 100.0, float(rain.duration_hr or 0), 0.0, METHOD,
                           "No rain — nothing to flood.", {}, table)
    scan = window_scan(rain, table)
    crit = _critical(scan, table)
    T, dur, depth = crit["return_period_yr"], crit["duration_hr"], crit["depth_in"]
    chance = 100.0 if T < 1 else (0.0 if not np.isfinite(T) else 100.0 / T)

    size = f"{rain.depth_in:.1f} in in {_fmt_hr(rain.duration_hr)}"
    if not crit["full_event"] and abs(dur - rain.duration_hr) > 1e-6:
        size += f"; heaviest {_fmt_hr(dur)}: {depth:.1f} in"
    if not np.isfinite(T):
        s = (f"A storm this size ({size}) is rarer than a 1-in-1000-year storm in San Francisco — "
             f"beyond NOAA's tables, under a 0.1% chance in any year.")
    elif T < 1:
        s = f"A storm this size ({size}) happens several times a year in San Francisco."
    elif T >= 100:
        s = (f"A storm this size ({size}) is a {label(T)} storm in San Francisco — "
             f"{_pct(T)} chance in any year.")
    else:
        s = (f"A storm this size ({size}) happens {label(T)} in San Francisco — "
             f"{_pct(T)} chance in any year.")

    emp = {}
    if rain.duration_hr <= DAILY_COMPARABLE_MAX_HR:
        try:
            emp = empirical(rain.depth_in, ghcn)
        except Exception as e:   # gauge record is a bonus; never block the answer
            emp = {"error": str(e)}
        if emp.get("days_at_or_above") is not None:
            n = emp["days_at_or_above"]
            if n == 0:
                s += (f" Since {emp['first_year']} the downtown gauge has never recorded a day this wet "
                      f"(wettest: {emp['wettest_day']['prcp_in']:.2f} in on {emp['wettest_day']['date']}).")
            elif emp["per_year"] >= 2:
                s += (f" The downtown gauge averages about {emp['per_year']:.0f} days a year this wet "
                      f"(since {emp['first_year']}).")
            else:
                s += (f" Since {emp['first_year']} the downtown gauge has seen {n} "
                      f"day{'s' if n != 1 else ''} this wet.")
    return Commonality(T, chance, dur, depth, METHOD, s, emp, table)
