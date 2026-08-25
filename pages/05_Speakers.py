import os

import streamlit as st

os.environ["DEP_PAGE"] = "speakers"
st.session_state["DEP_PAGE"] = "speakers"

import meetup  # noqa: E402, F401

try:
    st.sidebar.image("assets/dep_logo.png", width=120)
except Exception:
    pass

meetup.main()
