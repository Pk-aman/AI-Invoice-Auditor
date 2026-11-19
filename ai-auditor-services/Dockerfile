FROM python:3.11.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# unprivileged user (fixed uid so volume ownership is predictable)
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# mount points for the shared volumes, writable by the unprivileged user
RUN mkdir -p /data/incoming /data/reports && chown -R app:app /data

WORKDIR /srv

# 1) dependencies: changes rarely -> cached layer
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) application source: changes often -> last layer
COPY app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# long-lived foreground process; logs go to stdout/stderr
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
