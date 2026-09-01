"""Spec validation: structural (JSON Schema) plus semantic (referential integrity).

Runs OUTSIDE Blender. Importing bpy here is a bug: this module is used by CI and
by the MCP server process, and it is the compiler's first step so that a bad spec
fails before the scene is touched.

Backend is fastjsonschema, which Blender bundles, so the same library validates in
both environments and the two can never disagree.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # invoked as a script: python compiler/validate.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "compiler"

from .issues import Issue, SpecValidationError, ValidationResult, pointer
from .refs import ProfileIndex
from .validate_semantic import semantic_issues

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
SHOT_SCHEMA = SPEC_DIR / "shot.schema.json"
BIBLE_SCHEMA = SPEC_DIR / "bible.schema.json"
SEQUENCE_SCHEMA = SPEC_DIR / "sequence.schema.json"
SCHEMA_PATH = SHOT_SCHEMA  # backwards-compatible name

SHOT_VERSION = "1.1"

__all__ = ["Issue", "ValidationResult", "SpecValidationError", "validate", "validate_file",
           "require_valid", "load_schema", "schema_issues", "load_json"]


def _fastjsonschema():
    try:
        import fastjsonschema
    except ImportError as exc:  # pragma: no cover - environment problem
        raise RuntimeError(
            "fastjsonschema is required. Use ./.venv/bin/python outside Blender "
            "(pip install fastjsonschema); inside Blender it is already bundled."
        ) from exc
    return fastjsonschema


_schema_cache: dict[str, dict[str, Any]] = {}
_compiled_cache: dict[str, Any] = {}


def load_json(path: Path | str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_schema(path: Path | str = SHOT_SCHEMA) -> dict[str, Any]:
    key = str(path)
    if key not in _schema_cache:
        _schema_cache[key] = load_json(path)
    return _schema_cache[key]


def _subvalidator(schema: dict[str, Any], sub: dict[str, Any], key: str):
    """Compile a fragment of a schema, keeping $defs resolvable."""
    cache_key = f"{schema.get('$id', id(schema))}::{key}"
    if cache_key not in _compiled_cache:
        # fastjsonschema rewrites $refs inside whatever dict it compiles, so hand
        # it a deep copy or the next fragment inherits absolute URIs it cannot fetch.
        fragment = copy.deepcopy(sub)
        fragment.pop("$id", None)
        fragment["$schema"] = schema.get("$schema")
        fragment["$defs"] = copy.deepcopy(schema.get("$defs", {}))
        _compiled_cache[cache_key] = _fastjsonschema().compile(fragment)
    return _compiled_cache[cache_key]


def schema_issues(doc: Any, schema: dict[str, Any], version: str | None = None) -> list[Issue]:
    """Validate a document per section and per array item, so one bad entity does
    not mask the rest. Works for any of the three top-level schemas."""
    fjs = _fastjsonschema()
    exc = fjs.JsonSchemaValueException
    issues: list[Issue] = []

    if not isinstance(doc, dict):
        return [Issue("error", "schema", "not_an_object",
                      f"document must be a JSON object, got {type(doc).__name__}")]

    props = schema["properties"]
    required = schema.get("required", [])
    for name in required:
        if name not in doc:
            issues.append(Issue("error", "schema", "missing_section",
                                f"required section '{name}' is missing", f"/{name}"))
    for extra in sorted(set(doc) - set(props)):
        issues.append(Issue("error", "schema", "unknown_key",
                            f"'{extra}' is not part of the format", f"/{extra}"))
    want_version = version or props.get("version", {}).get("const")
    if want_version and doc.get("version") != want_version:
        issues.append(Issue("error", "schema", "bad_version",
                            f"version must be {want_version!r}, got {doc.get('version')!r}",
                            "/version"))

    for name, sub in props.items():
        if name not in doc or name == "version":
            continue
        value = doc[name]
        if sub.get("type") == "array" and "items" in sub:
            if not isinstance(value, list):
                issues.append(Issue("error", "schema", "invalid_value",
                                    f"'{name}' must be an array", f"/{name}"))
                continue
            validate = _subvalidator(schema, sub["items"], f"{name}[]")
            for idx, item in enumerate(value):
                try:
                    validate(item)
                except exc as e:
                    eid = item.get("id") if isinstance(item, dict) else None
                    issues.append(Issue("error", "schema", "invalid_value", e.message,
                                        pointer([name, idx] + list(e.path[1:])),
                                        eid if isinstance(eid, str) else None))
        else:
            try:
                _subvalidator(schema, sub, name)(value)
            except exc as e:
                issues.append(Issue("error", "schema", "invalid_value", e.message,
                                    pointer([name] + list(e.path[1:]))))

    if issues:
        return issues
    try:  # nothing granular fired; the whole document must still pass
        _subvalidator(schema, schema, "<root>")(doc)
    except exc as e:
        issues.append(Issue("error", "schema", "invalid_value", e.message,
                            pointer(list(e.path[1:]))))
    return issues


def validate(spec: Any, schema: dict[str, Any] | None = None, strict: bool = False,
             profiles: ProfileIndex | None = None) -> ValidationResult:
    """Validate a shot spec. Semantic checks run only if the schema layer passed."""
    schema = schema if schema is not None else load_schema(SHOT_SCHEMA)
    issues = schema_issues(spec, schema, SHOT_VERSION)
    if not issues:
        profiles = profiles if profiles is not None else ProfileIndex.load()
        issues = semantic_issues(spec, profiles)
    result = ValidationResult(issues)
    return result.strict() if strict else result


def validate_file(path: Path | str, strict: bool = False,
                  profiles: ProfileIndex | None = None) -> ValidationResult:
    try:
        spec = load_json(path)
    except json.JSONDecodeError as e:
        return ValidationResult([Issue("error", "schema", "malformed_json",
                                       f"{e.msg} at line {e.lineno} column {e.colno}",
                                       str(path))])
    return validate(spec, strict=strict, profiles=profiles)


def require_valid(spec: Any, strict: bool = False,
                  profiles: ProfileIndex | None = None) -> ValidationResult:
    """Compiler step 1. Raises before anything touches the scene."""
    result = validate(spec, strict=strict, profiles=profiles)
    if not result.ok:
        raise SpecValidationError(result)
    return result


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    strict, as_json = "--strict" in argv, "--json" in argv
    if not args:
        print("usage: validate.py <spec.json> [...] [--strict] [--json]", file=sys.stderr)
        return 2
    failed = False
    for path in args:
        result = validate_file(path, strict=strict)
        if as_json:
            print(json.dumps({"spec": path, **result.to_dict()}, indent=2))
        else:
            n_w = len(result.warnings)
            print(f"{'PASS' if result.ok else 'FAIL'} {path}"
                  + (f" ({n_w} warning{'s' * (n_w != 1)})" if n_w else ""))
            for issue in result.issues:
                print(f"  {issue}")
        failed |= not result.ok
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
