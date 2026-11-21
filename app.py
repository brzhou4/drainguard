"""DrainGuard: which San Francisco storm drains to check first before the next storm.

Run locally:  streamlit run app.py --server.address 0.0.0.0
"""
import streamlit as st

st.set_page_config(page_title="DrainGuard", page_icon=":material/water_drop:", layout="wide")

pages = [
    st.Page("views/command_center.py", title="Command Center", icon=":material/dashboard:", default=True),
    st.Page("views/volunteer.py", title="Volunteer", icon=":material/volunteer_activism:"),
    st.Page("views/evidence.py", title="Evidence", icon=":material/fact_check:"),
]

st.sidebar.markdown("### DrainGuard")
st.sidebar.caption("Ranks storm drains that are likely to need checking before a storm. "
                   "Built at Acalanes Data Fest 2026 with public SF data.")

st.navigation(pages).run()
