"""Validation issue types, shared by every validator layer. No bpy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Issue:
    """One validation finding, addressed to a spec entity wherever possible."""

    level: str          # "error" | "warning"
    stage: str          # "schema" | "semantic" | "sequence" | "continuity" | "build"
    code: str
    message: str
    path: str = ""      # JSON pointer into the document
    entity_id: str | None = None

    def __str__(self) -> str:
        where = self.entity_id or self.path or "<spec>"
        return f"[{self.level}] {self.stage}/{self.code} at {where}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, issues: list[Issue]) -> "ValidationResult":
        self.issues.extend(issues)
        return self

    def strict(self) -> "ValidationResult":
        """Promote warnings to errors (CI mode)."""
        return ValidationResult([
            Issue("error", i.stage, i.code, i.message, i.path, i.entity_id)
            if i.level == "warning" else i for i in self.issues])

    def format(self) -> str:
        if not self.issues:
            return "valid, no warnings"
        return "\n".join(str(i) for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "errors": [vars(i) for i in self.errors],
                "warnings": [vars(i) for i in self.warnings]}


class SpecValidationError(Exception):
    """Raised by require_valid when a document cannot be used."""

    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(result.format())


def pointer(parts) -> str:
    out = "".join(f"/{p}" for p in parts)
    return out or "/"
