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
| AWS Terrain Tiles (elevation, from USGS 3DEP/NED) | https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png |
| NOAA Atlas 14 precipitation frequency, SF (PFDS) | https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text_mean.csv |
| NWS observations (KSFO, KOAK) + gridpoint rain forecast | https://api.weather.gov/stations/KSFO/observations, https://api.weather.gov/gridpoints/MTR/{x},{y} |
| SF Analysis Neighborhoods (city boundary, names) | https://data.sf.gov/resource/j2bu-swwd.geojson |

Note: the 311 flood label changed in June 2024 (`Sewer Issues` / `Flooding` → `Sewer` / `flooding`).

## Method

1. Clean the flood reports, keep storm days only, and merge duplicates into incidents.
2. Build a 100 m grid with features from before the test date.
3. Train a logistic regression to estimate each cell's flood risk.
4. Use greedy maximum coverage to pick the top-K drains.
5. Test on the Nov 18 storm against baselines: random drains, the official flood map, and past-history only.

## App

A 4-page Streamlit app:
- **Command Center:** urgency list, map, and a storm alert sent through ntfy.sh.
- **Volunteer:** share your location (or type an address or tap the map), see the closest priority drain on a map with walking directions, and a link to report sewer problems or flooding to SF 311. No sign-up. Look and report only, never during a storm.
- **Evidence:** test results and limits.
- **Flood Calculator:** where would a storm flood SF, given the drains and the hills?
  - Rain from today's weather (NWS observed + forecast), a past storm (NOAA record), or a storm you type in (inches, hours, shape).
  - Map of water depth across the city, the deepest spots by neighborhood, and drains that get overwhelmed.
  - How common the storm is ("about once every 7 years") from NOAA Atlas 14 and the downtown gauge since 1921.
  - Settings: blocked drains (%), share of rain that runs off, and grid size (30 m or 60 m). Details: `docs/flood_calculator.md`.

```bash
pip install -r requirements.txt            # app only
pip install -r requirements-analysis.txt   # + notebook packages
streamlit run app.py --server.address 0.0.0.0
python -m pytest -q                        # tests (fast, no network)
```

### Flood calculator from the command line

```bash
python -m src.flood --list-storms                     # wettest SF days on record
python -m src.flood --date 2026-11-18                 # replay a past storm
python -m src.flood --depth 2.5 --duration 6 --shape burst --png flood.png   # your own storm
python -m src.flood --today                           # today's observed + forecast rain
```

Other flags: `--clog 30` (percent of drain capacity lost), `--runoff 0.85`, `--cell 60`, `--geojson out.geojson`, `--csv hotspots.csv`. Each run takes about 2–4 s.

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

## Repo layout

```text
app.py                 # Streamlit entry point
views/                 # Command Center, Volunteer, Evidence, Flood Calculator
src/                   # alerts.py, geo.py, analysis code
src/flood/             # flood calculator: terrain, drains, weather_input, frequency, hydro, calc, render, cli
tests/                 # pytest tests (python -m pytest -q)
notebooks/             # analysis notebook
data/raw/              # downloaded datasets (not committed if large)
data/processed/        # drain_scores.csv etc. (current file is FAKE starter data)
docs/                  # build plan + app plan, flood_calculator.md
```

## Limits

311 reports are resident reports, not measured floods. The inlet map is incomplete and has no clog or pipe data. The model was tested on one storm. Predictions mean "likely to need checking," not "will clog."

Flood calculator limits:
- It is a 30 m surface model, not SFPUC's sewer model. The pipe network is not public, so each drain's intake is an estimate by inlet type and the pipes are assumed to carry SFPUC's 5-year, 3-hour design storm (1.24 in, about 10.5 mm/h).
- It does not include tides, sea level, combined-sewer backups, or buildings. Water in low spots is shown as one flat pool, so the deepest numbers (several feet in a few pits) are upper bounds.
- Today's and recent rain comes from the SF Airport gauge (about 18 km south of downtown), because the downtown station does not publish rain through the NWS API. City totals can differ.
