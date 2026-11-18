"""Qdrant access: collection bootstrap (with vector-size check) and thin helpers."""
from __future__ import annotations

import logging

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

log = logging.getLogger(__name__)

PAYLOAD_KEYWORD_INDEXES = ("invoice_id", "source_type", "vendor", "language")


class VectorDimMismatch(RuntimeError):
    pass


def make_client(url: str) -> QdrantClient:
    return QdrantClient(url=url, timeout=15)


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Create the single collection if absent. Refuse to start on a dimension mismatch."""
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(name, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE))
        log.info("qdrant collection created", extra={"collection": name, "dim": dim})
    else:
        info = client.get_collection(name)
        vectors = info.config.params.vectors
        size = vectors.size if hasattr(vectors, "size") else None
        if size is not None and size != dim:
            raise VectorDimMismatch(
                f"Qdrant collection {name!r} has vector size {size} but EMBEDDING_DIM={dim}. "
                "Change the embedding model back or rebuild the collection (POST /rag/reindex after dropping it)."
            )
    for field in PAYLOAD_KEYWORD_INDEXES:
        client.create_payload_index(name, field_name=field, field_schema=qm.PayloadSchemaType.KEYWORD)


def invoice_filter(invoice_id: str | None = None, vendor: str | None = None, source_type: str | None = None) -> qm.Filter | None:
    must = []
    if invoice_id:
        must.append(qm.FieldCondition(key="invoice_id", match=qm.MatchValue(value=invoice_id)))
    if vendor:
        must.append(qm.FieldCondition(key="vendor", match=qm.MatchValue(value=vendor)))
    if source_type:
        must.append(qm.FieldCondition(key="source_type", match=qm.MatchValue(value=source_type)))
    return qm.Filter(must=must) if must else None
