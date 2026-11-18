"""Prompt templates + persona live in YAML next to this file (a project deliverable)."""
from __future__ import annotations

from functools import cache
from pathlib import Path
from string import Template

import yaml

_DIR = Path(__file__).parent


@cache
def load(name: str) -> dict:
    return yaml.safe_load((_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def persona() -> str:
    return str(load("persona")["system"]).strip()


def render(template: str, **values: object) -> str:
    """${var} substitution (JSON braces in prompts are left untouched)."""
    return Template(template).safe_substitute({k: "" if v is None else v for k, v in values.items()})
