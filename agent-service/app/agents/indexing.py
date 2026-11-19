"""Indexing agent: chunk stored text + report, embed, upsert into Qdrant (idempotent, deterministic ids).

Payload `source_type`: `invoice_text` (translated invoice body) | `ai_report` (the AI audit report chunks).
"""
from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.clients.qdrant_client import invoice_filter
from app.core.llm import LLM

log = logging.getLogger(__name__)

_NS = uuid.UUID("6f1d0b0e-5a0e-4c53-9c7e-3d2b7a1f9a10")
EMBED_BATCH = 16
SOURCE_INVOICE = "invoice_text"
SOURCE_REPORT = "ai_report"


def point_id(invoice_id: str, source_type: str, chunk_no: int) -> str:
    return str(uuid.uuid5(_NS, f"{invoice_id}|{source_type}|{chunk_no}"))


def chunk_text(text: str, size: int = 2000, overlap: int = 200) -> list[str]:
    """~500-token chunks (char approximation) with overlap, preferring paragraph/line boundaries."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def report_chunks(report: dict) -> list[str]:
    """Report summary + one chunk per discrepancy / data finding / reason, so 'why was it flagged' hits the report."""
    inv = report.get("invoice", {})
    head = (f"Audit report for invoice {inv.get('invoice_number') or '(no number)'} from {inv.get('vendor_name') or '(unknown vendor)'}. "
            f"Recommendation: {report.get('recommendation')}. Status: {report.get('validation_status')}. {report.get('summary', '')}")
    out = [head]
    out += [f"Reason: {r}" for r in report.get("reasons", [])]
    for d in report.get("discrepancies", []):
        out.append(f"Discrepancy on {d['field_name']} (severity {d['severity']}): invoice value {d.get('invoice_value')} "
                   f"vs ERP value {d.get('erp_value')}, deviation {d.get('deviation_pct')}%.")
    out += [f"Data check [{f['severity']}]: {f['message']}" for f in report.get("data_findings", [])]
    return out


def _upsert(client: QdrantClient, collection: str, llm: LLM, invoice_id: str, source_type: str, chunks: list[str], payload_base: dict) -> int:
    n = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        vectors = llm.embed(batch)
        points = [
            qm.PointStruct(id=point_id(invoice_id, source_type, i + j), vector=vec,
                           payload={**payload_base, "chunk_text": text, "source_type": source_type, "chunk_no": i + j})
            for j, (text, vec) in enumerate(zip(batch, vectors, strict=True))
        ]
        client.upsert(collection, points=points, wait=True)
        n += len(points)
    return n


def delete_points(client: QdrantClient, collection: str, invoice_id: str, source_type: str | None = None) -> None:
    client.delete(collection, points_selector=qm.FilterSelector(filter=invoice_filter(invoice_id=invoice_id, source_type=source_type)), wait=True)


def index_invoice(client: QdrantClient, collection: str, llm: LLM, *, invoice_id: str, invoice_number: str | None,
                  vendor: str | None, language: str | None, text: str, report: dict | None,
                  chunk_chars: int = 2000, overlap: int = 200, include_invoice_text: bool = True) -> int:
    base = {"invoice_id": invoice_id, "vendor": vendor or "", "language": language or "", "invoice_number": invoice_number or ""}
    total = 0
    if include_invoice_text:
        total += _upsert(client, collection, llm, invoice_id, SOURCE_INVOICE, chunk_text(text, chunk_chars, overlap), base)
    if report:
        # replace the report chunks wholesale (the number of chunks changes between validation runs)
        delete_points(client, collection, invoice_id, SOURCE_REPORT)
        total += _upsert(client, collection, llm, invoice_id, SOURCE_REPORT, report_chunks(report), base)
    log.info("indexed invoice", extra={"invoice_id": invoice_id, "points": total})
    return total
