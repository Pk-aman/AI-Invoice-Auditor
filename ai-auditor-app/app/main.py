"""Entry point: streamlit run app/main.py"""
import logging
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st  # noqa: E402

from app import ui_text as T  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.nav import pages  # noqa: E402

logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    stream=sys.stdout)

st.set_page_config(page_title=T.APP_TITLE, page_icon="🧾", layout="wide")
st.sidebar.title(f"🧾 {T.APP_TITLE}")
st.navigation(list(pages().values())).run()
