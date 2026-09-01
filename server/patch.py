"""JSON Patch (RFC 6902) on plain Python data. add / remove / replace / move /
copy / test. Operates on a deep copy; the caller validates the result before
committing it, so a failed patch never leaves a half-applied spec.

Paths are JSON pointers (RFC 6901). A convenience: array elements may be addressed
by id, "/assets/id=hero_block/transform", because the agent edits by id and the
array index is not a stable handle.
"""

from __future__ import annotations

import copy
from typing import Any


class PatchError(ValueError):
    def __init__(self, index: int, op: dict[str, Any], message: str):
        self.index, self.op = index, op
        super().__init__(f"operation {index} ({op.get('op')} {op.get('path')}): {message}")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _split(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError(f"pointer must start with '/': {path!r}")
    return [_unescape(t) for t in path.split("/")[1:]]


def _step(container: Any, token: str, for_insert: bool = False) -> Any:
    """Resolve one token against a container to a key/index."""
    if isinstance(container, list):
        if token.startswith("id="):
            wanted = token[3:]
            for i, item in enumerate(container):
                if isinstance(item, dict) and item.get("id") == wanted:
                    return i
            raise KeyError(f"no element with id '{wanted}'")
        if token == "-":
            if not for_insert:
                raise KeyError("'-' is only valid as an insert target")
            return len(container)
        if not token.isdigit():
            raise KeyError(f"array index must be a number or id=<id>, got {token!r}")
        idx = int(token)
        if idx > len(container) or (idx == len(container) and not for_insert):
            raise KeyError(f"index {idx} out of range (length {len(container)})")
        return idx
    if isinstance(container, dict):
        return token
    raise KeyError(f"cannot descend into {type(container).__name__}")


def _resolve(doc: Any, tokens: list[str]) -> tuple[Any, Any]:
    """Walk to the parent of the last token. Returns (parent, key)."""
    if not tokens:
        raise KeyError("root cannot be the target of this operation")
    node = doc
    for token in tokens[:-1]:
        key = _step(node, token)
        if isinstance(node, dict) and key not in node:
            raise KeyError(f"'{key}' does not exist")
        node = node[key]
    return node, tokens[-1]


def get(doc: Any, path: str) -> Any:
    tokens = _split(path)
    node = doc
    for token in tokens:
        key = _step(node, token)
        if isinstance(node, dict) and key not in node:
            raise KeyError(f"'{key}' does not exist")
        node = node[key]
    return node


def _add(doc, path, value):
    tokens = _split(path)
    if not tokens:
        return copy.deepcopy(value)
    parent, last = _resolve(doc, tokens)
    if isinstance(parent, list):
        parent.insert(_step(parent, last, for_insert=True), copy.deepcopy(value))
    elif isinstance(parent, dict):
        parent[last] = copy.deepcopy(value)
    else:
        raise KeyError(f"cannot add into {type(parent).__name__}")
    return doc


def _remove(doc, path):
    parent, last = _resolve(doc, _split(path))
    key = _step(parent, last)
    if isinstance(parent, dict) and key not in parent:
        raise KeyError(f"'{key}' does not exist")
    removed = parent[key]
    del parent[key]
    return removed


def _replace(doc, path, value):
    tokens = _split(path)
    if not tokens:
        return copy.deepcopy(value)
    parent, last = _resolve(doc, tokens)
    key = _step(parent, last)
    if isinstance(parent, dict) and key not in parent:
        raise KeyError(f"'{key}' does not exist; use add")
    parent[key] = copy.deepcopy(value)
    return doc


def apply_patch(doc: Any, operations: list[dict[str, Any]]) -> Any:
    """Return a patched deep copy. Raises PatchError on the first bad operation."""
    out = copy.deepcopy(doc)
    for i, op in enumerate(operations):
        kind = op.get("op")
        path = op.get("path")
        if not isinstance(path, str):
            raise PatchError(i, op, "missing path")
        try:
            if kind == "add":
                out = _add(out, path, op["value"])
            elif kind == "remove":
                _remove(out, path)
            elif kind == "replace":
                out = _replace(out, path, op["value"])
            elif kind == "move":
                value = _remove(out, op["from"])
                out = _add(out, path, value)
            elif kind == "copy":
                out = _add(out, path, copy.deepcopy(get(out, op["from"])))
            elif kind == "test":
                if get(out, path) != op["value"]:
                    raise PatchError(i, op, "test failed: value differs")
            else:
                raise PatchError(i, op, f"unknown op {kind!r}")
        except PatchError:
            raise
        except (KeyError, ValueError, IndexError, TypeError) as e:
            raise PatchError(i, op, str(e).strip("'")) from e
    return out
