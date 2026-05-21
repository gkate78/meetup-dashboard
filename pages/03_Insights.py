import os

import streamlit as st

import meetup  # noqa: F401

os.environ["DEP_PAGE"] = "insights"
st.session_state["DEP_PAGE"] = "insights"

try:
    st.sidebar.image("assets/dep_logo.png", width=120)
except Exception:
    pass

meetup.main()
