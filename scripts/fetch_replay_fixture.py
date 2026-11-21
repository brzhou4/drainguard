"""Package the archived Nov 18-20 Open-Meteo previous-day-1 forecast as a small replay fixture.

Run from the repo root:  python scripts/fetch_replay_fixture.py
Refuses to write the fixture if the endpoint fails or the data no longer matches the original archive
(23.7 mm at the central point in the window ending 2026-11-19 12:00 UTC).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.forecast import POINTS, PREVIOUS_RUNS_URL, REPLAY_FIXTURE  # noqa: E402

KEEP_FROM, KEEP_TO = "2026-11-18T00:00", "2026-11-20T00:00"   # inclusive
REPLAY_START = "2026-11-19T00:00"
CHECK_END, CHECK_MM = "2026-11-19T12:00", 23.7
PARAMS = {"models": "gfs_hrrr", "hourly": "precipitation_previous_day1", "timezone": "GMT",
          "start_date": "2026-11-18", "end_date": "2026-11-20"}


def main():
    params = {**PARAMS, "latitude": ",".join(str(p[0]) for p in POINTS),
              "longitude": ",".join(str(p[1]) for p in POINTS)}
    r = requests.get(PREVIOUS_RUNS_URL, params=params, timeout=60)
    r.raise_for_status()
    locs = r.json()
    locs = locs if isinstance(locs, list) else [locs]
    if len(locs) != len(POINTS):
        sys.exit(f"ERROR: expected {len(POINTS)} locations, got {len(locs)}")
    units = locs[0]["hourly_units"]["precipitation_previous_day1"]
    if units != "mm":
        sys.exit(f"ERROR: unexpected unit {units!r}")
    times = locs[0]["hourly"]["time"]
    i0, i1 = times.index(KEEP_FROM), times.index(KEEP_TO)
    keep = times[i0:i1 + 1]
    values = []
    for loc in locs:
        if loc["hourly"]["time"] != times:
            sys.exit("ERROR: locations returned different time axes")
        values.append(loc["hourly"]["precipitation_previous_day1"][i0:i1 + 1])

    j = keep.index(CHECK_END)
    got = sum(values[0][j - 2:j + 1])
    if abs(got - CHECK_MM) > 0.05:
        sys.exit(f"ERROR: archive changed: central window ending {CHECK_END}Z is {got:.2f} mm, "
                 f"expected {CHECK_MM} mm. Not writing a fixture.")

    fx = {
        "description": "Open-Meteo previous-runs API, GFS/HRRR blend, precipitation_previous_day1 "
                       "(forecast issued the day before). Hourly value at T = rain in the hour ending at T.",
        "source_url": PREVIOUS_RUNS_URL, "params": PARAMS,
        "requested_points": [list(p) for p in POINTS],
        "returned_points": [[loc["latitude"], loc["longitude"]] for loc in locs],
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "replay_start_utc": REPLAY_START,
        "check": {"window_end_utc": CHECK_END, "central_mm": round(got, 2)},
        "time": keep,
        "precipitation_mm": values,
    }
    REPLAY_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    REPLAY_FIXTURE.write_text(json.dumps(fx, indent=1) + "\n")
    print(f"Wrote {REPLAY_FIXTURE.relative_to(Path.cwd()) if REPLAY_FIXTURE.is_relative_to(Path.cwd()) else REPLAY_FIXTURE}"
          f" ({len(keep)} hours x {len(values)} points). Check window: {got:.1f} mm.")


if __name__ == "__main__":
    main()
