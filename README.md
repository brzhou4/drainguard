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

A 3-page Streamlit app:
- **Command Center:** urgency list, map, and a storm alert sent through ntfy.sh.
- **Volunteer:** nearby drains to look at and report. Look and report only, never during a storm.
- **Evidence:** test results and limits.

```bash
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0
```

## Repo layout

```text
app.py                 # Streamlit entry point (to build)
pages/                 # Command Center, Volunteer, Evidence
src/                   # alerts.py, geo.py, analysis code
notebooks/             # analysis notebook
data/raw/              # downloaded datasets (not committed if large)
data/processed/        # drain_scores.csv etc. (current file is FAKE starter data)
docs/                  # build plan + app plan
```

## Limits

311 reports are resident reports, not measured floods. The inlet map is incomplete and has no clog or pipe data. The model was tested on one storm. Predictions mean "likely to need checking," not "will clog."
