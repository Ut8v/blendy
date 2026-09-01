"""Drive Claude Code headlessly for the studio UI.

Spawns the `claude` CLI in print mode with stream-json output. This is the same
binary you log into, so it runs on your Claude subscription: no API key is read
or set here. Conversation continuity uses --resume with the session id the CLI
hands back on the first turn.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "director" / "studio_state.json"

ALLOWED_TOOLS = ["mcp__blendy__*", "ToolSearch", "Read", "Glob", "Grep", "Agent"]
STUDIO_PROMPT = (
    "You are working inside Blendy Studio, a browser UI. The human sees your text, every "
    "tool call, and any image returned by render_preview / read_image inline. Keep messages "
    "short and visual: after each mutation, render_preview and describe what changed. "
    "The director UI and shot panel are beside you; when the human records a camera take, "
    "use list_takes / apply_take rather than authoring a move. Stop at the human gates in "
    "agents/producer.md and ask in chat."
)


def find_claude() -> str:
    exe = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not exe or not os.path.exists(exe):
        raise RuntimeError("claude CLI not found; install Claude Code or set CLAUDE_BIN")
    return exe


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"session_id": None, "shot": None, "turns": 0}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _command(message: str, session_id: str | None, shot: str | None, agent: str | None) -> list[str]:
    cmd = [find_claude(), "-p", message, "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(ROOT / ".mcp.json"), "--strict-mcp-config", "--include-partial-messages",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED_TOOLS,
           "--append-system-prompt", STUDIO_PROMPT + (f" Current shot: {shot}." if shot else "")]
    if session_id:
        cmd += ["--resume", session_id]
    if agent:
        cmd += ["--agent", agent] if _supports_agent_flag() else []
    return cmd


_agent_flag: bool | None = None


def _supports_agent_flag() -> bool:
    global _agent_flag
    if _agent_flag is None:
        try:
            out = subprocess.run([find_claude(), "--help"], capture_output=True, text=True, timeout=20).stdout
            _agent_flag = "--agent <" in out
        except Exception:  # noqa: BLE001
            _agent_flag = False
    return _agent_flag


def _simplify(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce a stream-json event to what the UI renders."""
    t = event.get("type")
    if t == "system" and event.get("subtype") == "init":
        return [{"type": "init", "session_id": event.get("session_id")}]
    if t == "assistant":
        out = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text"):
                out.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                out.append({"type": "tool_use", "id": block.get("id"), "name": block.get("name"),
                            "input": block.get("input", {})})
        return out
    if t == "user":
        out = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") != "tool_result":
                continue
            content = block.get("content")
            text = content if isinstance(content, str) else "".join(
                c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text")
            images = _image_paths(text)
            out.append({"type": "tool_result", "tool_use_id": block.get("tool_use_id"),
                        "text": text[:4000], "is_error": bool(block.get("is_error")), "images": images})
        return out
    if t == "stream_event":
        ev = event.get("event", {})
        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            return [{"type": "delta", "text": ev["delta"].get("text", "")}]
        return []
    if t == "result":
        return [{"type": "result", "session_id": event.get("session_id"), "ok": not event.get("is_error"),
                 "text": event.get("result", ""), "cost_usd": event.get("total_cost_usd"),
                 "duration_ms": event.get("duration_ms")}]
    return []


def _image_paths(text: str) -> list[str]:
    """Preview/render paths mentioned in a tool result, made UI-servable."""
    paths = []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return paths
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and node.lower().endswith((".png", ".jpg", ".jpeg")):
            p = Path(node)
            try:
                rel = p.resolve().relative_to(ROOT)
                paths.append(str(rel))
            except ValueError:
                continue
    return paths


class ClaudeTurn:
    """One headless turn. Iterate events; stop() kills it."""

    def __init__(self, message: str, session_id: str | None = None, shot: str | None = None,
                 agent: str | None = None):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # subscription auth only
        self.proc = subprocess.Popen(_command(message, session_id, shot, agent), cwd=str(ROOT),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env, bufsize=1)
        self.session_id = session_id
        self._stderr: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())

    def events(self) -> Iterator[dict[str, Any]]:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                yield {"type": "log", "text": line[:300]}
                continue
            for e in _simplify(event):
                if e.get("session_id"):
                    self.session_id = e["session_id"]
                yield e
        code = self.proc.wait()
        if code != 0:
            yield {"type": "error", "text": "\n".join(self._stderr[-15:]) or f"claude exited {code}"}

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
