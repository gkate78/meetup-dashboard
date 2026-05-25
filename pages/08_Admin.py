import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import meetup  # noqa: F401

os.environ["DEP_PAGE"] = "admin"
st.session_state["DEP_PAGE"] = "admin"

try:
    st.sidebar.image(os.path.join(os.path.dirname(__file__), "..", "assets", "dep_logo.png"), width=120)
except Exception:
    pass

meetup.main()
