"""LangGraph invoice pipeline + the re-validation sub-pipeline.

monitor -> extract -> translate -> parse -> data_validate -> business_validate -> report -> index -> complete
Every node is guarded: success/failure is written to audit.processing_events and a failure ends the run
(never a silent loop). Nodes are idempotent, so a retry/resume is safe.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from langgraph.graph import END, StateGraph

from app.agents import data_validation as dv
from app.agents.business_validation import business_validate
from app.agents.indexing import index_invoice
from app.agents.monitor import compute_checksum, load_meta
from app.agents.parser import parse_invoice
from app.agents.recommendation import recommend
from app.agents.reporting import build_report, narrative, write_report_files
from app.agents.translation import translate_text
from app.core.context import AppContext
from app.core.normalize import jsonable, normalize_currency, parse_amount, parse_date
from app.extraction.extract import detect_type, extract
from app.graphs.state import InvoiceState
from app.schemas.models import BusinessResult, Discrepancy, ExtractedInvoice, Finding

log = logging.getLogger(__name__)

Node = Callable[[InvoiceState], dict]


def _inv(state: InvoiceState) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate(state["invoice"])


def apply_corrections(inv: ExtractedInvoice, feedback: dict[str, str], symbol_map: dict[str, str]) -> tuple[ExtractedInvoice, dict[str, str]]:
    """Overlay the latest human corrections on the extracted values (in memory only; originals stay untouched in the DB).

    Field names follow the services API: header fields, and `line:{n}:{item_code|description|quantity|unit_price|line_total}`.
    """
    applied: dict[str, str] = {}
    inv = inv.model_copy(deep=True)
    for field_name, value in feedback.items():
        v = None if value is None or str(value).strip() == "" else str(value).strip()
        if field_name.startswith("line:"):
            try:
                _, n, attr = field_name.split(":", 2)
                idx = int(n) - 1
                li = inv.line_items[idx]
            except (ValueError, IndexError):
                log.warning("ignoring correction for unknown line", extra={"field": field_name})
                continue
            if attr in ("quantity", "unit_price", "line_total"):
                setattr(li, attr, parse_amount(v))
            elif attr in ("item_code", "description"):
                setattr(li, attr, v)
            else:
                continue
        elif field_name in ("invoice_number", "vendor_name", "po_number", "vendor_id"):
            setattr(inv, field_name, v)
            if field_name == "vendor_name":
                inv.vendor_id = feedback.get("vendor_id")  # force re-resolution through the ERP
        elif field_name == "invoice_date":
            inv.invoice_date = parse_date(v)
        elif field_name == "currency":
            inv.currency = normalize_currency(v, symbol_map) or (v.upper() if v else None)
        elif field_name in ("total_amount", "subtotal", "tax_amount", "tax_rate"):
            setattr(inv, field_name, parse_amount(v))
        else:
            log.warning("ignoring correction for unsupported field", extra={"field": field_name})
            continue
        applied[field_name] = value
    return inv, applied


def build_nodes(ctx: AppContext) -> dict[str, Node]:
    repo, llm, rules, s = ctx.repo, ctx.llm, ctx.rules, ctx.settings

    def guarded(stage: str, fn: Node) -> Node:
        def node(state: InvoiceState) -> dict:
            if state.get("failed") or state.get("duplicate"):
                return {}
            iid, chk = state["invoice_id"], state.get("file_checksum")
            repo.add_event(stage, "STARTED", invoice_id=iid, file_checksum=chk)
            try:
                update = fn(state) or {}
            except Exception as exc:  # noqa: BLE001 - every failure must be recorded, never swallowed
                log.exception("pipeline stage failed", extra={"stage": stage, "invoice_id": iid})
                repo.add_event(stage, "FAILED", invoice_id=iid, file_checksum=chk, detail={"error": f"{type(exc).__name__}: {exc}"})
                return {"failed": True, "errors": [*state.get("errors", []), f"{stage}: {exc}"]}
            repo.add_event(stage, "OK", invoice_id=iid, file_checksum=chk)
            return update
        return node

    def monitor(state: InvoiceState) -> dict:
        path = Path(state["file_path"])
        iid = state["invoice_id"]
        try:
            checksum = compute_checksum(path)
            kind = detect_type(path)
        except OSError as exc:
            repo.add_event("DETECTED", "FAILED", invoice_id=iid, file_path=str(path), detail={"error": str(exc)})
            return {"failed": True, "errors": [f"monitor: {exc}"]}
        if kind is None:
            repo.add_event("DETECTED", "REJECTED", invoice_id=iid, file_path=str(path), file_checksum=checksum,
                           detail={"reason": f"unsupported type {path.suffix}"})
            return {"failed": True, "file_checksum": checksum, "errors": [f"unsupported type {path.suffix}"]}
        existing = repo.find_by_checksum(checksum)
        if existing:
            if not repo.event_exists(checksum, "DUPLICATE", str(path)):
                repo.add_event("DETECTED", "DUPLICATE", invoice_id=existing, file_path=str(path), file_checksum=checksum,
                               detail={"existing_invoice_id": existing})
            return {"duplicate": True, "existing_invoice_id": existing, "file_checksum": checksum}
        repo.add_event("DETECTED", "OK", invoice_id=iid, file_path=str(path), file_checksum=checksum, detail={"type": kind})
        return {"file_checksum": checksum, "file_type": kind, "meta": load_meta(path), "errors": []}

    def extract_node(state: InvoiceState) -> dict:
        res = extract(Path(state["file_path"]), langs=s.ocr_langs, min_chars=s.min_chars_per_page)
        # raw text is persisted BEFORE anything else (OCR is expensive and must never be repeated)
        repo.create_invoice(state["invoice_id"], state["file_path"], state["file_checksum"], res.method, res.text, res.ocr_confidence)
        return {"extraction_method": res.method, "ocr_confidence": res.ocr_confidence}

    def translate_node(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        res = translate_text(repo.get_text(iid, "raw"), llm, hint_language=(state.get("meta") or {}).get("language"))
        repo.save_translation(iid, res.text, res.language, res.confidence)
        return {"source_language": res.language, "translation_confidence": res.confidence}

    def parse_node(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        inv = parse_invoice(repo.get_text(iid, "translated"), llm, rules, raw_text=repo.get_text(iid, "raw"))
        repo.save_parsed(iid, inv)
        return {"invoice": jsonable(inv.model_dump())}

    def load_corrected(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        row = repo.get_invoice(iid)
        original = repo.get_parsed(iid)
        if row is None or original is None:
            raise LookupError(f"invoice {iid} not found")
        inv, applied = apply_corrections(original, repo.latest_feedback(iid), rules.currency_symbol_map)
        return {"invoice": jsonable(inv.model_dump()), "corrections": applied, "source_language": row["source_language"],
                "translation_confidence": float(row["translation_confidence"]) if row["translation_confidence"] is not None else None,
                "ocr_confidence": float(row["ocr_confidence"]) if row["ocr_confidence"] is not None else None,
                "reindex_report_only": True}

    def data_validate_node(state: InvoiceState) -> dict:
        inv = _inv(state)
        findings = dv.validate_data(inv, rules, arith_tol=s.arith_tolerance)
        return {"data_findings": [f.model_dump() for f in findings], "data_summary": dv.summarise_findings(inv, findings, llm)}

    def business_validate_node(state: InvoiceState) -> dict:
        res = business_validate(_inv(state), rules, ctx.erp, vendor_min_score=s.vendor_match_min_score)
        return {"discrepancies": jsonable([d.model_dump() for d in res.discrepancies]), "vendor_id": res.vendor_id,
                "po_number": res.po_number, "po_inferred": res.po_inferred, "notes": res.notes}

    def report_node(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        inv = _inv(state)
        findings = [Finding(**f) for f in state.get("data_findings", [])]
        discrepancies = [Discrepancy.model_validate(d) for d in state.get("discrepancies", [])]
        business = BusinessResult(discrepancies=discrepancies, vendor_id=state.get("vendor_id"), po_number=state.get("po_number"),
                                  po_inferred=state.get("po_inferred", False), notes=state.get("notes", []))
        outcome = recommend(findings, discrepancies, state.get("translation_confidence"), rules)
        run = state.get("validation_run", 1)
        summary = narrative(inv, outcome, findings, discrepancies, state.get("translation_confidence"), llm)
        report = build_report(invoice_id=iid, inv=inv, outcome=outcome, findings=findings, business=business,
                              source_language=state.get("source_language"), translation_confidence=state.get("translation_confidence"),
                              ocr_confidence=state.get("ocr_confidence"), run=run, summary=summary, corrections=state.get("corrections"))
        report_path = write_report_files(s.reports_dir, iid, report, run)
        repo.save_validation(iid, run=run, status=outcome.status, recommendation=outcome.recommendation,
                             discrepancies=discrepancies, vendor_id=business.vendor_id, report_path=report_path)
        return {"validation_status": outcome.status, "recommendation": outcome.recommendation, "reasons": outcome.reasons,
                "report_path": report_path, "report": report}

    def index_node(state: InvoiceState) -> dict:
        if state.get("failed") or state.get("duplicate"):
            return {}
        iid, chk = state["invoice_id"], state.get("file_checksum")
        repo.add_event("INDEX", "STARTED", invoice_id=iid, file_checksum=chk)
        try:
            inv = _inv(state)
            only_report = state.get("reindex_report_only", False)
            index_invoice(
                ctx.qdrant, s.qdrant_collection, llm, invoice_id=iid, invoice_number=inv.invoice_number, vendor=inv.vendor_name,
                language=state.get("source_language"), text="" if only_report else repo.get_text(iid, "translated"),
                report=state.get("report"), chunk_chars=s.chunk_chars, overlap=s.chunk_overlap_chars, include_invoice_text=not only_report,
            )
        except Exception as exc:  # noqa: BLE001 - the audit is complete without the vector index; /rag/reindex repairs it
            log.exception("indexing failed", extra={"invoice_id": iid})
            repo.add_event("INDEX", "FAILED", invoice_id=iid, file_checksum=chk, detail={"error": f"{type(exc).__name__}: {exc}"})
            return {"index_error": str(exc)}
        repo.add_event("INDEX", "OK", invoice_id=iid, file_checksum=chk)
        return {}

    def complete(state: InvoiceState) -> dict:
        if state.get("failed") or state.get("duplicate"):
            return {}
        iid = state["invoice_id"]
        if state.get("reindex_report_only"):
            repo.mark_feedback_revalidated(iid)
        repo.add_event("COMPLETED", "OK", invoice_id=iid, file_checksum=state.get("file_checksum"),
                       detail={"recommendation": state.get("recommendation"), "validation_run": state.get("validation_run"),
                               "index_error": state.get("index_error")})
        return {}

    return {
        "monitor": monitor,
        "extract": guarded("EXTRACT", extract_node),
        "translate": guarded("TRANSLATE", translate_node),
        "parse": guarded("PARSE", parse_node),
        "load_corrected": guarded("LOAD", load_corrected),
        "data_validate": guarded("DATA_VALIDATION", data_validate_node),
        "business_validate": guarded("BUSINESS_VALIDATION", business_validate_node),
        "report": guarded("REPORT", report_node),
        "index": index_node,
        "complete": complete,
    }


def _stop(state: InvoiceState) -> str:
    return "stop" if state.get("failed") or state.get("duplicate") else "go"


def build_invoice_graph(ctx: AppContext, checkpointer=None):
    n = build_nodes(ctx)
    g = StateGraph(InvoiceState)
    for name in ("monitor", "extract", "translate", "parse", "data_validate", "business_validate", "report", "index", "complete"):
        g.add_node(name, n[name])
    g.set_entry_point("monitor")
    g.add_conditional_edges("monitor", _stop, {"go": "extract", "stop": END})
    g.add_conditional_edges("extract", _stop, {"go": "translate", "stop": END})
    g.add_edge("translate", "parse")
    g.add_edge("parse", "data_validate")
    g.add_edge("data_validate", "business_validate")
    g.add_edge("business_validate", "report")
    g.add_edge("report", "index")
    g.add_edge("index", "complete")
    g.add_edge("complete", END)
    return g.compile(checkpointer=checkpointer)


def build_revalidate_graph(ctx: AppContext, checkpointer=None):
    """Resumes at data validation with human corrections overlaid; re-indexes the report chunks only."""
    n = build_nodes(ctx)
    g = StateGraph(InvoiceState)
    for name in ("load_corrected", "data_validate", "business_validate", "report", "index", "complete"):
        g.add_node(name, n[name])
    g.set_entry_point("load_corrected")
    g.add_conditional_edges("load_corrected", _stop, {"go": "data_validate", "stop": END})
    g.add_edge("data_validate", "business_validate")
    g.add_edge("business_validate", "report")
    g.add_edge("report", "index")
    g.add_edge("index", "complete")
    g.add_edge("complete", END)
    return g.compile(checkpointer=checkpointer)
