import uuid
from pathlib import Path

REPORT_EXT = {"html": ".html", "json": ".json"}


def resolve_report_path(reports_dir: Path, invoice_id: uuid.UUID, fmt: str) -> Path | None:
    """Report files are named {invoice_id}.{html|json} by the agent. The name is built from a
    validated UUID (never from client text) and the resolved path must stay inside reports_dir."""
    if fmt not in REPORT_EXT:
        return None
    base = reports_dir.resolve()
    candidate = (base / f"{invoice_id}{REPORT_EXT[fmt]}").resolve()
    if base not in candidate.parents or not candidate.is_file():
        return None
    return candidate
