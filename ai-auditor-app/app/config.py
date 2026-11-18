"""Backend locations come from the environment (or Streamlit secrets); nothing is hard-coded."""
import os

import streamlit as st


def _get(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        try:
            value = st.secrets.get(name)
        except Exception:  # no secrets file
            value = None
    if not value:
        raise RuntimeError(f"{name} is not set. Define it in the environment or .streamlit/secrets.toml.")
    return value.rstrip("/")


SERVICES_URL = _get("SERVICES_URL")
AGENT_URL = _get("AGENT_URL")

PAGE_SIZE = 8
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = ["pdf", "docx", "png", "jpg", "jpeg"]
