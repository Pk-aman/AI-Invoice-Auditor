"""HTTP client for the mock ERP (contracts/erp-api.openapi.yaml). The ONLY way the agent reads ERP data."""
from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.schemas.models import ErpPurchaseOrder, ErpSku, ErpVendor

log = logging.getLogger(__name__)


class ErpUnavailable(RuntimeError):
    """ERP unreachable or returning 5xx. Never treated as 'no data' — the pipeline step fails instead."""


class ErpClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    @retry(retry=retry_if_exception_type(ErpUnavailable), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=0.3, max=2), reraise=True)
    def _get(self, path: str, params: dict | None = None):
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ErpUnavailable(f"ERP request failed: {path}: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 500:
            raise ErpUnavailable(f"ERP returned {resp.status_code} for {path}")
        if resp.status_code >= 400:
            raise ErpUnavailable(f"ERP rejected request {path}: {resp.status_code}")
        return resp.json()

    def get_vendor(self, vendor_id: str) -> ErpVendor | None:
        data = self._get(f"/vendor/{vendor_id}")
        return ErpVendor(**data) if data else None

    def get_po(self, po_number: str) -> ErpPurchaseOrder | None:
        data = self._get(f"/po/{po_number}")
        return ErpPurchaseOrder(**data) if data else None

    def get_sku(self, sku_code: str) -> ErpSku | None:
        data = self._get(f"/sku/{sku_code}")
        return ErpSku(**data) if data else None

    def search_vendors(self, name: str, limit: int = 5) -> list[ErpVendor]:
        data = self._get("/api/v1/erp/vendors/search", {"name": name, "limit": limit}) or []
        return [ErpVendor(**d) for d in data]

    def list_pos(self, vendor_id: str) -> list[ErpPurchaseOrder]:
        data = self._get("/api/v1/erp/pos", {"vendor_id": vendor_id}) or []
        return [ErpPurchaseOrder(**d) for d in data]

    def health(self) -> bool:
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False
