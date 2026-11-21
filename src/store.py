"""Tiny table store: Supabase (keeps data across restarts) when configured, local CSVs otherwise.

Set SUPABASE_URL and SUPABASE_KEY (Streamlit secrets or .env) to use Supabase; create the tables
with docs/supabase_schema.sql. The key stays on the server: Streamlit never sends it to the browser.
Without them, everything is saved to CSV files in data/processed/ (these reset when Streamlit Cloud
restarts the app).
"""
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
PHOTOS = PROCESSED / "photos"
PHOTO_BUCKET = "photos"

# Columns for each table. Every value is stored as text to keep both backends identical.
TABLES = {
    "volunteers": ["token", "topic", "lat", "lon", "radius_m", "lang", "adult", "created"],  # anonymous
    "drain_status": ["asset_id", "status", "volunteer", "volunteer_token", "time", "condition", "note", "photo"],
    "claims": ["asset_id", "token", "until"],  # "en route" holds
}
CSV_NAMES = {"volunteers": "volunteers.csv", "drain_status": "drain_status.csv", "claims": "claims.csv"}

_cache = {}        # table -> (fetched_at, DataFrame), only for Supabase
CACHE_SECONDS = 10


def _setting(name):
    from src.data import setting
    return setting(name, "")


def backend():
    return "supabase" if _setting("SUPABASE_URL") and _setting("SUPABASE_KEY") else "csv"


def _headers(extra=None):
    key = _setting("SUPABASE_KEY")
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _rest(table):
    return f"{_setting('SUPABASE_URL').rstrip('/')}/rest/v1/{table}"


def _empty(table):
    return pd.DataFrame(columns=TABLES[table])


def _normalize(df, table):
    for c in TABLES[table]:
        if c not in df.columns:
            df[c] = ""
    return df[TABLES[table]].fillna("").astype(str)


def read(table):
    """Whole table as a DataFrame of strings, oldest row first."""
    if backend() == "csv":
        path = PROCESSED / CSV_NAMES[table]
        if not path.exists():
            return _empty(table)
        return _normalize(pd.read_csv(path, dtype=str), table)

    hit = _cache.get(table)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1].copy()
    rows, page = [], 1000
    while True:  # Supabase returns at most 1000 rows per request
        r = requests.get(_rest(table), params={"select": "*", "order": "id.asc"},
                         headers=_headers({"Range": f"{len(rows)}-{len(rows) + page - 1}"}), timeout=15)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < page:
            break
    df = _normalize(pd.DataFrame(rows), table) if rows else _empty(table)
    _cache[table] = (time.time(), df)
    return df.copy()


def append(table, row):
    row = {c: "" if row.get(c) is None else str(row.get(c)) for c in TABLES[table]}
    if backend() == "csv":
        PROCESSED.mkdir(parents=True, exist_ok=True)
        path = PROCESSED / CSV_NAMES[table]
        if path.exists():  # older files may lack newer columns: rewrite with the full header once
            old = pd.read_csv(path, dtype=str, nrows=0)
            if list(old.columns) != TABLES[table]:
                _normalize(pd.read_csv(path, dtype=str), table).to_csv(path, index=False)
        pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)
        return
    r = requests.post(_rest(table), json=row, headers=_headers({"Prefer": "return=minimal"}), timeout=15)
    r.raise_for_status()
    _cache.pop(table, None)


def delete(table, **match):
    """Delete rows where every given column equals the given value."""
    if backend() == "csv":
        path = PROCESSED / CSV_NAMES[table]
        if not path.exists():
            return
        df = read(table)
        keep = pd.Series(False, index=df.index)
        for col, val in match.items():
            keep |= df[col] != str(val)
        df[keep].to_csv(path, index=False)
        return
    params = {col: f"eq.{val}" for col, val in match.items()}
    requests.delete(_rest(table), params=params, headers=_headers(), timeout=15).raise_for_status()
    _cache.pop(table, None)


def save_photo(name, data: bytes, content_type="image/jpeg"):
    """Store a photo and return a path or URL to it ('' if it could not be saved)."""
    if backend() == "supabase":
        base = _setting("SUPABASE_URL").rstrip("/")
        try:
            r = requests.post(f"{base}/storage/v1/object/{PHOTO_BUCKET}/{name}", data=data,
                              headers={**_headers(), "Content-Type": content_type}, timeout=30)
            if r.ok:
                return f"{base}/storage/v1/object/{PHOTO_BUCKET}/{name}"
        except requests.RequestException:
            pass
    try:
        PHOTOS.mkdir(parents=True, exist_ok=True)
        (PHOTOS / name).write_bytes(data)
        return f"photos/{name}"
    except OSError:
        return ""
