"""RAG query router. Structured questions map to a CLOSED SET of parameterised SQL templates — the LLM only
picks a template + parameters (validated here); it never writes SQL."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app import prompts
from app.core.llm import LLM, LLMError, extract_json

log = logging.getLogger(__name__)

RECOMMENDATIONS = {"approve", "review", "reject"}

# name -> (sql, ordered param names). Every statement is read-only and parameterised.
TEMPLATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "total_invoices": ("SELECT count(*) AS invoices FROM audit.invoice_audit", ()),
    "count_by_status": (
        "SELECT COALESCE(validation_status,'pending') AS validation_status, count(*) AS invoices"
        " FROM audit.invoice_audit GROUP BY 1 ORDER BY 2 DESC", ()),
    "count_by_recommendation": (
        "SELECT COALESCE(recommendation,'pending') AS recommendation, count(*) AS invoices"
        " FROM audit.invoice_audit GROUP BY 1 ORDER BY 2 DESC", ()),
    "count_by_vendor": (
        "SELECT COALESCE(vendor_name,'(unknown)') AS vendor_name, count(*) AS invoices"
        " FROM audit.invoice_audit GROUP BY 1 ORDER BY 2 DESC LIMIT 50", ()),
    "totals_by_currency": (
        "SELECT COALESCE(currency,'(none)') AS currency, count(*) AS invoices, COALESCE(sum(total_amount),0) AS total_amount"
        " FROM audit.invoice_audit GROUP BY 1 ORDER BY 3 DESC", ()),
    "list_by_recommendation": (
        "SELECT invoice_number, vendor_name, total_amount, currency, recommendation, processed_at"
        " FROM audit.invoice_audit WHERE recommendation = %s"
        " AND processed_at >= now() - make_interval(days => %s) ORDER BY processed_at DESC LIMIT 50",
        ("recommendation", "days")),
    "list_needs_attention": (
        "SELECT invoice_number, vendor_name, total_amount, currency, recommendation, processed_at"
        " FROM audit.invoice_audit WHERE recommendation IN ('review','reject')"
        " AND processed_at >= now() - make_interval(days => %s) ORDER BY processed_at DESC LIMIT 50",
        ("days",)),
    "discrepancies_for_invoice": (
        "SELECT i.invoice_number, d.field_name, d.invoice_value, d.erp_value, d.deviation_pct, d.severity, d.validation_run"
        " FROM audit.discrepancies d JOIN audit.invoice_audit i USING (invoice_id)"
        " WHERE (i.invoice_number = %s OR i.invoice_id::text = %s) AND d.superseded = FALSE ORDER BY d.id LIMIT 100",
        ("invoice_number", "invoice_number")),
}


@dataclass
class RoutePlan:
    kind: str  # structured | open
    template: str | None = None
    params: dict = field(default_factory=dict)


def _days(q: str) -> int:
    if m := re.search(r"(?:last|past)\s+(\d{1,3})\s+days?", q):
        return int(m.group(1))
    if re.search(r"last\s+week|this\s+week", q):
        return 7
    if re.search(r"last\s+month|this\s+month", q):
        return 31
    if re.search(r"today", q):
        return 1
    return 36500


def keyword_route(question: str, invoice_scope: str | None = None) -> RoutePlan | None:
    """Deterministic pre-check. Returns None when the question is not clearly structured."""
    q = question.lower().strip()
    if re.search(r"\bwhy\b|\bexplain\b|\bsummar|\bwhat does\b|\bwhat is wrong\b|\bdescribe\b|\bcompare\b", q):
        return RoutePlan("open")
    if re.search(r"how many invoices|number of invoices|count of invoices|total invoices|invoices (?:are|were|have been) (?:processed|there)", q):
        if re.search(r"\bvendor|supplier", q):
            return RoutePlan("structured", "count_by_vendor")
        if re.search(r"status|passed|failed|partial", q):
            return RoutePlan("structured", "count_by_status")
        for rec, words in (("reject", r"reject"), ("approve", r"approv"), ("review", r"review|flag")):
            if re.search(words, q):
                return RoutePlan("structured", "list_by_recommendation", {"recommendation": rec, "days": _days(q)})
        if "recommend" in q:
            return RoutePlan("structured", "count_by_recommendation")
        return RoutePlan("structured", "total_invoices")
    if re.search(r"need(?:s)? (?:my )?attention|require(?:s)? attention|flagged invoices", q):
        return RoutePlan("structured", "list_needs_attention", {"days": _days(q)})
    if re.search(r"(?:total|sum|amount).*(?:currency|currencies)|by currency", q):
        return RoutePlan("structured", "totals_by_currency")
    if re.search(r"(?:per|by|each)\s+vendor", q) and re.search(r"invoices|count|how many", q):
        return RoutePlan("structured", "count_by_vendor")
    if m := re.search(r"(?:which|list|show)\s+(?:all\s+)?(?:the\s+)?invoices?.*?\b(reject|approv|review|flag)", q):
        word = m.group(1)
        rec = {"reject": "reject", "approv": "approve"}.get(word, "review")
        return RoutePlan("structured", "list_by_recommendation", {"recommendation": rec, "days": _days(q)})
    if m := re.search(r"discrepanc\w*\s+(?:for|of|on)\s+(?:invoice\s+)?([A-Za-z0-9][A-Za-z0-9\-/]{2,})", q):
        return RoutePlan("structured", "discrepancies_for_invoice", {"invoice_number": m.group(1)})
    if invoice_scope:
        return RoutePlan("open")  # scoped to one invoice -> retrieval
    return None


def validate_plan(raw: dict) -> RoutePlan:
    if raw.get("kind") != "structured":
        return RoutePlan("open")
    name = raw.get("template")
    if name not in TEMPLATES:
        return RoutePlan("open")
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    needed = TEMPLATES[name][1]
    clean: dict = {}
    for p in needed:
        if p == "recommendation":
            v = str(params.get("recommendation", "")).lower()
            v = {"rejected": "reject", "approved": "approve", "flagged": "review"}.get(v, v)
            if v not in RECOMMENDATIONS:
                return RoutePlan("open")
            clean[p] = v
        elif p == "days":
            try:
                clean[p] = max(1, min(int(params.get("days") or 36500), 36500))
            except (TypeError, ValueError):
                clean[p] = 36500
        elif p == "invoice_number":
            v = str(params.get("invoice_number") or "").strip()
            if not v or len(v) > 64:
                return RoutePlan("open")
            clean[p] = v
    return RoutePlan("structured", name, clean)


def route(question: str, llm: LLM, invoice_scope: str | None = None) -> RoutePlan:
    plan = keyword_route(question, invoice_scope)
    if plan is not None:
        return validate_plan({"kind": plan.kind, "template": plan.template, "params": plan.params}) if plan.kind == "structured" else plan
    cfg = prompts.load("rag")["router"]
    try:
        raw = extract_json(llm.complete("reasoning", prompts.persona() + "\n\n" + cfg["system"],
                                        prompts.render(cfg["user"], question=question), json_mode=True, max_tokens=200))
        return validate_plan(raw)
    except LLMError:
        log.warning("router LLM failed; defaulting to open retrieval", exc_info=True)
        return RoutePlan("open")


def sql_params(plan: RoutePlan) -> tuple:
    return tuple(plan.params[p] for p in TEMPLATES[plan.template][1])  # type: ignore[index]


def format_rows(template: str, rows: list[dict]) -> str:
    """Deterministic answer text for SQL results (no LLM involved)."""
    if not rows:
        return "No matching invoices were found."
    if template == "total_invoices":
        return f"{rows[0]['invoices']} invoices have been processed in total."
    headers = list(rows[0].keys())
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    for r in rows:
        lines.append(" | ".join("" if r[h] is None else (r[h].isoformat(timespec="seconds") if hasattr(r[h], "isoformat") else str(r[h])) for h in headers))
    return "\n".join(lines)
