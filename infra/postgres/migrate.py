#!/usr/bin/env python3
"""Apply schema migrations to an EXISTING database (fresh installs get the final schema from init/*.sql).

Run (admin credentials come from the root .env via the `migrate` compose profile):

    docker compose --profile migrate run --rm db-migrate

Each migration runs in ONE transaction: a failure rolls everything back and leaves the database untouched.
Migration 001 backfills audit.invoice_audit.content_checksum from the stored translated text using the exact
normalisation the agent uses (lower-case, whitespace-collapsed, SHA-256). A row without translated text, or two
rows whose normalised text collide, aborts the migration with a clear message: checksums are never invented.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent / "migrations"


def content_checksum(translated_text: str) -> str:
    normalized = " ".join(translated_text.lower().split())  # must match agent-service/app/core/normalize.py
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def backfill_content_checksums(conn: psycopg.Connection) -> int:
    rows = conn.execute("SELECT invoice_id, translated_text FROM audit.invoice_audit WHERE content_checksum IS NULL").fetchall()
    missing = [str(r[0]) for r in rows if not (r[1] or "").strip()]
    if missing:
        raise SystemExit(f"ABORT: {len(missing)} invoice(s) have no translated_text, cannot compute content_checksum: {missing[:10]}")
    seen: dict[str, str] = {r[0]: str(r[1]) for r in conn.execute(
        "SELECT content_checksum, invoice_id FROM audit.invoice_audit WHERE content_checksum IS NOT NULL").fetchall()}
    for invoice_id, text in rows:
        digest = content_checksum(text)
        if digest in seen:
            raise SystemExit(f"ABORT: invoices {seen[digest]} and {invoice_id} have equivalent translated content "
                             "(duplicate); resolve the duplicate before migrating.")
        seen[digest] = str(invoice_id)
        conn.execute("UPDATE audit.invoice_audit SET content_checksum=%s WHERE invoice_id=%s", (digest, invoice_id))
    return len(rows)


def migration_001(conn: psycopg.Connection) -> None:
    conn.execute((HERE / "001_two_write_lifecycle_pre.sql").read_text(encoding="utf-8"))
    n = backfill_content_checksums(conn)
    print(f"  backfilled content_checksum for {n} invoice(s)")
    conn.execute((HERE / "001_two_write_lifecycle_post.sql").read_text(encoding="utf-8"))


MIGRATIONS = [(1, "two_write_lifecycle", migration_001)]


def database_url() -> str:
    if url := os.environ.get("MIGRATION_DATABASE_URL"):
        return url
    user, pw, db = (os.environ.get(k) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"))
    if not (user and pw and db):
        raise SystemExit("set MIGRATION_DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB (admin role)")
    return f"postgresql://{user}:{pw}@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/{db}"


def main() -> int:
    with psycopg.connect(database_url()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS audit.schema_migrations "
                     "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        conn.commit()
        applied = {r[0] for r in conn.execute("SELECT version FROM audit.schema_migrations").fetchall()}
        for version, name, fn in MIGRATIONS:
            if version in applied:
                print(f"migration {version:03d} {name}: already applied")
                continue
            print(f"migration {version:03d} {name}: applying")
            with conn.transaction():
                fn(conn)
                conn.execute("INSERT INTO audit.schema_migrations (version, name) VALUES (%s, %s)", (version, name))
            print(f"migration {version:03d} {name}: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
