"""Reporting agent: JSON (machine) + HTML (human) reports; the database stays the system of record."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import prompts
from app.agents.recommendation import Outcome
from app.core.llm import LLM
from app.core.normalize import jsonable
from app.schemas.models import BusinessResult, Discrepancy, ExtractedInvoice, Finding

log = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent.parent / "reporting" / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))
_LABEL = {"approve": "Approve", "review": "Needs manual review", "reject": "Reject"}


def narrative(inv: ExtractedInvoice, outcome: Outcome, findings: list[Finding], discrepancies: list[Discrepancy],
              translation_confidence: float | None, llm: LLM) -> str:
    fallback = f"Recommendation: {_LABEL[outcome.recommendation]}. " + " ".join(outcome.reasons[:3])
    try:
        cfg = prompts.load("report")["narrative"]
        user = prompts.render(
            cfg["user"], invoice_number=inv.invoice_number or "(no number)", vendor_name=inv.vendor_name or "(unknown)",
            recommendation=outcome.recommendation, status=outcome.status,
            reasons=json.dumps(outcome.reasons, ensure_ascii=False),
            findings=json.dumps(jsonable([f.model_dump() for f in findings]), ensure_ascii=False),
            discrepancies=json.dumps(jsonable([d.model_dump() for d in discrepancies]), ensure_ascii=False),
            translation_confidence=translation_confidence,
        )
        return llm.complete("reasoning", prompts.persona() + "\n\n" + cfg["system"], user, max_tokens=400).strip() or fallback
    except Exception:  # noqa: BLE001 - narrative is cosmetic
        log.warning("report narrative via LLM failed; using deterministic text", exc_info=True)
        return fallback


def build_report(*, invoice_id: str, inv: ExtractedInvoice, outcome: Outcome, findings: list[Finding],
                 business: BusinessResult, source_language: str | None, translation_confidence: float | None,
                 ocr_confidence: float | None, run: int, summary: str, corrections: dict | None = None) -> dict:
    missing = [f.field for f in findings if f.code in ("missing_field", "no_line_items") and f.field]
    return jsonable({
        "invoice_id": invoice_id,
        "validation_run": run,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recommendation": outcome.recommendation,
        "recommendation_label": _LABEL[outcome.recommendation],
        "validation_status": outcome.status,
        "policy_rule": outcome.rule,
        "reasons": outcome.reasons,
        "summary": summary,
        "invoice": inv.model_dump(),
        "vendor_id": business.vendor_id,
        "po_number": business.po_number or inv.po_number,
        "po_inferred": business.po_inferred,
        "source_language": source_language,
        "translation_confidence": translation_confidence,
        "ocr_confidence": ocr_confidence,
        "data_findings": [f.model_dump() for f in findings],
        "discrepancies": [d.model_dump() for d in business.discrepancies],
        "missing_fields": missing,
        "corrections": corrections or {},
        "notes": business.notes,
    })


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_report_files(reports_dir: Path, invoice_id: str, report: dict, run: int) -> str:
    """Write {id}.json/.html (latest) and {id}.run{n}.json/.html (archive). Returns the HTML file name (stored in the DB)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    html = _env.get_template("report.html.j2").render(r=_AttrDict(report))
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    # per-run archives are never overwritten (run 1 is the immutable original AI report); {id}.json/.html = latest run
    _atomic_write(reports_dir / f"{invoice_id}.run{run}.json", payload)
    _atomic_write(reports_dir / f"{invoice_id}.run{run}.html", html)
    _atomic_write(reports_dir / f"{invoice_id}.json", payload)
    _atomic_write(reports_dir / f"{invoice_id}.html", html)
    return f"{invoice_id}.html"


class _AttrDict(dict):
    """Dict with attribute access (so Jinja renders r.invoice.currency and treats missing keys as undefined)."""

    def __getattr__(self, item):
        try:
            v = self[item]
        except KeyError:
            raise AttributeError(item) from None
        return _AttrDict(v) if isinstance(v, dict) else [_AttrDict(x) if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v
