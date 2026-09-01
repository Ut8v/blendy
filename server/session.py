"""The working spec: one writer, validated on every mutation, checkpointed on
every accepted step. A bad step costs one step, never the session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from compiler.issues import ValidationResult
from compiler.patch_compat import apply_patch, PatchError  # noqa: F401  (re-export path)
from compiler.refs import ProfileIndex
from compiler.validate import load_json, validate

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DIR = ROOT / "checkpoints"
_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


class WriteLockHeld(RuntimeError):
    pass


class Session:
    """Holds the current spec for one shot. Exactly one holder of the write lock
    (hard rule 9); specialists take it in sequence."""

    def __init__(self, spec_path: Path | str, profiles: ProfileIndex | None = None):
        self.spec_path = Path(spec_path)
        self.spec: dict[str, Any] = load_json(self.spec_path)
        self.profiles = profiles if profiles is not None else ProfileIndex.load()
        self._lock = threading.Lock()
        self.writer: str | None = None
        self.history: list[dict[str, Any]] = []
        self.blend_path: str | None = None
        self.last_fingerprint: str | None = None

    # --- write lock ---------------------------------------------------------------

    def acquire(self, agent: str) -> None:
        with self._lock:
            if self.writer not in (None, agent):
                raise WriteLockHeld(f"spec is held by '{self.writer}'; specialists run in sequence")
            self.writer = agent

    def release(self, agent: str) -> None:
        with self._lock:
            if self.writer == agent:
                self.writer = None

    def _check_writer(self, agent: str | None) -> None:
        if self.writer is not None and agent is not None and agent != self.writer:
            raise WriteLockHeld(f"spec is held by '{self.writer}'")

    # --- state -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.spec.get("sequence", {}).get("shot_id") or self.spec["meta"]["name"]

    def validate(self) -> ValidationResult:
        return validate(self.spec, profiles=self.profiles)

    def patch(self, operations: list[dict[str, Any]], agent: str | None = None
              ) -> tuple[ValidationResult, dict[str, Any] | None]:
        """Apply, validate, commit only if valid. Returns (result, new_spec or None)."""
        self._check_writer(agent)
        try:
            candidate = apply_patch(self.spec, operations)
        except PatchError as e:
            from compiler.issues import Issue
            return ValidationResult([Issue("error", "patch", "bad_operation", str(e), e.op.get("path", ""))]), None
        result = validate(candidate, profiles=self.profiles)
        if not result.ok:
            return result, None
        self.history.append({"ops": operations, "hash": spec_hash(candidate), "at": time.time(),
                             "agent": agent})
        self.spec = candidate
        self.save()
        return result, candidate

    def replace(self, spec: dict[str, Any], agent: str | None = None) -> ValidationResult:
        self._check_writer(agent)
        result = validate(spec, profiles=self.profiles)
        if result.ok:
            self.spec = spec
            self.save()
        return result

    def save(self) -> None:
        os.makedirs(self.spec_path.parent, exist_ok=True)
        tmp = self.spec_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.spec, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, self.spec_path)

    # --- checkpoints (hard rule 5) -------------------------------------------------------

    def checkpoint_dir(self) -> Path:
        return CHECKPOINT_DIR / self.name

    def checkpoint(self, label: str) -> dict[str, Any]:
        if not _LABEL.match(label):
            raise ValueError("label must be [A-Za-z0-9_.-], max 64 chars")
        d = self.checkpoint_dir()
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = d / f"{stamp}_{label}"
        spec_out = base.with_suffix(".spec.json")
        with open(spec_out, "w", encoding="utf-8") as fh:
            json.dump(self.spec, fh, indent=2)
        blend_out = None
        if self.blend_path and os.path.exists(self.blend_path):
            blend_out = str(base.with_suffix(".blend"))
            shutil.copyfile(self.blend_path, blend_out)
        meta = {"label": label, "created": time.time(), "spec": str(spec_out), "blend": blend_out,
                "hash": spec_hash(self.spec), "fingerprint": self.last_fingerprint}
        with open(base.with_suffix(".meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        return meta

    def list_checkpoints(self) -> list[dict[str, Any]]:
        d = self.checkpoint_dir()
        if not d.exists():
            return []
        out = []
        for m in sorted(d.glob("*.meta.json")):
            with open(m, "r", encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    def restore(self, label: str, agent: str | None = None) -> dict[str, Any]:
        self._check_writer(agent)
        matches = [c for c in self.list_checkpoints() if c["label"] == label]
        if not matches:
            have = ", ".join(c["label"] for c in self.list_checkpoints()) or "none"
            raise FileNotFoundError(f"no checkpoint '{label}' (have: {have})")
        meta = matches[-1]
        self.spec = load_json(meta["spec"])
        self.save()
        if meta.get("blend") and os.path.exists(meta["blend"]):
            self.blend_path = meta["blend"]
        return meta
