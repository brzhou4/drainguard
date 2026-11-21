"""Ask DrainGuard: read-only chat for app questions, drain records and local rain context."""
import streamlit as st

from src.assistant import HELP, respond

st.title("Ask DrainGuard")
st.caption("Answers from the app's own data and the public forecast. Read-only: it can't mark drains checked "
           "or send alerts. No AI service or API key is used.")

if "dg_chat" not in st.session_state:
    st.session_state["dg_chat"] = [{"role": "assistant", "content": HELP}]

for msg in st.session_state["dg_chat"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask about the app, a drain ID, unchecked drains, or rain near a place")
if prompt:
    st.session_state["dg_chat"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Looking that up..."):
            reply = respond(prompt)
        st.markdown(reply)
    st.session_state["dg_chat"].append({"role": "assistant", "content": reply})

if len(st.session_state["dg_chat"]) > 1 and st.button("Clear chat", icon=":material/delete_sweep:"):
    st.session_state["dg_chat"] = [{"role": "assistant", "content": HELP}]
    st.rerun()
