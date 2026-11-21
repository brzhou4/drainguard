"""Load and save the app's data files (all local CSVs, no database)."""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DRAINS_CSV = PROCESSED / "drain_scores.csv"
STATUS_CSV = PROCESSED / "drain_status.csv"
VOLUNTEERS_CSV = PROCESSED / "volunteers.csv"
RESULTS_CSV = PROCESSED / "results_table.csv"
RESULTS_JSON = PROCESSED / "results.json"

TZ = ZoneInfo("America/Los_Angeles")
TIERS = ["Urgent", "High", "Watch"]
TIER_COLORS = {"Urgent": "#D64545", "High": "#E8892B", "Watch": "#6B7C93"}
CHECKED_COLOR = "#B8BEC6"
STATUSES = ["Not checked", "Checked - clear", "Blocked - reported to 311", "Couldn't reach"]

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


def load_drains():
    """Priority drains from the analysis. Columns: asset_id, lat, lon, neighborhood, risk, rank, tier, reason."""
    if not DRAINS_CSV.exists():
        return pd.DataFrame(columns=["asset_id", "lat", "lon", "neighborhood", "risk", "rank", "tier", "reason"])
    d = pd.read_csv(DRAINS_CSV)
    if "is_fake" not in d.columns:
        d["is_fake"] = False
    return d.sort_values("rank").reset_index(drop=True)


def load_status():
    if not STATUS_CSV.exists():
        return pd.DataFrame(columns=["asset_id", "status", "volunteer", "time"])
    return pd.read_csv(STATUS_CSV, dtype=str)


def latest_status():
    """One row per drain: its most recent status."""
    s = load_status()
    if s.empty:
        return s
    return s.groupby("asset_id", as_index=False).last()


def drains_with_status():
    d = load_drains()
    s = latest_status()
    if s.empty:
        d["status"], d["checked_by"], d["checked_at"] = "Not checked", "", ""
        return d
    s = s.rename(columns={"volunteer": "checked_by", "time": "checked_at"})
    d = d.merge(s[["asset_id", "status", "checked_by", "checked_at"]], on="asset_id", how="left")
    d["status"] = d["status"].fillna("Not checked")
    d[["checked_by", "checked_at"]] = d[["checked_by", "checked_at"]].fillna("")
    return d


def log_status(asset_id, status, volunteer):
    PROCESSED.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"asset_id": asset_id, "status": status, "volunteer": volunteer, "time": now_str()}])
    row.to_csv(STATUS_CSV, mode="a", header=not STATUS_CSV.exists(), index=False)


def load_volunteers():
    if not VOLUNTEERS_CSV.exists():
        return pd.DataFrame(columns=["name", "topic", "lat", "lon", "radius_m"])
    return pd.read_csv(VOLUNTEERS_CSV)


def save_volunteer(name, topic, lat, lon, radius_m):
    PROCESSED.mkdir(parents=True, exist_ok=True)
    v = load_volunteers()
    v = v[v["name"] != name]  # re-signing up replaces the old entry
    v = pd.concat([v, pd.DataFrame([{"name": name, "topic": topic, "lat": lat, "lon": lon, "radius_m": radius_m}])])
    v.to_csv(VOLUNTEERS_CSV, index=False)
