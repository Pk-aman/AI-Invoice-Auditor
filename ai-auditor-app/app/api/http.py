"""Shared HTTP plumbing: clients, friendly errors, schema-drift detection."""
from __future__ import annotations

import logging
from typing import Any, TypeVar

import httpx
import streamlit as st
from pydantic import BaseModel, ValidationError

from app import ui_text as T
from app.config import get_settings

log = logging.getLogger("ai-auditor-app")
M = TypeVar("M", bound=BaseModel)


class UiError(Exception):
    """An error that is safe to show to a non-technical auditor."""

    def __init__(self, message: str, *, status: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def build_client(base_url: str, read_timeout: float) -> httpx.Client:
    timeout = httpx.Timeout(read_timeout, connect=get_settings().connect_timeout_s)
    # retries apply to connection errors only (never to a request that reached the server)
    return httpx.Client(base_url=base_url, timeout=timeout, transport=httpx.HTTPTransport(retries=2))


@st.cache_resource(show_spinner=False)
def cached_client(base_url: str, read_timeout: float) -> httpx.Client:
    return build_client(base_url, read_timeout)


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(body.get("detail"), str):
            return body["detail"]
    return resp.text[:200]


def send(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    try:
        resp = client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        log.warning("timeout calling %s %s", method, path)
        raise UiError(T.ERR_TIMEOUT, detail=str(exc)) from exc
    except httpx.TransportError as exc:
        log.warning("cannot reach backend for %s %s: %s", method, path, exc)
        raise UiError(T.ERR_UNREACHABLE, detail=str(exc)) from exc
    if resp.status_code >= 400:
        msg = _error_message(resp)
        if resp.status_code == 404:
            raise UiError(T.ERR_NOT_FOUND, status=404, detail=msg)
        if resp.status_code in (409, 422):
            raise UiError(msg or T.ERR_REJECTED, status=resp.status_code, detail=msg)
        raise UiError(T.ERR_SERVER, status=resp.status_code, detail=msg)
    return resp


def parse(model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        log.error("backend response did not match %s: %s", model.__name__, exc)
        raise UiError(T.ERR_VERSION_MISMATCH, detail=str(exc)) from exc


def parse_list(model: type[M], data: Any) -> list[M]:
    if not isinstance(data, list):
        raise UiError(T.ERR_VERSION_MISMATCH, detail=f"expected a list, got {type(data).__name__}")
    return [parse(model, row) for row in data]
