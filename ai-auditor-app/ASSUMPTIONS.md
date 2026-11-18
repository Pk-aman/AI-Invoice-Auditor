# Backend contract assumptions (reconcile against the real OpenAPI)

The UI codes against the documented plans, not a live backend. `app/models.py` holds every
assumed shape; `tests/fake_backend.py` mimics both backends (`uvicorn tests.fake_backend:app --port 8765`).

**ai-auditor-services (`/api/v1`)**
- `GET /invoices` → `{items:[Invoice], total, page, page_size}`; query params `vendor, status, recommendation, currency, date_from, date_to, q, page, page_size, sort`.
- Invoice: `invoice_id, invoice_number, vendor_name, vendor_id, po_number, invoice_date, currency, total_amount, validation_status (passed|partial|failed|null), recommendation (approve|review|reject|null), source_language, translation_confidence (0–1), validation_run, processed_at`.
- `line-items` → `[{line_number,item_code,description,quantity,unit_price,line_total}]`.
- `discrepancies` → `[{id,validation_run,superseded,field_name,invoice_value,erp_value,deviation_pct,severity}]`; line-level mismatches use `field_name = "line:{n}:{column}"` (same naming as feedback).
- `text?kind=raw|translated` → `{kind,text}`; `events` → `[{stage,state,detail,created_at}]`; `report?format=html|json` → file body (404 when none).
- `feedback` GET → `[{id,field_name,original_value,corrected_value,corrected_by,corrected_at,revalidated}]`; POST body `{field_name,corrected_value,corrected_by}` → 201 row. Header fields: `invoice_number, vendor_name, po_number, invoice_date, currency, total_amount`; line fields `line:{n}:{item_code|description|quantity|unit_price|line_total}`.
- `stats/summary` → `{total, by_recommendation{approve,review,reject}, by_status, pending_review, pending_corrections, avg_translation_confidence}`; `stats/by-vendor` → `[{vendor,invoices,discrepancies}]`; `stats/by-day` → `[{day,count}]`; `stats/discrepancy-types` → `[{field_name,count}]`. (No vendor-list endpoint: vendor filter uses `by-vendor`.)
- `POST /uploads` multipart field name **`files`** (repeatable) → `[{upload_id,filename,checksum,status,invoice_id?,detail?}]` (status `QUEUED|DUPLICATE|REJECTED`); `GET /uploads/{checksum}/status` → `{checksum,status,invoice_id?,error?}` with status one of `QUEUED, EXTRACTING, TRANSLATING, PARSING, VALIDATING, ERP_CHECK, REPORTING, INDEXING, COMPLETED, FAILED` (raw event stage names such as `EXTRACT`, `BUSINESS_VALIDATION` are also mapped; unknown values are shown prettified).

**agent-service**
- `POST /invoices/{id}/revalidate` → `{validation_run}`; `GET /invoices/{id}/status` → `{stage,state,error?}` with state `QUEUED|RUNNING|COMPLETED|FAILED`.
- `POST /rag/query` `{question,invoice_id,vendor,session_id}` → `{answer, route (sql|structured|database ⇒ "from database", anything else ⇒ "from documents"), sources:[{invoice_id,invoice_number,source_type (invoice|report),chunk_text}], eval_id}`.
- `GET /rag/evaluations/{eval_id}` → `{state: PENDING|READY|FAILED, relevance, groundedness, context_relevance}` (0–1).

Errors: `{"error":{"code","message"}}` or FastAPI `{"detail"}` both handled; 404 = "not found" message, 5xx = generic message, network failure = "can't reach service".
