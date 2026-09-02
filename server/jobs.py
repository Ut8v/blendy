"""Background jobs.

Anything that shells out to Blender takes seconds to minutes. Running it inside
an HTTP request means the browser sits on a dead connection with nothing to show,
so those calls run on a thread and the UI watches them instead.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

_LOCK = threading.Lock()
_JOBS: list["Job"] = []
MAX_KEPT = 40


class Job:
    def __init__(self, kind: str, label: str, fn: Callable[[], Any]):
        self.id = f"job_{uuid.uuid4().hex[:8]}"
        self.kind, self.label = kind, label
        self.started = time.time()
        self.ended: float | None = None
        self.ok: bool | None = None
        self.result: Any = None
        self.error: str | None = None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            self.result = fn()
            failed = isinstance(self.result, dict) and (
                self.result.get("ok") is False or self.result.get("applied") is False)
            self.ok = not failed
            if failed:
                self.error = self.result.get("error") or "; ".join(
                    f"{e.get('code')} @ {e.get('entity_id') or e.get('path')}"
                    for e in (self.result.get("errors") or [])) or "failed"
        except Exception as e:  # noqa: BLE001
            self.ok = False
            self.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            self.ended = time.time()

    @property
    def running(self) -> bool:
        return self.ended is None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "started": self.started, "ended": self.ended, "ok": self.ok,
                "error": (self.error or "")[:400], "running": self.running,
                "seconds": round((self.ended or time.time()) - self.started, 1),
                "result": self.result if not self.running else None}


def start(kind: str, label: str, fn: Callable[[], Any]) -> Job:
    job = Job(kind, label, fn)
    with _LOCK:
        _JOBS.append(job)
        del _JOBS[:-MAX_KEPT]
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return next((j for j in _JOBS if j.id == job_id), None)


def listing(limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        return [j.to_dict() for j in _JOBS[-limit:]][::-1]


def running() -> list[dict[str, Any]]:
    with _LOCK:
        return [j.to_dict() for j in _JOBS if j.running]
