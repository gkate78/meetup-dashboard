import os
import importlib

import streamlit as st

os.environ["DEP_PAGE"] = "insights"
st.session_state["DEP_PAGE"] = "insights"

try:
    st.sidebar.image("assets/dep_logo.png", width=120)
except Exception:
    pass

import meetup  # noqa: F401
importlib.reload(meetup)
