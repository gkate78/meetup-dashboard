import os

import streamlit as st

import meetup  # noqa: F401

os.environ["DEP_PAGE"] = "feedback"
st.session_state["DEP_PAGE"] = "feedback"

try:
    st.sidebar.image("assets/dep_logo.png", width=120)
except Exception:
    pass

meetup.main()
