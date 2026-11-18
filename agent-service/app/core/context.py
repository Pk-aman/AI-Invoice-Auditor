"""Dependency container wired once at startup (and replaced wholesale in tests)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.clients.erp_client import ErpClient
from app.core.config import Settings
from app.core.llm import LLM
from app.core.rules import Rules
from app.persistence.audit_repo import AuditRepo


@dataclass
class AppContext:
    settings: Settings
    rules: Rules
    repo: AuditRepo
    llm: LLM
    erp: ErpClient
    qdrant: Any  # qdrant_client.QdrantClient
    redis: Any
    pool: Any = None
    checkpointer: Any = None
    extras: dict = field(default_factory=dict)
