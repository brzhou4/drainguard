"""Ask DrainGuard: read-only chat for app questions, drain records and local rain context."""
import streamlit as st

from src.assistant import HELP, respond

st.title("Ask DrainGuard")
st.caption("Answers from the app's own data and the public forecast. Read-only: it can't mark drains checked "
           "or send alerts. No AI service or API key is used.")

if "dg_chat" not in st.session_state:
    st.session_state["dg_chat"] = [{"role": "assistant", "content": HELP}]


def _ask(question):
    """FAQ buttons queue a question; it is answered at the top of the next run, like a typed one."""
    st.session_state["dg_pending"] = question


for msg in st.session_state["dg_chat"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask about the app, a drain ID, unchecked drains, or rain near a place")
prompt = prompt or st.session_state.pop("dg_pending", None)
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

# ---------- FAQ ----------
st.divider()
st.subheader("FAQ")
st.caption("Tap a question to ask it, or type your own in the box below.")

EXAMPLES = {
    "About the app": ["What is DrainGuard?", "What does the forecast score mean?", "How are drains ranked?",
                      "How do I mark a drain checked?", "How do I toggle map layers?",
                      "What does Evidence measure?", "What can't the app predict?"],
    "Drains": ["N-315979", "Which unchecked drains are in the Mission?", "What drains still need checking in SoMa?"],
    "Rain near a place": ["Rain near 37.7625, -122.4155", "Is it gonna pour near Dolores Park tomorrow?",
                          "Do I need an umbrella in Noe Valley?"],
}
for group, questions in EXAMPLES.items():
    with st.expander(group, expanded=group == "About the app"):
        for q in questions:
            st.button(q, key=f"faq-{q}", on_click=_ask, args=(q,), use_container_width=True)

with st.expander("What can I type?"):
    st.markdown("""
- **Casual wording and typos are fine.** "uncheked drians in the misson" works. The reply says how a typo
  was read, so you can spot a wrong guess.
- **Places:** all 41 SF neighborhoods (Mission, SoMa, Castro, Noe Valley, Mission Bay...), plus landmarks like
  Dolores Park or FiDi, which use their neighborhood's center. "Sunset" and "Richmond" cover both halves.
- **Drain IDs** from the Command Center list, typed any common way: `N-315979`, `n315979`, `drain #315979`.
- **Coordinates:** `37.7625, -122.4155`, `37.7625 N, 122.4155 W`, or with the minus sign left off.
- **Outside San Francisco** (Daly City, Marin, Oakland...) it asks for coordinates instead of guessing.
""")

with st.expander("What it can't do"):
    st.markdown("""
- It can't mark drains checked or send alerts. Use the Command Center or Volunteer page.
- It can't tell you flood depth, whether a drain will overflow, or whether a road is passable.
- The rain numbers are a forecast, not observed rain. For official warnings see weather.gov/mtr; in an
  emergency call 911.
- It isn't an AI chatbot. It matches your question to one of the lookups above, so questions outside
  those topics get the help message.
""")
