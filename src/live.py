"""Rank SF storm drains from real public data (runs on app start, cached).

Method (history + greedy coverage, a baseline until the full model is ready):
1. Download every 311 flood / clogged-drain report and every mapped storm drain.
2. Storm day = a day with >= 25 flood reports citywide. Keep reports on storm days.
3. Merge reports on the same day within ~30 m into one incident (duplicate removal).
4. Use only incidents BEFORE 2026-11-18 to pick drains (no leakage).
5. Greedy maximum coverage: pick drains one at a time, each covering the most
   not-yet-covered past incidents within 100 m. Rank 1-25 Urgent, 26-100 High, 101-250 Watch.
6. Test: share of Nov 18-19 incidents within 100 m of the top K drains, vs. random drains
   and vs. ranking drains by raw past-incident count.
"""
import heapq

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

BASE = "https://data.sf.gov/resource"
TEST_START = pd.Timestamp("2026-11-18")
TEST_END = pd.Timestamp("2026-11-20")          # Nov 18-19
RADIUS_M = 100
STORM_DAY_MIN_REPORTS = 25
N_RANKED = 250
LAT0 = 37.76
BBOX = (37.70, 37.83, -122.52, -122.35)          # lat min, lat max, lon min, lon max

FLOOD_WHERE = ("((service_name='Sewer Issues' AND service_subtype='Flooding') OR "
               "(service_name='Sewer' AND service_subtype in('flooding','catch_basin'))) "
               "AND lat IS NOT NULL")


def to_xy(lat, lon):
    """Lat/lon to approximate meters (fine for distances inside one city)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.c_[lon * 111320 * np.cos(np.radians(LAT0)), lat * 110540]


def in_sf(df, lat="lat", lon="lon"):
    return df[df[lat].between(BBOX[0], BBOX[1]) & df[lon].between(BBOX[2], BBOX[3])]


def _get_all(dataset, params, page=50000):
    rows, offset = [], 0
    while True:
        p = dict(params, **{"$limit": page, "$offset": offset})
        r = requests.get(f"{BASE}/{dataset}.json", params=p, timeout=120)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < page:
            return rows
        offset += page


def fetch_reports():
    rows = _get_all("vw6y-z8j6", {
        "$select": "service_request_id,requested_datetime,service_subtype,lat,long",
        "$where": FLOOD_WHERE, "$order": "service_request_id"})
    df = pd.DataFrame(rows)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["long"], errors="coerce")
    df["time"] = pd.to_datetime(df["requested_datetime"], errors="coerce")
    df = in_sf(df.dropna(subset=["lat", "lon", "time"]))
    df["date"] = df["time"].dt.normalize()
    return df[["service_request_id", "date", "service_subtype", "lat", "lon"]]


def fetch_inlets():
    rows = _get_all("jtgq-b7c5", {
        "$select": "puc_maximo_asset_id,drain_type,analysis_neighborhood,location",
        "$order": "puc_maximo_asset_id"})
    out = []
    for r in rows:
        loc = r.get("location") or {}
        coords = loc.get("coordinates") if isinstance(loc, dict) else None
        if coords and len(coords) == 2:
            out.append({"asset_id": r.get("puc_maximo_asset_id"), "drain_type": r.get("drain_type", ""),
                        "neighborhood": r.get("analysis_neighborhood") or "Unknown",
                        "lon": float(coords[0]), "lat": float(coords[1])})
    return in_sf(pd.DataFrame(out)).drop_duplicates("asset_id").reset_index(drop=True)


def to_incidents(reports):
    """Storm days only, then merge same-day reports within ~30 m."""
    per_day = reports.groupby("date").size()
    storm_days = set(per_day[per_day >= STORM_DAY_MIN_REPORTS].index)
    r = reports[reports["date"].isin(storm_days)].copy()
    xy = to_xy(r["lat"], r["lon"])
    r["gx"], r["gy"] = np.round(xy[:, 0] / 30).astype(int), np.round(xy[:, 1] / 30).astype(int)
    inc = r.drop_duplicates(["date", "gx", "gy"]).reset_index(drop=True)
    return inc, storm_days


def greedy_cover(cov_lists, n_items, k):
    """Lazy greedy maximum coverage. Returns (picked indices, gains)."""
    covered = np.zeros(n_items, bool)
    heap = [(-len(c), i) for i, c in enumerate(cov_lists) if len(c)]
    heapq.heapify(heap)
    picks, gains = [], []
    while heap and len(picks) < k:
        neg, i = heapq.heappop(heap)
        gain = int((~covered[cov_lists[i]]).sum())
        if gain == 0:
            continue
        if heap and gain < -heap[0][0]:
            heapq.heappush(heap, (-gain, i))
            continue
        picks.append(i)
        gains.append(gain)
        covered[cov_lists[i]] = True
    return picks, gains


def recall_at_k(inlet_xy, test_tree, n_test, idx):
    if n_test == 0 or len(idx) == 0:
        return float("nan")
    hits = set()
    for lst in test_tree.query_ball_point(inlet_xy[idx], r=RADIUS_M):
        hits.update(lst)
    return len(hits) / n_test


def rank_drains(reports, inlets, seed=0):
    inc, storm_days = to_incidents(reports)
    train = inc[inc["date"] < TEST_START].reset_index(drop=True)
    test = inc[(inc["date"] >= TEST_START) & (inc["date"] < TEST_END)].reset_index(drop=True)
    assert train["date"].max() < TEST_START, "leakage: training data includes the test storm"

    inlet_xy = to_xy(inlets["lat"], inlets["lon"])
    train_xy = to_xy(train["lat"], train["lon"])
    cov = [np.asarray(c, int) for c in cKDTree(train_xy).query_ball_point(inlet_xy, r=RADIUS_M)]

    # Per-drain history: past incidents nearby and distinct storm days with flooding nearby.
    train_dates = train["date"].to_numpy()
    total = np.array([len(c) for c in cov])
    days = np.array([len(set(train_dates[c])) if len(c) else 0 for c in cov])

    picks, gains = greedy_cover(cov, len(train), N_RANKED)
    ranked = inlets.iloc[picks].copy().reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    ranked["tier"] = np.where(ranked["rank"] <= 25, "Urgent", np.where(ranked["rank"] <= 100, "High", "Watch"))
    ranked["past_incidents"] = total[picks]
    ranked["storm_days"] = days[picks]
    ranked["new_coverage"] = gains
    ranked["risk"] = (ranked["new_coverage"] / max(gains[0], 1)).round(3) if gains else 0
    ranked["reason"] = [f"Flooding reported within {RADIUS_M} m on {d} past storm days ({t} incidents)."
                        for d, t in zip(ranked["storm_days"], ranked["past_incidents"])]
    ranked["is_fake"] = False

    # ---- Test on the Nov 18-19 storm ----
    rows = []
    if len(test):
        test_tree = cKDTree(to_xy(test["lat"], test["lon"]))
        rng = np.random.default_rng(seed)
        by_count = np.argsort(-total, kind="stable")
        for k in (25, 50, 100, 250):
            rand = [recall_at_k(inlet_xy, test_tree, len(test), rng.choice(len(inlets), k, replace=False))
                    for _ in range(100)]
            rows.append({
                "K drains": k,
                "DrainGuard (greedy coverage)": recall_at_k(inlet_xy, test_tree, len(test), np.array(picks[:k])),
                "Most past reports (no greedy)": recall_at_k(inlet_xy, test_tree, len(test), by_count[:k]),
                "Random drains (mean of 100)": float(np.mean(rand)),
                "Random 95th percentile": float(np.percentile(rand, 95)),
            })
    results = pd.DataFrame(rows)
    summary = {
        "storm_days_used": int(sum(1 for d in storm_days if d < TEST_START)),
        "train_incidents": int(len(train)), "test_incidents": int(len(test)),
        "reports_downloaded": int(len(reports)), "drains": int(len(inlets)),
        "train_last_date": str(train["date"].max().date()) if len(train) else "",
    }
    return ranked, results, summary, test


def run():
    """Download everything and rank. Returns (ranked, all_inlets, results, summary, test_incidents)."""
    reports = fetch_reports()
    inlets = fetch_inlets()
    ranked, results, summary, test = rank_drains(reports, inlets)
    return ranked, inlets[["asset_id", "lat", "lon"]], results, summary, test[["lat", "lon"]]
