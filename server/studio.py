"""Blendy Studio: one local UI for chat, previews, director mode and the shot
panel. stdlib HTTP + SSE. Claude runs headless on your subscription via
server/claude_runner.py; Blender runs through the same tools the MCP server uses.

    ./.venv/bin/python -m server.studio [--port 8765] [--shot s01_002]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.validate import load_json                            # noqa: E402
from server import claude_runner, director                          # noqa: E402
from server.state import get_state                                  # noqa: E402
from server.tools import director_tools, model_tools, sequence_tools, spec_tools  # noqa: E402

WEB = ROOT / "director" / "web"
SERVABLE = ("preview", "renders", "director/proxies", "evals/results", "evals/references",
            "assets/references")
_turn_lock = threading.Lock()


class Turn:
    """A headless Claude turn that outlives the browser.

    Modeling runs take half an hour; a page reload must not kill one. The turn
    runs on its own thread into a buffer, and any number of clients attach to
    that buffer and replay from wherever they left off.
    """

    def __init__(self, message: str, session_id: str | None, shot: str | None, agent: str | None):
        self.events: list[dict] = []
        self.done = False
        self.shot = shot
        self._lock = threading.Lock()
        self._turn = claude_runner.ClaudeTurn(message, session_id, shot, agent)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            for event in self._turn.events():
                with self._lock:
                    self.events.append(event)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.events.append({"type": "error", "text": f"{type(e).__name__}: {e}"})
        state = claude_runner.load_state()
        state.update({"session_id": self._turn.session_id, "shot": self.shot,
                      "turns": state.get("turns", 0) + 1})
        claude_runner.save_state(state)
        with self._lock:
            self.events.append({"type": "done"})
            self.done = True

    def since(self, index: int) -> list[dict]:
        with self._lock:
            return self.events[index:]

    def stop(self) -> None:
        self._turn.stop()


_current_turn: Turn | None = None


def _shots() -> list[dict]:
    out = []
    db = get_state().db
    states = {r["shot_id"]: r for r in db.shot_states()}
    for p in sorted((ROOT / "sequence" / "shots").glob("*.json")) + sorted((ROOT / "spec" / "scenes").glob("*.json")):
        spec = load_json(p)
        sid = p.stem
        st = states.get(sid, {})
        out.append({"id": sid, "kind": "shot" if "sequence" in spec else "scene",
                    "frames": spec["meta"]["frame_end"] - spec["meta"]["frame_start"] + 1,
                    "render_state": st.get("render_state", "pending"),
                    "frames_done": st.get("frames_done", 0)})
    return out


def _models() -> list[dict]:
    out = []
    for m in model_tools.list_models():
        d = ROOT / "preview" / "models" / m["id"]
        files = sorted(d.glob("*.png")) if d.exists() else []
        m["previews"] = [{"view": f.stem, "path": f"preview/models/{m['id']}/{f.name}",
                          "mtime": f.stat().st_mtime} for f in files]
        m["reference"] = None
        try:
            ref = model_tools.read_model(m["id"]).get("reference")
            m["reference"] = ref if ref and (ROOT / ref).exists() else None
        except Exception:  # noqa: BLE001
            pass
        out.append(m)
    return out


def _previews(shot: str) -> list[dict]:
    d = ROOT / "preview" / shot
    if not d.exists():
        return []
    files = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"path": f"preview/{shot}/{f.name}", "angle": f.name.split("_")[0],
             "mtime": f.stat().st_mtime} for f in files[:60]]


def _renders(shot: str) -> dict:
    d = ROOT / "renders" / shot
    frames = sorted(d.glob("frame_*.png")) if d.exists() else []
    return {"count": len(frames), "frames": [f"renders/{shot}/{f.name}" for f in frames[:400]]}


def _shot_info(shot: str) -> dict:
    st = get_state()
    s = st.open(shot)
    spec = s.spec
    bible = load_json(ROOT / "sequence" / "bible.json") if (ROOT / "sequence" / "bible.json").exists() else None
    style = (bible or {}).get("style", {})
    proxy = director.PROXY_DIR / f"{shot}.glb"
    info = {"shot_id": shot, "valid": s.validate().to_dict(), "fps": spec["meta"]["fps"],
            "frame_start": spec["meta"]["frame_start"], "frame_end": spec["meta"]["frame_end"],
            "lens_set": style.get("lens_set", [24, 35, 50, 85]), "sensor_width": style.get("sensor_width", 36.0),
            "aspect": spec["render"]["resolution"][0] / spec["render"]["resolution"][1],
            "proxy": f"/files/director/proxies/{shot}.glb" if proxy.exists() else None,
            "cameras": [c["id"] for c in spec["cameras"]], "takes": director.list_takes(shot),
            "checkpoints": s.list_checkpoints(), "previews": _previews(shot), "renders": _renders(shot),
            "sequence": spec.get("sequence")}
    if spec.get("sequence"):
        try:
            info["resolved"] = sequence_tools.read_shot(shot)["resolved"]
        except Exception as e:  # noqa: BLE001
            info["resolved_error"] = str(e)
    return info


def _overview() -> dict:
    bible_p, bd_p = ROOT / "sequence" / "bible.json", ROOT / "sequence" / "breakdown.json"
    bible = load_json(bible_p) if bible_p.exists() else None
    bd = load_json(bd_p) if bd_p.exists() else None
    return {"shots": _shots(), "studio": claude_runner.load_state(),
            "bible": {"title": bible["title"], "cast": [c["id"] for c in bible["cast"]],
                      "locations": [l["id"] for l in bible["locations"]],
                      "looks": [l["id"] for l in bible["looks"]]} if bible else None,
            "breakdown": {"approved": bool(bd.get("approved")), "shots": [
                {"id": s["id"], "beat": s["beat"], "shot_type": s["shot_type"], "cast": s["cast"],
                 "duration_frames": s["duration_frames"]} for s in bd["shots"]]} if bd else None,
            "claude": bool(os.environ.get("CLAUDE_BIN")) or claude_runner.shutil.which("claude") is not None}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def end_headers(self):
        # the UI is edited live; never let the browser cache a stale bundle
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, *a):
        pass

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _safe(self, fn):
        try:
            self._json(200, fn())
        except Exception as e:  # noqa: BLE001
            self._json(400, {"error": f"{type(e).__name__}: {e}"})

    # --- GET ---------------------------------------------------------------------

    def _serve_index(self) -> None:
        """Stamp asset URLs with their mtime. Module and stylesheet caching is
        otherwise impossible to shake during development."""
        html = (WEB / "index.html").read_text(encoding="utf-8")
        def stamp(m):
            f = WEB / m.group(1)
            v = int(f.stat().st_mtime) if f.exists() else 0
            return f'{m.group(1)}?v={v}'
        html = re.sub(r'(studio\.(?:js|css)|models\.js|chat\.js|panels\.js|director\.js)(?:\?v=\d+)?', stamp, html)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._serve_index()
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/overview":
            return self._safe(_overview)
        if url.path == "/api/chat/state":
            t = _current_turn
            return self._json(200, {"running": bool(t and not t.done),
                                    "events": len(t.events) if t else 0,
                                    **claude_runner.load_state()})
        if url.path == "/api/models":
            return self._safe(_models)
        if url.path == "/api/model":
            return self._safe(lambda: model_tools.read_model(q["id"]))
        if url.path == "/api/shot":
            return self._safe(lambda: _shot_info(q["id"]))
        if url.path.startswith("/files/"):
            return self._serve_file(url.path[len("/files/"):])
        if url.path.startswith("/proxy/"):
            return self._serve_file("director/proxies/" + os.path.basename(url.path))
        super().do_GET()

    def _serve_file(self, rel: str) -> None:
        rel = os.path.normpath(rel)
        if rel.startswith("..") or not rel.startswith(SERVABLE):
            return self.send_error(403)
        p = ROOT / rel
        if not p.is_file():
            return self.send_error(404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # --- POST ---------------------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat":
            return self._chat(self._body())
        if path == "/api/chat/attach":
            return self._stream(int(self._body().get("from", 0)))
        if path == "/api/chat/stop":
            if _current_turn:
                _current_turn.stop()
            return self._json(200, {"stopped": True})
        if path == "/api/chat/reset":
            st = claude_runner.load_state()
            st.update({"session_id": None, "turns": 0})
            claude_runner.save_state(st)
            return self._json(200, st)
        if path == "/api/take":
            b = self._body()
            return self._safe(lambda: self._save_take(b))
        if path == "/api/breakdown/approve":
            return self._safe(self._approve)
        if path == "/api/action":
            b = self._body()
            return self._safe(lambda: self._action(b))
        self.send_error(404)

    def _save_take(self, b: dict) -> dict:
        shot = b["shot"]
        spec = get_state().open(shot).spec
        bible_p = ROOT / "sequence" / "bible.json"
        lens = load_json(bible_p)["style"]["lens_set"] if bible_p.exists() else []
        take = director.save_take(shot, b["mode"], b["samples"], spec["meta"]["fps"], lens, get_state().db)
        return {"take_id": take["id"], "samples": len(take["samples"])}

    def _approve(self) -> dict:
        p = ROOT / "sequence" / "breakdown.json"
        bd = load_json(p)
        result = sequence_tools.validate_breakdown_tool(bd)
        if not result["ok"]:
            return {"approved": False, **result}
        bd["approved"] = True
        p.write_text(json.dumps(bd, indent=2) + "\n", encoding="utf-8")
        return {"approved": True}

    ACTIONS = {
        "render_preview": lambda a: spec_tools.render_preview(a.get("angles"), a.get("quality", "fast"), a.get("frame")),
        "compile_scene": lambda a: spec_tools.compile_scene(),
        "export_proxy": lambda a: director_tools.export_proxy(),
        "checkpoint": lambda a: spec_tools.checkpoint(a["label"]),
        "restore": lambda a: spec_tools.restore(a["label"]),
        "apply_take": lambda a: director_tools.apply_take(a["take_id"], a.get("camera_id", "cam_main")),
        "promote_take": lambda a: director_tools.promote_take(a["take_id"], a["name"], a.get("description", ""),
                                                             a.get("shot_types", []), a.get("register")),
        "validate": lambda a: spec_tools.validate_spec(),
        "preview_model": lambda a: model_tools.preview_model(a["model"], a.get("views"),
                                                             a.get("quality", "lookdev")),
        "validate_model": lambda a: model_tools.validate_model_tool(a["model"]),
        "checkpoint_model": lambda a: model_tools.checkpoint_model(a["model"], a["label"]),
    }

    def _action(self, b: dict) -> dict:
        if b.get("shot"):
            get_state().open(b["shot"])      # disk is the truth; the MCP process may have changed it
        fn = self.ACTIONS.get(b.get("action"))
        if fn is None:
            raise KeyError(f"unknown action {b.get('action')!r}")
        return fn(b.get("args", {}))

    def _chat(self, b: dict) -> None:
        global _current_turn
        with _turn_lock:
            if _current_turn and not _current_turn.done:
                return self._json(409, {"error": "a turn is already running; attach to it instead"})
            state = claude_runner.load_state()
            shot = b.get("shot") or state.get("shot")
            _current_turn = Turn(b["message"], state.get("session_id"), shot, b.get("agent"))
        self._stream(0)

    def _stream(self, start: int) -> None:
        """Replay a running turn's events as SSE. Disconnecting does not stop it."""
        turn = _current_turn
        if turn is None:
            return self._json(204, {})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        i = start
        try:
            while True:
                batch = turn.since(i)
                for event in batch:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    i += 1
                self.wfile.flush()
                if turn.done and i >= len(turn.events):
                    return
                time.sleep(0.15)
        except (BrokenPipeError, ConnectionResetError):
            return          # the browser went away; the turn keeps running


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--shot", default=None)
    a = p.parse_args()
    if a.shot:
        state = claude_runner.load_state()
        state["shot"] = a.shot
        claude_runner.save_state(state)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Blendy Studio: http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
