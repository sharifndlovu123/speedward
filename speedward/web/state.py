"""In-flight scan state, in memory only. Nothing here is written to disk or logged.

An entry holds the random id, a status, short progress lines and, once finished, the result until
the browser's first successful fetch. A finished entry that is never fetched is dropped after
FINISHED_TTL seconds; a scan that never finishes is dropped after STUCK_AFTER seconds.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

FINISHED_TTL = 120
STUCK_AFTER = 900  # a scan is bounded by Globalping's wait timeouts; this is only a safety net
MAX_PROGRESS_LINES = 50


@dataclass
class Entry:
    id: str
    created: float
    status: str = "running"  # running | done | error
    progress: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    finished: float | None = None

    def add_progress(self, line: str) -> None:
        if len(self.progress) < MAX_PROGRESS_LINES:
            self.progress.append(line)


class ScanState:
    def __init__(self, max_concurrent: int = 2, clock=time.monotonic):
        self._max = max_concurrent
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}

    def __len__(self) -> int:
        with self._lock:
            self._sweep()
            return len(self._entries)

    def _sweep(self) -> None:
        now = self._clock()
        for key, e in list(self._entries.items()):
            if (e.finished is not None and now - e.finished > FINISHED_TTL) or now - e.created > STUCK_AFTER:
                del self._entries[key]

    def create(self) -> Entry | None:
        """A new running entry, or None when MAX_CONCURRENT_SCANS scans are already running."""
        with self._lock:
            self._sweep()
            if sum(1 for e in self._entries.values() if e.status == "running") >= self._max:
                return None
            entry = Entry(secrets.token_urlsafe(16), self._clock())
            self._entries[entry.id] = entry
            return entry

    def get(self, scan_id: str) -> Entry | None:
        with self._lock:
            self._sweep()
            return self._entries.get(scan_id)

    def take(self, scan_id: str) -> Entry | None:
        """Remove and return a finished entry (its one and only fetch)."""
        with self._lock:
            self._sweep()
            entry = self._entries.get(scan_id)
            if entry is None or entry.status == "running":
                return None
            return self._entries.pop(scan_id)

    def finish(self, scan_id: str, result: dict) -> None:
        self._end(scan_id, "done", result=result)

    def fail(self, scan_id: str, message: str) -> None:
        self._end(scan_id, "error", error=message)

    def _end(self, scan_id: str, status: str, result: dict | None = None, error: str = "") -> None:
        with self._lock:
            entry = self._entries.get(scan_id)
            if entry is None:  # already swept: drop the outcome
                return
            entry.result, entry.error = result, error
            entry.finished = self._clock()
            entry.status = status


_lock = threading.Lock()
_state: ScanState | None = None


def get() -> ScanState:
    from django.conf import settings

    global _state
    with _lock:
        if _state is None:
            _state = ScanState(settings.MAX_CONCURRENT_SCANS)
        return _state


def reset() -> None:
    global _state
    with _lock:
        _state = None
