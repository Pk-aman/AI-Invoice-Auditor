"""Local stand-in for ai-auditor-services (/api/v1) AND agent-service, one process.

Run:  .venv/bin/uvicorn tests.fake_backend:app --port 8765
Then: SERVICES_URL=http://127.0.0.1:8765 AGENT_URL=http://127.0.0.1:8765 streamlit run app/main.py
Shapes follow the plans; see ASSUMPTIONS.md. In-memory state; restart to reset.
"""
from __future__ import annotations

import hashlib
import itertools
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

app = FastAPI(title="fake backend")
NOW = datetime.now(UTC)
_ids = itertools.count(100)


def _inv(n, no, vendor, vid, cur, total, rec, status, lang="en", conf=0.97, days=0, run=1):
    return {"invoice_id": f"00000000-0000-0000-0000-{n:012d}", "invoice_number": no,
            "vendor_name": vendor, "vendor_id": vid, "po_number": f"PO-100{n}",
            "invoice_date": str(date.today() - timedelta(days=days + 3)), "currency": cur,
            "total_amount": total, "validation_status": status, "recommendation": rec,
            "source_language": lang, "translation_confidence": conf, "ocr_confidence": 0.9,
            "validation_run": run, "extraction_method": "pdfplumber",
            "processed_at": (NOW - timedelta(days=days)).isoformat(), "report_path": f"{n}.html"}


INVOICES = [
    _inv(1, "INV-1001", "Global Logistics Ltd", "VEND-001", "USD", 1020.00, "approve", "passed"),
    _inv(2, "INV-1002", "BlueOcean Transport Co.", "VEND-002", "USD", 262.50, "review", "partial", days=1),
    _inv(3, "INV-1003", "Transporte Ibérico S.A.", "VEND-003", "EUR", 340.00, "review", "failed",
         lang="es", conf=0.91, days=2),
    _inv(4, "INV-1004", "HafenLogistik GmbH", "VEND-004", "EUR", 1600.00, "approve", "passed",
         lang="de", conf=0.96, days=2),
    _inv(5, "VNEX2134", "Blue Logistic Vietnam", None, "VND", 134462512.00, "reject", "failed",
         lang="vi", conf=0.82, days=3),
    _inv(6, None, "OceanFreight Pvt Ltd", "VEND-006", None, 40000.00, "review", "failed", days=4),
]
LINES = {1: [{"line_number": 1, "item_code": "SKU-001", "description": "Pallet Wrapping Film",
              "quantity": 50, "unit_price": 12.0, "line_total": 600.0},
             {"line_number": 2, "item_code": "SKU-002", "description": "Industrial Gloves",
              "quantity": 120, "unit_price": 3.0, "line_total": 360.0}],
         2: [{"line_number": 1, "item_code": "SKU-101", "description": "Container Seals",
              "quantity": 200, "unit_price": 1.31, "line_total": 262.0}]}
DISCS = {2: [{"id": 1, "validation_run": 1, "superseded": False, "field_name": "line:1:unit_price",
              "invoice_value": "1.31", "erp_value": "1.25", "deviation_pct": 4.8, "severity": "low"},
             {"id": 2, "validation_run": 1, "superseded": False, "field_name": "total_amount",
              "invoice_value": "262.50", "erp_value": "250.00", "deviation_pct": 5.0,
              "severity": "medium"}],
         3: [{"id": 3, "validation_run": 1, "superseded": False, "field_name": "currency",
              "invoice_value": "USD", "erp_value": "EUR", "deviation_pct": None, "severity": "high"}],
         5: [{"id": 4, "validation_run": 1, "superseded": False, "field_name": "vendor_id",
              "invoice_value": "Blue Logistic Vietnam", "erp_value": None, "deviation_pct": None,
              "severity": "critical"},
             {"id": 5, "validation_run": 1, "superseded": False, "field_name": "currency",
              "invoice_value": "VND", "erp_value": "USD/EUR/INR/GBP", "deviation_pct": None,
              "severity": "critical"}]}
FEEDBACK: dict[int, list[dict]] = {}
UPLOADS: dict[str, dict] = {}
SEEN: set[str] = set()
STAGES = ["QUEUED", "EXTRACTING", "TRANSLATING", "VALIDATING", "ERP_CHECK", "REPORTING",
          "INDEXING", "COMPLETED"]
REVAL: dict[str, int] = {}
EVALS: dict[str, int] = {}


def _find(invoice_id: str) -> dict:
    for i in INVOICES:
        if i["invoice_id"] == invoice_id:
            return i
    raise HTTPException(404, detail="invoice not found")


def _n(inv: dict) -> int:
    return int(inv["invoice_id"][-12:])


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- services /api/v1
@app.get("/api/v1/invoices")
def list_invoices(page: int = 1, page_size: int = 25, vendor: str | None = None,
                  status: str | None = None, recommendation: str | None = None,
                  currency: str | None = None, q: str | None = None):
    rows = [i for i in INVOICES
            if (not vendor or i["vendor_name"] == vendor)
            and (not status or i["validation_status"] == status)
            and (not recommendation or i["recommendation"] == recommendation)
            and (not currency or i["currency"] == currency)
            and (not q or q.lower() in f"{i['invoice_number']} {i['vendor_name']}".lower())]
    rows.sort(key=lambda i: i["processed_at"], reverse=True)
    a = (page - 1) * page_size
    return {"items": rows[a:a + page_size], "total": len(rows), "page": page, "page_size": page_size}


@app.get("/api/v1/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    return _find(invoice_id)


@app.get("/api/v1/invoices/{invoice_id}/line-items")
def line_items(invoice_id: str):
    return LINES.get(_n(_find(invoice_id)), [])


@app.get("/api/v1/invoices/{invoice_id}/discrepancies")
def discrepancies(invoice_id: str, run: str = "all"):
    return DISCS.get(_n(_find(invoice_id)), [])


@app.get("/api/v1/invoices/{invoice_id}/text")
def text(invoice_id: str, kind: str = "raw"):
    inv = _find(invoice_id)
    body = f"INVOICE {inv['invoice_number']}\nVendor: {inv['vendor_name']}\nTotal: {inv['total_amount']}"
    return {"kind": kind, "text": body if kind == "translated" else f"[{inv['source_language']}] " + body}


@app.get("/api/v1/invoices/{invoice_id}/events")
def events(invoice_id: str):
    _find(invoice_id)
    return [{"stage": s, "state": "OK", "created_at": (NOW - timedelta(minutes=10 - i)).isoformat()}
            for i, s in enumerate(["DETECTED", "EXTRACT", "TRANSLATE", "PARSE", "DATA_VALIDATION",
                                   "BUSINESS_VALIDATION", "REPORT", "INDEX", "COMPLETED"])]


@app.get("/api/v1/invoices/{invoice_id}/report")
def report(invoice_id: str, format: str = "html"):
    inv = _find(invoice_id)
    if _n(inv) == 6:
        raise HTTPException(404, detail="no report")
    if format == "json":
        return {"invoice_id": invoice_id, "recommendation": inv["recommendation"]}
    html = (f"<html><body style='font-family:sans-serif'><h2>Audit report {inv['invoice_number']}</h2>"
            f"<p>Recommendation: <b>{inv['recommendation']}</b></p></body></html>")
    return Response(html, media_type="text/html")


@app.get("/api/v1/invoices/{invoice_id}/feedback")
def feedback(invoice_id: str):
    return FEEDBACK.get(_n(_find(invoice_id)), [])


class FB(BaseModel):
    field_name: str
    corrected_value: str
    corrected_by: str


@app.post("/api/v1/invoices/{invoice_id}/feedback", status_code=201)
def add_feedback(invoice_id: str, body: FB):
    inv = _find(invoice_id)
    rows = FEEDBACK.setdefault(_n(inv), [])
    row = {"id": next(_ids), "field_name": body.field_name, "original_value": "(original)",
           "corrected_value": body.corrected_value, "corrected_by": body.corrected_by,
           "corrected_at": datetime.now(UTC).isoformat(), "revalidated": False}
    rows.append(row)
    return row


@app.get("/api/v1/stats/summary")
def summary():
    rec = Counter(i["recommendation"] for i in INVOICES)
    pending = sum(1 for rows in FEEDBACK.values() for r in rows if not r["revalidated"])
    return {"total": len(INVOICES), "by_recommendation": dict(rec),
            "by_status": dict(Counter(i["validation_status"] for i in INVOICES)),
            "pending_review": rec.get("review", 0), "pending_corrections": pending,
            "avg_translation_confidence": sum(i["translation_confidence"] for i in INVOICES) / len(INVOICES)}


@app.get("/api/v1/stats/by-vendor")
def by_vendor():
    out = Counter()
    for i in INVOICES:
        out[i["vendor_name"]] += 0
    return [{"vendor": v, "invoices": sum(1 for i in INVOICES if i["vendor_name"] == v),
             "discrepancies": sum(len(DISCS.get(_n(i), [])) for i in INVOICES if i["vendor_name"] == v)}
            for v in out]


@app.get("/api/v1/stats/by-day")
def by_day():
    c = Counter(i["processed_at"][:10] for i in INVOICES)
    return [{"day": d, "count": n} for d, n in sorted(c.items())]


@app.get("/api/v1/stats/discrepancy-types")
def disc_types():
    c = Counter(d["field_name"] for rows in DISCS.values() for d in rows)
    return [{"field_name": k, "count": v} for k, v in c.items()]


@app.post("/api/v1/uploads", status_code=202)
async def uploads(files: list[UploadFile] = File(...)):
    out = []
    for f in files:
        data = await f.read()
        cs = hashlib.sha256(data).hexdigest()
        if cs in SEEN or "dup" in (f.filename or "").lower():
            out.append({"upload_id": cs[:8], "filename": f.filename, "checksum": cs,
                        "status": "DUPLICATE", "invoice_id": INVOICES[0]["invoice_id"]})
            continue
        SEEN.add(cs)
        UPLOADS[cs] = {"polls": 0, "invoice_id": INVOICES[1]["invoice_id"],
                       "fail": "fail" in (f.filename or "").lower()}
        out.append({"upload_id": cs[:8], "filename": f.filename, "checksum": cs, "status": "QUEUED"})
    return out


@app.get("/api/v1/uploads/{checksum}/status")
def upload_status(checksum: str):
    u = UPLOADS.get(checksum)
    if not u:
        raise HTTPException(404, detail="unknown upload")
    u["polls"] += 1
    idx = min(u["polls"], len(STAGES) - 1)
    if u["fail"] and idx >= 3:
        return {"checksum": checksum, "status": "FAILED", "error": "No readable text was found."}
    st = STAGES[idx]
    return {"checksum": checksum, "status": st,
            "invoice_id": u["invoice_id"] if st == "COMPLETED" else None}


# ---------------------------------------------------------------- agent
@app.post("/invoices/{invoice_id}/revalidate", status_code=202)
def revalidate(invoice_id: str):
    inv = _find(invoice_id)
    REVAL[invoice_id] = 0
    for r in FEEDBACK.get(_n(inv), []):
        r["revalidated"] = True
    inv["validation_run"] += 1
    return {"validation_run": inv["validation_run"]}


@app.get("/invoices/{invoice_id}/status")
def pipeline_status(invoice_id: str):
    n = REVAL.get(invoice_id)
    if n is None:
        return {"stage": "COMPLETED", "state": "COMPLETED"}
    REVAL[invoice_id] = n + 1
    if n >= 2:
        REVAL.pop(invoice_id)
        return {"stage": "COMPLETED", "state": "COMPLETED"}
    return {"stage": ["VALIDATING", "REPORTING"][n], "state": "RUNNING"}


class Q(BaseModel):
    question: str
    invoice_id: str | None = None
    vendor: str | None = None
    session_id: str | None = None


@app.post("/rag/query")
def rag_query(q: Q):
    if "error" in q.question.lower():
        raise HTTPException(500, detail="boom")
    structured = any(w in q.question.lower() for w in ("how many", "which invoices", "count"))
    eid = f"eval-{next(_ids)}"
    EVALS[eid] = 0
    if structured:
        ans = f"There are {len(INVOICES)} invoices; {sum(i['recommendation'] == 'reject' for i in INVOICES)} rejected."
        return {"answer": ans, "route": "sql", "sources": [], "eval_id": eid}
    inv = _find(q.invoice_id) if q.invoice_id else INVOICES[4]
    return {"answer": f"Invoice {inv['invoice_number']} was flagged because the vendor and currency "
                      f"could not be matched to ERP records [S1].",
            "route": "rag", "eval_id": eid,
            "sources": [{"invoice_id": inv["invoice_id"], "invoice_number": inv["invoice_number"],
                         "source_type": "report", "chunk_text": "Vendor not found in ERP; currency VND not accepted."}]}


@app.get("/rag/evaluations/{eval_id}")
def rag_eval(eval_id: str):
    n = EVALS.get(eval_id)
    if n is None:
        raise HTTPException(404, detail="unknown")
    EVALS[eval_id] = n + 1
    if n < 2:
        return {"state": "PENDING"}
    return {"state": "READY", "relevance": 0.91, "groundedness": 0.84, "context_relevance": 0.77}
