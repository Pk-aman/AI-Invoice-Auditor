"""Invoice Monitor agent (no LLM): detects files, checksums them, routes by type, skips duplicates."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.extraction.extract import detect_type

log = logging.getLogger(__name__)

_IGNORE_SUFFIXES = (".tmp", ".part", ".crdownload", ".swp")


def compute_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta(path: Path) -> dict:
    """Optional simulated e-mail header: INV_X.pdf -> INV_X.meta.json (absent for UI uploads)."""
    meta = path.with_name(path.stem + ".meta.json")
    if meta.is_file():
        try:
            return json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("unreadable meta file", extra={"file": str(meta)})
    return {}


def is_candidate(path: Path) -> bool:
    name = path.name
    if not path.is_file() or name.startswith(".") or name.lower().endswith(_IGNORE_SUFFIXES):
        return False
    if name.lower().endswith(".meta.json"):
        return False
    return True


class FolderWatcher:
    """Polling watcher (inotify is unreliable on Docker Desktop bind mounts).

    A file is emitted once its size has been stable for two consecutive polls.
    """

    def __init__(self, directory: Path, interval_s: float, on_file: Callable[[Path], None],
                 on_unsupported: Callable[[Path], None] | None = None):
        self.dir = directory
        self.interval = interval_s
        self.on_file = on_file
        self.on_unsupported = on_unsupported
        self._sizes: dict[Path, int] = {}
        self._emitted: set[tuple[Path, int, int]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def scan_once(self) -> list[Path]:
        ready: list[Path] = []
        if not self.dir.is_dir():
            return ready
        current = {p for p in self.dir.iterdir() if is_candidate(p)}
        for p in sorted(current):
            st = p.stat()
            key = (p, st.st_size, int(st.st_mtime))
            if key in self._emitted:
                continue
            if self._sizes.get(p) == st.st_size and st.st_size > 0:
                self._emitted.add(key)
                ready.append(p)
            else:
                self._sizes[p] = st.st_size
        for gone in set(self._sizes) - current:
            self._sizes.pop(gone, None)
        return ready

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for p in self.scan_once():
                    if detect_type(p) is None:
                        if self.on_unsupported:
                            self.on_unsupported(p)
                        continue
                    self.on_file(p)
            except Exception:  # noqa: BLE001 - the watcher must never die
                log.exception("folder scan failed")
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="folder-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
