"""LangGraph invoice pipeline with a human-in-the-loop pause and a strict two-write PostgreSQL lifecycle.

monitor -> extract -> translate -> dedupe(content) -> ingest [SQL Write #1] -> parse -> data_validate
        -> business_validate -> report -> index -> await_review -> human_review (interrupt)
              |- approve                -> finalize [SQL Write #2] -> complete
              |- reject                 -> finalize [SQL Write #2] -> complete
              `- reject_and_revalidate  -> log_review -> data_validate -> ... -> index -> mark_revalidated
                                             |- count > max_human_checks -> finalize SKIPPED_MAX_RETRIES
                                             `- otherwise               -> await_review (loop)

Persistence rules
  * Before SQL Write #1 nothing business-related is written (text lives in the graph state).
  * Between the two writes only the LangGraph checkpoint, Qdrant, Redis, report FILES and the append-only audit
    logs (processing_events, human_feedback, review_decisions) are touched.
  * Stages before ingestion end the run on failure (nothing persisted). Stages after ingestion RE-RAISE so the
    checkpoint stays on the failing node and `POST /invoices/{id}/retry` can resume it. All of them are idempotent.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agents import data_validation as dv
from app.agents.business_validation import business_validate
from app.agents.indexing import index_invoice
from app.agents.monitor import compute_checksum, load_meta
from app.agents.parser import parse_invoice
from app.agents.recommendation import recommend
from app.agents.review import apply_corrections
from app.agents.reporting import build_report, narrative, write_report_files
from app.agents.translation import translate_text
from app.core.context import AppContext
from app.core.normalize import content_checksum, jsonable
from app.core.observability import annotate, record_score, traced_span
from app.core.rag_cache import RagCache
from app.extraction.extract import ExtractionError, detect_type, extract
from app.graphs.state import InvoiceState
from app.persistence.audit_repo import DuplicateInvoice
from app.schemas.lifecycle import DecisionItem, FeedbackItem, Finalization, Ingestion
from app.schemas.models import BusinessResult, Discrepancy, ExtractedInvoice, Finding

log = logging.getLogger(__name__)

Node = Callable[[InvoiceState], dict]


def _inv(state: InvoiceState) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate(state["invoice"])


def build_nodes(ctx: AppContext) -> dict[str, Node]:
    repo, llm, rules, s = ctx.repo, ctx.llm, ctx.rules, ctx.settings
    rag_cache = RagCache(ctx.redis, s.rag_cache_ttl_s)

    def guarded(stage: str, fn: Node, *, resumable: bool = False) -> Node:
        """Event + trace wrapper. resumable=True re-raises so the checkpoint stays on this node (retryable)."""

        def node(state: InvoiceState) -> dict:
            if state.get("failed") or state.get("duplicate"):
                return {}
            iid, chk = state["invoice_id"], state.get("file_checksum")
            repo.add_event(stage, "STARTED", invoice_id=iid, file_checksum=chk)
            with traced_span(f"node.{stage.lower()}", invoice_id=iid, stage=stage):
                try:
                    update = fn(state) or {}
                except Exception as exc:  # noqa: BLE001 - every failure must be recorded, never swallowed
                    log.exception("pipeline stage failed", extra={"stage": stage, "invoice_id": iid})
                    detail = {"error": f"{type(exc).__name__}: {exc}"}
                    if isinstance(exc, ExtractionError):
                        detail["extraction_method"] = exc.method  # "failed"
                    repo.add_event(stage, "FAILED", invoice_id=iid, file_checksum=chk, detail=detail)
                    if resumable:
                        raise
                    return {"failed": True, "errors": [*state.get("errors", []), f"{stage}: {exc}"]}
            if not update.get("duplicate"):
                repo.add_event(stage, "OK", invoice_id=iid, file_checksum=chk)
            return update

        return node

    # ---- before SQL Write #1 (nothing business-related is persisted) ---------------------------------
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
        existing = repo.find_by_checksum(checksum)  # raw checksum must not exist yet
        if existing:
            if not repo.event_exists(checksum, "DUPLICATE", str(path)):
                repo.add_event("DETECTED", "DUPLICATE", invoice_id=existing, file_path=str(path), file_checksum=checksum,
                               detail={"kind": "file", "existing_invoice_id": existing})
            return {"duplicate": True, "existing_invoice_id": existing, "file_checksum": checksum}
        repo.add_event("DETECTED", "OK", invoice_id=iid, file_path=str(path), file_checksum=checksum, detail={"type": kind})
        return {"file_checksum": checksum, "file_type": kind, "meta": load_meta(path), "errors": []}

    def extract_node(state: InvoiceState) -> dict:
        res = extract(Path(state["file_path"]), langs=s.ocr_langs, min_chars=s.min_chars_per_page)
        record_score("ocr_confidence", res.ocr_confidence)
        annotate(extraction_method=res.method, chars=len(res.text))
        return {"extraction_method": res.method, "ocr_confidence": res.ocr_confidence, "raw_text": res.text}

    def translate_node(state: InvoiceState) -> dict:
        res = translate_text(state["raw_text"] or "", llm, hint_language=(state.get("meta") or {}).get("language"))
        record_score("translation_confidence", res.confidence)
        annotate(source_language=res.language, translated=res.translated)
        return {"translated_text": res.text, "source_language": res.language, "translation_confidence": res.confidence}

    def dedupe_node(state: InvoiceState) -> dict:
        """Content de-duplication on the normalised English text (equivalent copies in other formats/filenames)."""
        digest = content_checksum(state["translated_text"] or "")
        existing = repo.find_by_content_checksum(digest)
        if existing:
            repo.add_event("DEDUPE", "DUPLICATE", invoice_id=existing, file_path=state["file_path"],
                           file_checksum=state.get("file_checksum"),
                           detail={"kind": "content", "existing_invoice_id": existing, "content_checksum": digest})
            return {"duplicate": True, "existing_invoice_id": existing, "content_checksum": digest,
                    "raw_text": None, "translated_text": None}
        return {"content_checksum": digest}

    def ingest_node(state: InvoiceState) -> dict:
        """SQL Write #1: the ONLY write before finalization. Evidence only: no parsed fields, nothing validated."""
        try:
            repo.ingest_translation(Ingestion(
                invoice_id=state["invoice_id"], file_path=state["file_path"], file_checksum=state["file_checksum"],
                content_checksum=state["content_checksum"], extraction_method=state["extraction_method"],
                raw_extracted_text=state["raw_text"] or "", translated_text=state["translated_text"] or "",
                source_language=state.get("source_language") or "unknown",
                translation_confidence=float(state.get("translation_confidence") or 0.0),
                ocr_confidence=state.get("ocr_confidence")))
        except DuplicateInvoice as dup:  # a concurrent twin committed first: the UNIQUE constraints are the arbiter
            repo.add_event("DEDUPE", "DUPLICATE", invoice_id=dup.existing_invoice_id, file_path=state["file_path"],
                           file_checksum=state.get("file_checksum"),
                           detail={"kind": dup.kind, "existing_invoice_id": dup.existing_invoice_id})
            return {"duplicate": True, "existing_invoice_id": dup.existing_invoice_id, "raw_text": None, "translated_text": None}
        return {"raw_text": None, "translated_text": None, "human_check_count": 0, "validation_run": 1}

    # ---- between the writes: state / Qdrant / Redis / report files only -----------------------------
    def parse_node(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        inv = parse_invoice(repo.get_text(iid, "translated"), llm, rules, raw_text=repo.get_text(iid, "raw"))
        return {"invoice": jsonable(inv.model_dump())}

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
        report_path = write_report_files(s.reports_dir, iid, report, run)  # files, not the database
        annotate(validation_status=outcome.status, recommendation=outcome.recommendation, discrepancies=len(discrepancies),
                 validation_run=run)
        update: dict = {
            "validation_status": outcome.status, "recommendation": outcome.recommendation, "reasons": outcome.reasons,
            "report_path": report_path, "report": report,
            "run_reports": {**state.get("run_reports", {}), str(run): report},
            "discrepancy_runs": {**state.get("discrepancy_runs", {}), str(run): jsonable([d.model_dump() for d in discrepancies])},
        }
        if run == 1:
            update["original_report"] = report  # the immutable AI report
        return update

    def index_node(state: InvoiceState) -> dict:
        """Derived index: a failure is logged and repaired by /rag/reindex; it never fails the audit."""
        if state.get("failed") or state.get("duplicate"):
            return {}
        iid, chk = state["invoice_id"], state.get("file_checksum")
        repo.add_event("INDEX", "STARTED", invoice_id=iid, file_checksum=chk)
        with traced_span("node.index", invoice_id=iid, stage="INDEX"):
            try:
                inv = _inv(state)
                first_run = state.get("validation_run", 1) == 1
                index_invoice(
                    ctx.qdrant, s.qdrant_collection, llm, invoice_id=iid, invoice_number=inv.invoice_number, vendor=inv.vendor_name,
                    language=state.get("source_language"), text=repo.get_text(iid, "translated") if first_run else "",
                    report=state.get("report"), chunk_chars=s.chunk_chars, overlap=s.chunk_overlap_chars,
                    include_invoice_text=first_run,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("indexing failed", extra={"invoice_id": iid})
                repo.add_event("INDEX", "FAILED", invoice_id=iid, file_checksum=chk, detail={"error": f"{type(exc).__name__}: {exc}"})
                return {"index_error": str(exc)}
        repo.add_event("INDEX", "OK", invoice_id=iid, file_checksum=chk)
        return {"index_error": None}

    def await_review(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        repo.add_event("HUMAN_REVIEW", "PENDING", invoice_id=iid, file_checksum=state.get("file_checksum"),
                       detail={"validation_run": state.get("validation_run", 1), "human_check_count": state.get("human_check_count", 0),
                               "recommendation": state.get("recommendation"), "validation_status": state.get("validation_status")})
        return {"status": "NEEDS_REPORT_REVIEW", "force_finalize": False}

    def human_review(state: InvoiceState) -> dict:
        """Pause until a reviewer answers (POST /invoices/{id}/review resumes the graph with the decision)."""
        decision = interrupt({"invoice_id": state["invoice_id"], "validation_run": state.get("validation_run", 1),
                              "human_check_count": state.get("human_check_count", 0)})
        return {"review": decision}

    # ---- reject_and_revalidate ----------------------------------------------------------------------
    def log_review_node(state: InvoiceState) -> dict:
        review = state["review"]
        iid = state["invoice_id"]
        count = state.get("human_check_count", 0) + 1
        warnings: list[str] = []
        # 1) invalidate invoice-scoped RAG cache BEFORE revalidating; never claim success if it failed
        try:
            cleared = rag_cache.invalidate_invoice(iid)
            annotate(rag_cache_keys_deleted=cleared.total)
        except Exception as exc:  # noqa: BLE001
            log.error("RAG cache invalidation failed; stale answers may be served until the TTL expires",
                      extra={"invoice_id": iid, "error": f"{type(exc).__name__}: {exc}"})
            warnings.append("The assistant's cached answers could not be cleared; they may be outdated for up to "
                            f"{s.rag_cache_ttl_s // 60} minutes.")
        # 2) append the decision + corrections to the audit log (idempotent, serialised on the invoice row)
        corrections = review.get("corrections") or []
        feedback = [FeedbackItem(field_name=c["field_name"], original_value=c.get("original_value"),
                                 corrected_value=c["corrected_value"], corrected_by=review["reviewer"], review_round=count)
                    for c in corrections]
        decision = DecisionItem(review_round=count, decision="reject_and_revalidate", reviewer=review["reviewer"],
                                comments=review.get("comments"), human_check_count=count)
        repo.log_review_round(iid, new_count=count, decision=decision, feedback=feedback)
        # 3) corrected in-memory state for the re-run
        inv, applied = apply_corrections(_inv(state), {c["field_name"]: c["corrected_value"] for c in corrections},
                                         rules.currency_symbol_map)
        return {
            "human_check_count": count, "invoice": jsonable(inv.model_dump()),
            "corrections": {**state.get("corrections", {}), **applied},
            "validation_run": state.get("validation_run", 1) + 1,
            "force_finalize": count > s.max_human_checks,
            "feedback_log": [*state.get("feedback_log", []), *[f.model_dump() for f in feedback]],
            "decision_log": [*state.get("decision_log", []), decision.model_dump()],
            "warnings": warnings,
        }

    def mark_revalidated(state: InvoiceState) -> dict:
        repo.mark_round_revalidated(state["invoice_id"], state.get("human_check_count", 0))
        repo.add_event("REVALIDATE", "OK", invoice_id=state["invoice_id"], file_checksum=state.get("file_checksum"),
                       detail={"validation_run": state.get("validation_run"), "human_check_count": state.get("human_check_count"),
                               "recommendation": state.get("recommendation")})
        return {}

    # ---- SQL Write #2 ---------------------------------------------------------------------------------
    def finalize_node(state: InvoiceState) -> dict:
        review = state.get("review") or {}
        decision = review.get("decision")
        forced = bool(state.get("force_finalize"))
        count = state.get("human_check_count", 0)
        feedback = [FeedbackItem.model_validate(f) for f in state.get("feedback_log", [])]
        decisions = [DecisionItem.model_validate(d) for d in state.get("decision_log", [])]
        if forced:
            status, validation_status, recommendation = "SKIPPED_MAX_RETRIES", "skipped_max_retries", state["recommendation"]
        else:
            count += 1  # this approve/reject is itself a human check
            reviewer, comments = review["reviewer"], review.get("comments")
            decisions.append(DecisionItem(review_round=count, decision=decision, reviewer=reviewer, comments=comments,
                                          human_check_count=count))
            if decision == "reject":
                status, recommendation = "REJECTED", "reject"
                feedback += [FeedbackItem(field_name=c["field_name"], original_value=c.get("original_value"),
                                          corrected_value=c["corrected_value"], corrected_by=reviewer, review_round=count)
                             for c in review.get("corrections") or []]
            else:
                status, recommendation = "APPROVED", state["recommendation"]
            validation_status = state["validation_status"]
        inv = _inv(state)
        fin = Finalization(
            status=status, validation_status=validation_status, recommendation=recommendation,
            validation_run=state.get("validation_run", 1), human_check_count=count, invoice=inv,
            vendor_id=state.get("vendor_id"), report_path=state.get("report_path"),
            ai_audit_report=state["original_report"],
            run_reports={int(k): v for k, v in state.get("run_reports", {}).items()},
            discrepancy_runs={int(k): [Discrepancy.model_validate(d) for d in v] for k, v in state.get("discrepancy_runs", {}).items()},
            feedback=feedback, decisions=decisions)
        applied = repo.finalize_invoice(state["invoice_id"], fin)  # single atomic transaction
        annotate(final_status=status, human_check_count=count, applied=applied)
        record_score("human_check_count", count)
        return {"status": status, "human_check_count": count, "validation_status": validation_status, "recommendation": recommendation}

    def complete(state: InvoiceState) -> dict:
        iid = state["invoice_id"]
        repo.add_event("COMPLETED", "OK", invoice_id=iid, file_checksum=state.get("file_checksum"),
                       detail={"status": state.get("status"), "recommendation": state.get("recommendation"),
                               "validation_run": state.get("validation_run"), "index_error": state.get("index_error")})
        return {}

    return {
        "monitor": monitor,
        "extract": guarded("EXTRACT", extract_node),
        "translate": guarded("TRANSLATE", translate_node),
        "dedupe": guarded("DEDUPE", dedupe_node),
        "ingest": guarded("INGEST", ingest_node, resumable=True),
        "parse": guarded("PARSE", parse_node, resumable=True),
        "data_validate": guarded("DATA_VALIDATION", data_validate_node, resumable=True),
        "business_validate": guarded("BUSINESS_VALIDATION", business_validate_node, resumable=True),
        "report": guarded("REPORT", report_node, resumable=True),
        "index": index_node,
        "await_review": await_review,
        "human_review": human_review,
        "log_review": guarded("REVIEW", log_review_node, resumable=True),
        "mark_revalidated": mark_revalidated,
        "finalize": guarded("FINALIZE", finalize_node, resumable=True),
        "complete": complete,
    }


def _stop(state: InvoiceState) -> str:
    return "stop" if state.get("failed") or state.get("duplicate") else "go"


def _after_index(state: InvoiceState) -> str:
    """First pass -> first review; a revalidation pass -> bookkeeping first."""
    return "revalidated" if state.get("human_check_count", 0) > 0 else "await"


def _after_revalidate(state: InvoiceState) -> str:
    return "finalize" if state.get("force_finalize") else "await"


def _after_review(state: InvoiceState) -> str:
    return {"approve": "finalize", "reject": "finalize", "reject_and_revalidate": "log_review"}[state["review"]["decision"]]


def build_invoice_graph(ctx: AppContext, checkpointer=None):
    n = build_nodes(ctx)
    g = StateGraph(InvoiceState)
    for name, fn in n.items():
        g.add_node(name, fn)
    g.set_entry_point("monitor")
    for src, nxt in (("monitor", "extract"), ("extract", "translate"), ("translate", "dedupe"), ("dedupe", "ingest"),
                     ("ingest", "parse")):
        g.add_conditional_edges(src, _stop, {"go": nxt, "stop": END})
    g.add_edge("parse", "data_validate")
    g.add_edge("data_validate", "business_validate")
    g.add_edge("business_validate", "report")
    g.add_edge("report", "index")
    g.add_conditional_edges("index", _after_index, {"await": "await_review", "revalidated": "mark_revalidated"})
    g.add_conditional_edges("mark_revalidated", _after_revalidate, {"await": "await_review", "finalize": "finalize"})
    g.add_edge("await_review", "human_review")
    g.add_conditional_edges("human_review", _after_review,
                            {"finalize": "finalize", "log_review": "log_review"})
    g.add_edge("log_review", "data_validate")
    g.add_edge("finalize", "complete")
    g.add_edge("complete", END)
    return g.compile(checkpointer=checkpointer)
