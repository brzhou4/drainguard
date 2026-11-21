"""Evidence: does the prediction actually work? (for judges)"""
import pandas as pd
import streamlit as st

from src.data import PROCESSED, RESULTS_CSV

st.title("Does it work?")
st.write("We picked drains using only data from **before Nov 18, 2026**, then checked them against the "
         "flood reports from the Nov 18 storm, which the model never saw.")

st.subheader("Results: share of Nov 18 flood incidents near the chosen drains")
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
            "2. 100 m grid; features from before the test date (past flooding, city flood zone, drains)  \n"
            "3. Logistic regression estimates each cell's risk  \n"
            "4. Greedy maximum coverage picks the top K drains (guaranteed ≥ 63% of the best possible coverage)  \n"
            "5. Compared against random drains, the city's flood map, and past-history-only picks")

st.subheader("Data sources")
st.markdown("- [SF 311 Cases](https://data.sf.gov/resource/vw6y-z8j6.json)\n"
            "- [Stormwater inlets, drains and catch basins](https://data.sf.gov/d/jtgq-b7c5)\n"
            "- [SFPUC 100-Year Storm Flood Risk Zone](https://data.sf.gov/d/jzu3-4yxp)\n"
            "- [NOAA daily rain, SF Downtown](https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/USW00023272.csv)")
