"""Compact JSON formatter for the schemas: short objects and arrays stay on one
line, so a schema reads like a table instead of a 900-line waterfall.

    ./.venv/bin/python spec/format_schema.py spec/*.schema.json
"""

from __future__ import annotations

import json
import sys

WIDTH = 100


def fmt(value, indent: int = 0) -> str:
    flat = json.dumps(value, separators=(", ", ": "), ensure_ascii=False)
    if len(flat) + indent <= WIDTH or not isinstance(value, (dict, list)) or not value:
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, dict):
        items = [f'{pad}{json.dumps(k, ensure_ascii=False)}: {fmt(v, indent + 2)}' for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [f"{pad}{fmt(v, indent + 2)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


def main(paths: list[str]) -> int:
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        text = fmt(data) + "\n"
        assert json.loads(text) == data
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{p}: {text.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
