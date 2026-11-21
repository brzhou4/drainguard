"""Tests for src/flood/weather_input.py (no network: requests.get is faked)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.flood import weather_input as w

TZ = ZoneInfo("America/Los_Angeles")


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "HOURLY_CACHE", tmp_path / "hourly.json")
    monkeypatch.setattr(w, "GHCN_CACHE", tmp_path / "ghcn.csv")


@pytest.mark.parametrize("shape", w.SHAPES)
@pytest.mark.parametrize("depth,dur,step", [(1.0, 24, 15), (2.5, 6, 15), (0.4, 1, 5), (3.0, 0.25, 15), (1.3, 7.3, 15)])
def test_hyetograph_sums_to_depth(shape, depth, dur, step):
    i = w.hyetograph(depth, dur, shape, step)
    assert len(i) == int(np.ceil(dur * 60 / step - 1e-9))
    assert np.all(i >= 0)
    assert np.isclose(i.sum() * step / 60, depth * 25.4)


def test_hyetograph_shapes():
    u = w.hyetograph(1, 24, "uniform")
    assert np.allclose(u, u[0])
    s = w.hyetograph(1, 24, "scs1")
    assert 36 <= np.argmax(s) <= 40            # Type I peak ~ hour 9.75-10
    f = w.hyetograph(1, 10, "front")
    assert np.argmax(f) == 0 and f[: len(f) // 3].sum() / f.sum() > 0.6
    b = w.hyetograph(1, 10, "burst")
    n = len(b)
    assert np.isclose(b[int(0.4 * n):int(0.6 * n)].sum() / b.sum(), 0.6)
    assert w.SCS_TYPE_I[0, 1] == 0 and w.SCS_TYPE_I[-1].tolist() == [24, 1]
    assert np.all(np.diff(w.SCS_TYPE_I[:, 1]) > 0)
    with pytest.raises(ValueError):
        w.hyetograph(1, 1, "nope")


def test_from_manual_validation():
    e = w.from_manual(2, 6, "burst")
    assert e.source == "manual" and e.depth_in == 2 and e.duration_hr == 6 and len(e.intensity_mm_hr) == 24
    for bad in [(0, 6), (31, 6), (1, 0.1), (1, 73), (-1, 2)]:
        with pytest.raises(ValueError):
            w.from_manual(*bad)


# ---------------------------------------------------------------- fake NWS

def _obs(t_utc, last_hr=None, last_3=None, last_6=None):
    q = lambda v: {"unitCode": "wmoUnit:mm", "value": v}  # noqa: E731
    return {"properties": {"timestamp": t_utc.isoformat(), "precipitationLastHour": q(last_hr),
                           "precipitationLast3Hours": q(last_3), "precipitationLast6Hours": q(last_6)}}


class FakeResp:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_nws(obs_by_station, qpf):
    def get(url, params=None, headers=None, timeout=None):
        if "/points/" in url:
            return FakeResp({"properties": {"gridId": "MTR", "gridX": 85, "gridY": 105}})
        if "/gridpoints/" in url:
            return FakeResp({"properties": {"quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": qpf}}})
        if "/stations/" in url:
            sid = url.split("/stations/")[1].split("/")[0]
            return FakeResp({"features": obs_by_station.get(sid, [])})
        return FakeResp({}, 429)
    return get


def test_parse_observations_routine_and_fill():
    t = datetime(2026, 11, 18, 12, 56, tzinfo=timezone.utc)
    feats = [_obs(t, 2.0), _obs(t + timedelta(minutes=35), 9.0),  # SPECI: ignored
             _obs(t + timedelta(hours=1), None), _obs(t + timedelta(hours=2), 1.0, last_3=6.0)]
    iv, miss, last = w.parse_observations(feats)
    assert miss == 0 and last == (t + timedelta(hours=2)).timestamp()
    assert np.isclose(w._total(iv), 6.0)                   # null hour filled with 6 - 2 - 1 = 3


def test_from_today_observed_plus_forecast_crossing_midnight(monkeypatch):
    now = datetime(2026, 11, 21, 14, 10, tzinfo=TZ)          # 22:10 UTC, PST (UTC-8)
    day0 = datetime(2026, 11, 21, 0, 0, tzinfo=TZ).astimezone(timezone.utc)
    # KSFO routine obs at :56 from 00:56 to 13:56 local; 2 mm/h from 09:56 to 12:56 local (4 hours)
    feats = []
    for h in range(14):
        t = day0 + timedelta(hours=h, minutes=56)
        feats.append(_obs(t, 2.0 if 10 <= h <= 13 else None))
    qpf = [{"validTime": "2026-11-21T18:00:00+00:00/PT6H", "value": 6.0},   # 10am-4pm local; 2/6 h after 13:56
           {"validTime": "2026-11-22T00:00:00+00:00/PT6H", "value": 3.0},   # 4pm-10pm local, all today
           {"validTime": "2026-11-22T06:00:00+00:00/PT6H", "value": 6.0},   # 10pm-4am: 2 of 6 h today
           {"validTime": "2026-11-22T12:00:00+00:00/PT6H", "value": 9.0}]   # tomorrow
    monkeypatch.setattr(w.requests, "get", fake_nws({"SFOC1": [], "KSFO": feats}, qpf))
    e = w.from_today(now=now)
    obs_mm, fc_mm = 8.0, 6.0 * (2 + 4 / 60) / 6 + 3.0 + 2.0
    assert np.isclose(e.depth_in * 25.4, obs_mm + fc_mm)
    assert np.isclose(e.intensity_mm_hr.sum() * e.step_min / 60, e.depth_in * 25.4)
    assert e.source == "today-forecast" and e.date == "2026-11-21"
    assert "0.31 in observed" in e.label and "Nov 21, 2026" in e.label
    assert np.isclose(e.duration_hr, 14.25)                  # rain 09:56 -> midnight, on 15-min steps (9:45-24:00)
    assert any("KSFO" in n for n in e.notes)


def test_from_today_dry(monkeypatch):
    now = datetime(2026, 7, 4, 9, 0, tzinfo=TZ)
    day0 = datetime(2026, 7, 4, tzinfo=TZ).astimezone(timezone.utc)
    feats = [_obs(day0 + timedelta(hours=h, minutes=56)) for h in range(9)]
    qpf = [{"validTime": "2026-07-04T12:00:00+00:00/P1D", "value": 0}]
    monkeypatch.setattr(w.requests, "get", fake_nws({"KSFO": feats}, qpf))
    e = w.from_today(now=now)
    assert e.depth_in == 0 and e.notes[0] == "No rain observed or forecast today"
    assert e.source == "today-observed" and np.all(e.intensity_mm_hr == 0)


def test_from_today_all_fail(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(w.requests, "get", boom)
    with pytest.raises(RuntimeError, match="Couldn't get today's weather"):
        w.from_today(now=datetime(2026, 11, 21, 12, tzinfo=TZ))


def test_iso_duration():
    assert w._iso_dur_s("PT6H") == 21600 and w._iso_dur_s("P1DT6H") == 108000 and w._iso_dur_s("PT30M") == 1800


# ---------------------------------------------------------------- daily record

@pytest.fixture
def ghcn():
    d = pd.date_range("2000-01-01", "2026-11-13", freq="D")
    p = np.zeros(len(d))
    p[d == pd.Timestamp("2026-11-13")] = 0.97
    p[d == pd.Timestamp("2014-12-11")] = 3.40
    p[d == pd.Timestamp("2022-12-31")] = 5.46
    return pd.DataFrame({"date": d, "prcp_in": p})


def test_from_date_ghcn_scs(monkeypatch, ghcn):
    monkeypatch.setattr(w, "_hourly_for_day", lambda day, now_s=None: None)
    e = w.from_date("2014-12-11", ghcn=ghcn)
    assert e.source == "noaa-daily" and e.depth_in == 3.40 and e.duration_hr == 24
    assert len(e.intensity_mm_hr) == 96 and np.isclose(e.intensity_mm_hr.sum() / 4, 3.40 * 25.4)
    assert e.label == "Dec 11, 2014 at SF Downtown (NOAA GHCN): 3.40 in"
    assert any("NRCS Type I" in n for n in e.notes)
    with pytest.raises(ValueError):
        w.from_date("1990-01-01", ghcn=ghcn)


def test_from_date_scaled_hourly_and_recent_nws(monkeypatch, ghcn):
    t0, _ = w._day_bounds(pd.Timestamp("2026-11-13").date())
    fake = {"intervals": [(t0 + 3600 * 5, t0 + 3600 * 6, 10.0), (t0 + 3600 * 6, t0 + 3600 * 7, 5.0)],
            "source": "NWS hourly observations at SF Airport (KSFO)", "station": "KSFO", "missing": 0}
    monkeypatch.setattr(w, "_hourly_for_day", lambda day, now_s=None: fake)
    e = w.from_date("2026-11-13", ghcn=ghcn)
    assert e.source == "noaa-daily" and e.depth_in == 0.97 and e.duration_hr == 2
    assert np.isclose(e.intensity_mm_hr.sum() / 4, 0.97 * 25.4)
    e2 = w.from_date("2026-11-18", ghcn=ghcn)                 # after the GHCN record ends -> NWS gauge total
    assert e2.source == "nws-observed" and np.isclose(e2.depth_in, 15 / 25.4)
    monkeypatch.setattr(w, "_hourly_for_day", lambda day, now_s=None: None)
    with pytest.raises(ValueError):
        w.from_date("2026-11-18", ghcn=ghcn)


def test_hourly_for_day_uses_nws(monkeypatch):
    day = datetime(2026, 11, 18).date()
    t0 = datetime(2026, 11, 18, tzinfo=TZ).astimezone(timezone.utc)
    feats = [_obs(t0 + timedelta(hours=h, minutes=56), 1.0) for h in range(24)]
    monkeypatch.setattr(w.requests, "get", fake_nws({"KSFO": feats}, []))
    res = w._hourly_for_day(day, now_s=datetime(2026, 11, 21, tzinfo=TZ).timestamp())
    assert res["station"] == "KSFO" and 23 < w._total(res["intervals"]) <= 24.0
    assert "day:2026-11-18" in w._cache_read()                # cached for offline use


def test_notable_storms(ghcn, monkeypatch):
    top = w.notable_storms(2, ghcn=ghcn)
    assert list(top.date.dt.strftime("%Y-%m-%d")) == ["2022-12-31", "2014-12-11"]
    w._cache_write("day:2026-11-18", {"intervals": [[0, 3600, 70.0]], "station": "KSFO", "source": "x"})
    top = w.notable_storms(2, ghcn=ghcn)
    assert "2026-11-18" in set(top.date.dt.strftime("%Y-%m-%d")) and len(top) == 2


def test_load_ghcn_parses_and_caches(monkeypatch):
    csv = '"STATION","DATE","PRCP"\n"X","2026-11-12","0"\n"X","2026-11-13","246"\n"X","2026-11-14",\n'
    calls = []

    class R(FakeResp):
        text = csv

    monkeypatch.setattr(w.requests, "get", lambda *a, **k: calls.append(1) or R({}))
    df = w.load_ghcn()
    assert list(df.columns) == ["date", "prcp_in"] and len(df) == 2
    assert np.isclose(df.prcp_in.iloc[1], 0.969, atol=1e-3) and str(df.date.dtype).startswith("datetime64")
    w.load_ghcn()
    assert len(calls) == 1                                   # second call served from cache
