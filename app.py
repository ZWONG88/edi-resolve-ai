"""EDI Resolve Streamlit entry point and named application navigation."""

from __future__ import annotations

import streamlit as st

from ui_shared import BRAND_MARK

st.set_page_config(page_title="EDI Resolve", layout="wide")
st.logo(str(BRAND_MARK), size="large")

pages = [
    st.Page("pages/workspace.py", title="Reconciliations", icon=":material/dashboard:",
            default=True),
    st.Page("pages/upload.py", title="Upload & Process", icon=":material/upload_file:"),
    st.Page("pages/review.py", title="Exception Review", icon=":material/rule:"),
    st.Page("pages/audit.py", title="Audit History", icon=":material/history:"),
]
st.session_state["_app_navigation_enabled"] = True
current_page = st.navigation(pages, position="hidden")
st.session_state["_active_navigation_title"] = current_page.title
current_page.run()
