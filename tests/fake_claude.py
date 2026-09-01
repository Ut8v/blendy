#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests: echoes its argv as stream-json events,
including a tool_use + tool_result pair pointing at a preview image."""
import json
import sys

args = sys.argv[1:]
msg = args[args.index("-p") + 1] if "-p" in args else ""
sid = args[args.index("--resume") + 1] if "--resume" in args else "sess-fake-0001"
if "--help" in args:
    print("  --agent <name>   fake"); sys.exit(0)
ev = [
    {"type": "system", "subtype": "init", "session_id": sid},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": f"echo: {msg}"},
        {"type": "tool_use", "id": "tu1", "name": "mcp__blendy__render_preview", "input": {"quality": "fast"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1",
         "content": [{"type": "text", "text": json.dumps({"ok": True, "outputs": ["preview/x/camera_fast_f0001.png"]})}]}]}},
    {"type": "result", "subtype": "success", "session_id": sid, "result": "done",
     "is_error": False, "total_cost_usd": 0.0, "duration_ms": 5, "argv": args},
]
for e in ev:
    print(json.dumps(e), flush=True)
