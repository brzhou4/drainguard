"""Tests for src/flood/frequency.py (no network)."""
import math

import numpy as np
import pandas as pd
import pytest

from src.flood import RainEvent
from src.flood import frequency as fq

# Trimmed copy of the real PFDS response for SF (fe_text_mean.csv, Nov 2026).
PFDS_TEXT = """Point precipitation frequency estimates (inches)
NOAA Atlas 14 Volume 6 Version 2
Data type: Precipitation depth
Time series type: Partial duration
Latitude: 37.7749 Degree
Longitude: -122.4194 Degree


PRECIPITATION FREQUENCY ESTIMATES
by duration for ARI (years):, 1,2,5,10,25,50,100,200,500,1000
5-min:, 0.139,0.172,0.216,0.253,0.304,0.344,0.386,0.429,0.490,0.537
10-min:, 0.199,0.247,0.310,0.362,0.436,0.493,0.553,0.615,0.702,0.770
15-min:, 0.241,0.298,0.375,0.438,0.527,0.596,0.668,0.744,0.849,0.931
30-min:, 0.330,0.408,0.513,0.600,0.721,0.816,0.915,1.02,1.16,1.27
60-min:, 0.465,0.575,0.723,0.846,1.02,1.15,1.29,1.44,1.64,1.80
2-hr:, 0.656,0.802,0.998,1.16,1.39,1.58,1.76,1.96,2.24,2.46
3-hr:, 0.815,0.994,1.24,1.44,1.72,1.95,2.19,2.44,2.79,3.06
6-hr:, 1.10,1.34,1.68,1.96,2.35,2.67,3.00,3.36,3.86,4.26
12-hr:, 1.43,1.77,2.24,2.64,3.21,3.67,4.16,4.68,5.43,6.04
24-hr:, 1.83,2.29,2.93,3.48,4.26,4.90,5.59,6.32,7.38,8.24
2-day:, 2.26,2.82,3.58,4.24,5.17,5.92,6.72,7.58,8.80,9.80
3-day:, 2.56,3.17,4.01,4.72,5.73,6.54,7.40,8.32,9.61,10.7
60-day:, 9.69,12.5,15.8,18.3,21.3,23.5,25.5,27.4,29.7,31.3

Date/time (GMT):  Sat Nov 21 22:55:34 2026
"""


@pytest.fixture
def table():
    return fq.parse_pfds(PFDS_TEXT)


def uniform(depth_in, hours, step_min=15.0):
    n = int(round(hours * 60 / step_min))
    return RainEvent(depth_in, hours, np.full(n, depth_in * 25.4 / hours), step_min)


def ghcn_frame():
    dates = pd.date_range("1921-01-01", "2020-12-31", freq="D")
    p = np.zeros(len(dates))
    p[::30] = 0.5                       # lots of moderate days
    big = {"1955-12-23": 3.0, "1998-02-03": 2.6, "2014-12-11": 2.4}
    s = pd.Series(p, index=dates)
    for d, v in big.items():
        s[d] = v
    return pd.DataFrame({"date": dates, "prcp_in": s.values})


def test_parse(table):
    assert list(table.columns) == fq.RETURN_PERIODS
    assert table.index[0] == pytest.approx(5 / 60)
    assert table.index[-1] == 1440
    assert table.loc[3.0, 5] == 1.24
    assert table.loc[24.0, 100] == 5.59
    assert table.loc[72.0, 1000] == 10.7


def test_parse_rejects_junk():
    with pytest.raises(ValueError):
        fq.parse_pfds("<html>error</html>")


def test_exact_hits(table):
    for dur in [0.25, 1.0, 3.0, 24.0, 48.0]:
        for T in [1, 2, 10, 100, 1000]:
            assert fq.return_period(table.loc[dur, T], dur, table) == pytest.approx(T, rel=1e-6)
            assert fq.design_depth(T, dur, table) == pytest.approx(table.loc[dur, T], rel=1e-6)


def test_monotonic(table):
    Ts = [fq.return_period(d, 24, table) for d in np.linspace(1.9, 8.2, 40)]
    assert all(b > a for a, b in zip(Ts, Ts[1:]))
    # same depth over a longer duration is less rare
    Ts = [fq.return_period(2.0, h, table) for h in [1, 1.5, 2, 4, 6, 9, 12, 24, 48]]
    assert all(b < a for a, b in zip(Ts, Ts[1:]))
    # between table durations the depth lies between neighbouring rows
    d = fq.design_depth(10, 18, table)
    assert table.loc[12.0, 10] < d < table.loc[24.0, 10]


def test_extremes(table):
    assert fq.return_period(0, 24, table) == 0.0
    assert fq.return_period(0.01, 24, table) == fq.MIN_T
    assert 0.5 <= fq.return_period(1.7, 24, table) < 1
    assert math.isinf(fq.return_period(9.0, 24, table))


def test_labels():
    assert fq.label(0.5) == "several times a year"
    assert fq.label(1.1) == "about once a year"
    assert fq.label(7.4) == "about once every 7 years"
    assert fq.label(100) == "1-in-100-year"
    assert fq.label(float("inf")) == "rarer than 1-in-1000-year"


def test_critical_window_picks_burst(table):
    inten = np.full(96, 0.5)            # 24 h of drizzle, 15-min steps
    inten[40:52] = 0.5 * 25.4           # 3 h at 0.5 in/h = 1.5 in (~15 yr at 3 h)
    depth = inten.sum() * 0.25 / 25.4
    rain = RainEvent(depth, 24, inten, 15)
    dur, d = fq.critical_window(rain, table)
    assert dur == 3.0
    assert d == pytest.approx(1.5, rel=0.02)
    assert fq.return_period(d, dur, table) > fq.return_period(depth, 24, table)


def test_critical_window_uniform_uses_long(table):
    dur, d = fq.critical_window(uniform(2.5, 24), table)
    assert dur == 24 and d == pytest.approx(2.5)


def test_empirical():
    e = fq.empirical(2.5, ghcn_frame())
    assert e["days_at_or_above"] == 2
    assert e["first_year"] == 1921 and e["last_year"] == 2020
    assert e["years_of_record"] == pytest.approx(100, abs=0.1)
    assert e["per_year"] == pytest.approx(0.02, abs=1e-3)
    assert e["rank_if_daily"] == 3
    assert e["wettest_day"] == {"date": "1955-12-23", "prcp_in": 3.0}
    assert e["last_time_at_or_above"] == "1998-02-03"
    assert fq.empirical(5.0, ghcn_frame())["last_time_at_or_above"] is None


def test_sentence_zero(table):
    c = fq.commonality(RainEvent(0.0, 24, np.zeros(96)), table, ghcn_frame())
    assert c.sentence == "No rain — nothing to flood."
    assert c.return_period_yr == 0


def test_sentence_small(table):
    c = fq.commonality(uniform(0.3, 6), table, ghcn_frame())
    assert "several times a year" in c.sentence
    assert c.annual_chance_pct == 100
    assert c.table is table


def test_sentence_mid(table):
    c = fq.commonality(uniform(2.5, 24), table, ghcn_frame())
    assert 2 < c.return_period_yr < 5
    assert c.annual_chance_pct == pytest.approx(100 / c.return_period_yr)
    assert fq.label(c.return_period_yr) in c.sentence
    assert "2.5 in in 24 h" in c.sentence
    assert "Since 1921 the downtown gauge has seen 2 days this wet." in c.sentence
    assert c.empirical["days_at_or_above"] == 2
    assert c.method.startswith("NOAA Atlas 14")


def test_sentence_huge(table):
    c = fq.commonality(uniform(12.0, 24), table, ghcn_frame())
    assert math.isinf(c.return_period_yr)
    assert "rarer than a 1-in-1000-year" in c.sentence
    assert "never recorded a day this wet" in c.sentence


def test_sentence_100yr(table):
    c = fq.commonality(uniform(5.6, 24), table, ghcn_frame())
    assert "1-in-100-year" in c.sentence and "1% chance" in c.sentence


def test_multiday_skips_daily(table):
    c = fq.commonality(uniform(5.0, 72, 60), table, ghcn_frame())
    assert c.empirical == {}
    assert "gauge" not in c.sentence


def test_cache_roundtrip(tmp_path, table, monkeypatch):
    p = tmp_path / "atlas14_sf.csv"
    table.to_csv(p, float_format="%.4g")
    monkeypatch.setattr(fq, "CACHE_CSV", p)
    t2 = fq.atlas14_table()
    assert list(t2.columns) == fq.RETURN_PERIODS
    np.testing.assert_allclose(t2.values, table.values)
