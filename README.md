# DrainGuard

**Predicting which San Francisco storm drains to check first before the next storm.**

Built at Acalanes Data Fest (Nov 21, 2026, BRIDGEGOOD, Oakland).

## The problem

On Nov 18, 2026, record rain flooded the Mission District and other parts of San Francisco, and 311 logged 837 "Sewer / flooding" reports on Nov 18–19. The city has about 25,000 storm drains, and crews can't check them all before a storm.

## The question

Using only data from before Nov 18, could we have picked the storm drains to check that sat closest to that day's flooding, and did we beat random picks and the city's official 100-Year Storm Flood Risk Map?

## Data (all public)

| Dataset | Source |
| --- | --- |
| SF 311 Cases (flood + clogged-drain reports) | https://data.sf.gov/resource/vw6y-z8j6.json |
| Stormwater inlets, drains and catch basins (23,907) | https://data.sf.gov/d/jtgq-b7c5 |
| SFPUC 100-Year Storm Flood Risk Zone | https://data.sf.gov/d/jzu3-4yxp |
| NOAA daily rain, SF Downtown (USW00023272) | https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/USW00023272.csv |

Note: the 311 flood label changed in June 2024 (`Sewer Issues` / `Flooding` → `Sewer` / `flooding`).

## Method

1. Clean the flood reports, keep storm days only, and merge duplicates into incidents.
2. Build a 100 m grid with features from before the test date.
3. Train a logistic regression to estimate each cell's flood risk.
4. Use greedy maximum coverage to pick the top-K drains.
5. Test on the Nov 18 storm against baselines: random drains, the official flood map, and past-history only.

## App

A 5-page Streamlit app:
- **Command Center:** urgency list, map, and a storm alert sent through ntfy.sh.
- **Flood Forecast:** Open-Meteo HRRR/GFS rain in eight three-hour windows (Pacific time), with a
  *citywide SF311 street-flood report score* from a frozen model trained on archived day-ahead forecasts.
  It also has an archived Nov 18–19 replay with an optional 24-hour modeled ponding scenario. The score is
  about report timing, not a street-level flood probability or an official warning.
- **Volunteer:** nearby drains to look at and report. Look and report only, never during a storm.
  - Anonymous: no names. Each volunteer gets a private sign-in link (`?v=token`).
  - Storm lock: when the forecast says storm, checks pause and the Command Center can't send volunteers out.
  - Start spot by location, address/cross streets, or map; age question and safety pledge.
  - Walking route in nearest-next order, numbered map, Google Maps walking link, Street View.
  - "I'm heading there" hides that drain from everyone else (lists and maps, Command Center too) for the walk time + 20 minutes, or until it's reported.
  - Report form (condition, note, photo); Blocked opens the pre-filled SF 311 sewer form.
  - "Couldn't reach" drains stay listed; checks expire after `CHECK_TTL_DAYS` (default 7) or a new check round.
  - Saved in Supabase if `SUPABASE_URL`/`SUPABASE_KEY` are set (run `docs/supabase_schema.sql`, add a private `photos` bucket); otherwise local files that reset on restart.
- **Evidence:** test results and limits.
- **Ask DrainGuard:** read-only chat (no LLM, no API key) for app FAQs, drain ID lookups, unchecked ranked
  drains by neighborhood, and rain plus static terrain screening near a place. It handles casual
  wording, common typos and all 41 SF Analysis Neighborhoods (`src/matching.py`, tests in `tests/`).

```bash
pip install -r requirements.txt            # app only
pip install -r requirements-analysis.txt   # + notebook packages
streamlit run app.py --server.address 0.0.0.0
```

### Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app**, choose this repo, branch `main`, main file `app.py`.
3. Under **Advanced settings → Secrets**, paste:
   ```toml
   CITY_TOPIC = "drainguard-city-<pick 6 random letters>"
   APP_URL = "https://<your-app>.streamlit.app"
   ```
4. Deploy. Files saved by the app (volunteers, drain status) reset when the app restarts.

### Files the analysis should write to `data/processed/`

| File | Used by |
| --- | --- |
| `drain_scores.csv` (asset_id, lat, lon, neighborhood, risk, rank, tier, reason) | all pages |
| `results_table.csv` (method × K recall) | Evidence |
| `results.json` (`{"headline": "62%"}`) | Command Center KPI |
| `nov18_incidents.csv` (lat, lon) | map toggle |
| `flood_zone.geojson` | map toggle |
| `checks.md`, `limits.md` | Evidence |

### Forecast replay and terrain layers

Committed, rebuilt offline:

| File | Built by |
| --- | --- |
| `data/fixtures/open_meteo_previous_day1_2026-11-18_20.json` | `python scripts/fetch_replay_fixture.py` (Open-Meteo previous-runs API; refuses to write if the archive changed) |
| `data/processed/ponding_nov19_scenario.geojson`, `terrain_depressions.geojson`, `ponding_build_summary.json` | `python scripts/build_ponding.py` (needs `requirements-analysis.txt`) |

The ponding builder mosaics Terrarium elevation tiles (z13), runs an 8-neighbor priority flood, fills each
depression with 50% of Nov 19 forecast rain as runoff, and samples 100 m street cells. It is uncalibrated
and terrain-only. It does not route spill or model inlets, pipes or measured depth. The official SFPUC
100-year hazard layer (DataSF jzu3-4yxp) is a separate dataset and is not used as a storm prediction.

## Repo layout

```text
app.py                 # Streamlit entry point
views/                 # Command Center, Flood Forecast, Volunteer, Evidence, Ask DrainGuard
src/                   # alerts.py, geo.py, forecast.py, assistant.py, analysis code
scripts/               # offline builders: forecast replay fixture, ponding/terrain layers
notebooks/             # analysis notebook
data/raw/              # downloaded datasets (not committed if large)
data/processed/        # drain_scores.csv etc. (current file is FAKE starter data)
docs/                  # build plan + app plan
```

## Limits

311 reports are resident reports, not measured floods. The inlet map is incomplete and has no clog or pipe data. The model was tested on one storm. Predictions mean "likely to need checking," not "will clog."
