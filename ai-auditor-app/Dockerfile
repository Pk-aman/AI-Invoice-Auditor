# ai-auditor-app: Streamlit UI. Image: ai-auditor/ai-auditor-app:1.0.0
FROM python:3.11.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# unprivileged user
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /srv app

WORKDIR /srv

# 1) dependencies (rebuilt only when requirements.txt changes)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) application code + streamlit config (changes most often -> last)
COPY .streamlit/ .streamlit/
COPY app/ app/
RUN chown -R app:app /srv

USER app
EXPOSE 8501

# python (not curl) so no extra apt layer; localhost here is the container itself
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

# foreground, logs to stdout/stderr, bound to 0.0.0.0
CMD ["streamlit", "run", "app/main.py", "--server.address=0.0.0.0", "--server.port=8501"]
