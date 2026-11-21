"""Evidence: does the prediction actually work? (for judges)"""
import pandas as pd
import streamlit as st

import json

from src.data import PROCESSED, RESULTS_CSV, RESULTS_JSON, data_source

st.title("Does it work?")
st.write("We picked drains using only data from **before Nov 18, 2026**, then checked them against the "
         "flood reports from the Nov 18 storm, which the model never saw.")

if data_source() == "live" and RESULTS_JSON.exists():
    s = json.loads(RESULTS_JSON.read_text())
    st.caption(f"Live run: {s.get('reports_downloaded', 0):,} flood / clogged-drain reports and {s.get('drains', 0):,} drains "
               f"downloaded from DataSF. Trained on {s.get('train_incidents', 0):,} incidents from "
               f"{s.get('storm_days_used', 0)} storm days (last training day {s.get('train_last_date', '?')}); "
               f"tested on {s.get('test_incidents', 0):,} incidents from Nov 18-19, 2026.")

st.subheader("Results: share of Nov 18 flood incidents within 100 m of the chosen drains")
if RESULTS_CSV.exists():
    st.dataframe(pd.read_csv(RESULTS_CSV), hide_index=True, use_container_width=True)
else:
    st.info("Analysis results pending. The notebook writes data/processed/results_table.csv "
            "(method × K = 25, 50, 100, 250).")

for title, fname in [("Checks", "checks.md"), ("Limits", "limits.md")]:
    st.subheader(title)
    f = PROCESSED / fname
    if f.exists():
        st.markdown(f.read_text())
    elif title == "Checks":
        st.markdown("- Leakage check: no data from Nov 18 or later used in training\n"
                    "- Duplicate reports merged vs. raw\n- Radius 50 / 100 / 150 m\n"
                    "- 40 report texts read by hand\n- Reports per neighborhood vs. risk")
    else:
        st.markdown("- 311 reports are resident reports, not measured floods\n"
                    "- The drain map is incomplete and has no clog or pipe data\n"
                    "- Tested on one storm\n"
                    "- \"Likely to need checking\" is not \"will clog\"; covering a report is not preventing a flood")

st.subheader("Method")
st.markdown("1. 311 flood + clogged-drain reports, storm days only, duplicates merged  \n"
            "2. Storm day = 25+ flood reports citywide; same-day reports within 30 m merged into one incident  \n"
            "3. Only incidents before Nov 18, 2026 are used to pick drains (leakage check in code)  \n"
            "4. Greedy maximum coverage picks drains that cover the most past incidents within 100 m "
            "(guaranteed ≥ 63% of the best possible coverage). Ranks 1-25 Urgent, 26-100 High, 101-250 Watch  \n"
            "5. Tested on Nov 18-19 against random drains and against simply ranking by past report count  \n"
            "*Next: the team's logistic-regression model adds terrain and the city flood zone.*")

st.subheader("Data sources")
st.markdown("- [SF 311 Cases](https://data.sf.gov/resource/vw6y-z8j6.json)\n"
            "- [Stormwater inlets, drains and catch basins](https://data.sf.gov/d/jtgq-b7c5)\n"
            "- [SFPUC 100-Year Storm Flood Risk Zone](https://data.sf.gov/d/jzu3-4yxp)\n"
            "- [NOAA daily rain, SF Downtown](https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/USW00023272.csv)")
