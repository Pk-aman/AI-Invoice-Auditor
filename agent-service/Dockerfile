# agent-service — LangGraph pipeline, extraction/OCR, translation, validation, reporting, RAG
# Build:  docker build -t ai-auditor/agent-service:1.0.0 agent-service/
FROM python:3.11.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 1) System packages (changes rarely): Tesseract + the OCR language packs (English, Spanish, German, French, and
#    Vietnamese for the Vietnamese sample). PDF pages are rendered by PyMuPDF, so Poppler is no longer needed.
#    Cache cleaned in the SAME layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-vie \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# 2) Unprivileged user + data directories (the reports volume inherits this ownership on first mount)
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data/incoming /data/reports /config \
    && chown -R app:app /app /data

WORKDIR /app

# 3) Python dependencies (change occasionally) — before the application source so source edits don't reinstall them
COPY requirements.txt .
# Image size: drop what the runtime never imports (package test suites, bytecode, C headers/Cython sources, the LiteLLM
# proxy web UI, PyMuPDF dev headers) in the SAME layer as the install.
RUN pip install -r requirements.txt \
    && SP=/usr/local/lib/python3.11/site-packages \
    && find "$SP" -mindepth 2 -type d \( -name tests -o -name test \) -prune -exec rm -rf {} + \
    && rm -rf "$SP/litellm/proxy/_experimental" "$SP/pyarrow/include" "$SP/pymupdf/mupdf-devel" \
    && find "$SP" \( -name "*.pyx" -o -name "*.pxd" -o -name "*.pxi" -o -name "*.pyi" \) -delete \
    && find /usr/local/lib/python3.11 -name "__pycache__" -type d -prune -exec rm -rf {} +

# 4) Application source (changes constantly) — last
COPY --chown=app:app app/ ./app/

USER app
EXPOSE 8000

# /ready = the /health dependencies (Postgres, Qdrant, Redis, mock-erp) PLUS the LLM configuration: the container is
# only 'healthy' when it can actually process invoices. (/health alone omits the LLM check.)
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"

# Long-lived foreground process; logs go to stdout/stderr only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
