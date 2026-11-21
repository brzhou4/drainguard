"""DrainGuard: which San Francisco storm drains to check first before the next storm.

Run locally:  streamlit run app.py --server.address 0.0.0.0
"""
import streamlit as st

st.set_page_config(page_title="DrainGuard", page_icon=":material/water_drop:", layout="wide")

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap');
html, body, p, li, label, input, textarea, button, h1, h2, h3, h4, h5, h6,
[data-testid="stMarkdownContainer"] *:not([data-testid="stIconMaterial"]),
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] *, [data-testid="stCaptionContainer"] *,
[data-testid="stSidebarNavLink"] span:not([data-testid="stIconMaterial"]),
[data-testid="stDataFrame"], [data-baseweb="select"] * {
  font-family: 'Poppins', sans-serif !important;
}
h1 { font-weight: 700 !important; letter-spacing: -0.01em; }
</style>""", unsafe_allow_html=True)

pages = [
    st.Page("views/command_center.py", title="Command Center", icon=":material/dashboard:", default=True),
    st.Page("views/forecast.py", title="Flood Forecast", icon=":material/rainy:"),
    st.Page("views/volunteer.py", title="Volunteer", icon=":material/volunteer_activism:"),
    st.Page("views/evidence.py", title="Evidence", icon=":material/fact_check:"),
    st.Page("views/assistant.py", title="Ask DrainGuard", icon=":material/chat:"),
]

st.sidebar.markdown("### DrainGuard")
st.sidebar.caption("Ranks storm drains that are likely to need checking before a storm. "
                   "Built at Acalanes Data Fest 2026 with public SF data.")

st.navigation(pages).run()
