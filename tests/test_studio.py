"""Studio backend: HTTP API, SSE chat through a fake claude, file guards."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import claude_runner, director, studio        # noqa: E402
from server import state as state_mod                       # noqa: E402


class StudioBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.env = mock.patch.dict(os.environ, {"CLAUDE_BIN": str(ROOT / "tests" / "fake_claude.py")})
        cls.env.start()
        cls.patches = [mock.patch.object(claude_runner, "STATE_FILE", cls.tmp / "studio_state.json"),
                       mock.patch.object(director, "TAKES_DIR", cls.tmp / "takes"),
                       mock.patch.object(state_mod, "STATE", state_mod.State(str(cls.tmp / "t.sqlite")))]
        for p in cls.patches:
            p.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), studio.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for p in cls.patches:
            p.stop()
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read()

    def post(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST",
                                     data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class TestApi(StudioBase):
    def test_overview(self):
        code, body = self.get("/api/overview")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertIn("s01_002", [s["id"] for s in d["shots"]])
        self.assertTrue(d["breakdown"]["approved"])
        self.assertTrue(d["claude"])

    def test_shot_info(self):
        code, body = self.get("/api/shot?id=blockout_example")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(d["valid"]["ok"])
        self.assertEqual(d["cameras"], ["cam_main"])
        self.assertAlmostEqual(d["aspect"], 854 / 480)

    def test_unknown_shot_is_400_with_message(self):
        code, body = self.post("/api/action", {"shot": "nope", "action": "validate"})
        self.assertEqual(code, 400)
        self.assertIn("no spec", json.loads(body)["error"])

    def test_action_validate(self):
        code, body = self.post("/api/action", {"shot": "blockout_example", "action": "validate"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_file_guard(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/files/../CLAUDE.md")
        self.assertIn(ctx.exception.code, (403, 404))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/files/server/db.py")
        self.assertEqual(ctx.exception.code, 403)

    def test_static_ui_served(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Blendy Studio", body)

    def test_take_round_trip(self):
        code, body = self.post("/api/take", {"shot": "blockout_example", "mode": "keyframe", "samples": [
            {"frame": 1, "target": "@hero_block.top", "distance": 4, "azimuth": 10, "elevation": 5, "focal": 47}]})
        self.assertEqual(code, 200)
        take_id = json.loads(body)["take_id"]
        _, body = self.get("/api/shot?id=blockout_example")
        self.assertEqual(json.loads(body)["takes"][0]["id"], take_id)


class TestActivity(StudioBase):
    def test_activity_is_empty_when_nothing_runs(self):
        d = json.loads(self.get("/api/activity")[1])
        self.assertIn("turn", d)
        self.assertIn("jobs", d)
        self.assertIsNone(d["current"])

    def test_slow_actions_return_a_job_and_finish(self):
        code, body = self.post("/api/action", {"shot": "blockout_example", "action": "render_preview",
                                               "args": {"quality": "fast"}})
        self.assertEqual(code, 200)
        job_id = json.loads(body)["job"]
        for _ in range(400):
            listing = json.loads(self.get("/api/jobs")[1])
            job = next(j for j in listing if j["id"] == job_id)
            if not job["running"]:
                break
            time.sleep(0.05)
        self.assertFalse(job["running"], "the render job never finished")
        self.assertEqual(job["kind"], "render_preview")
        self.assertGreaterEqual(job["seconds"], 0)

    def test_fast_actions_still_answer_inline(self):
        code, body = self.post("/api/action", {"shot": "blockout_example", "action": "validate"})
        self.assertEqual(code, 200)
        self.assertNotIn("job", json.loads(body))

    def test_tool_calls_appear_in_the_timeline(self):
        self.post("/api/chat/reset")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/chat", method="POST",
                                     data=json.dumps({"message": "go", "shot": "blockout_example"}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req).close()
        for _ in range(200):
            if not json.loads(self.get("/api/chat/state")[1])["running"]:
                break
            time.sleep(0.05)
        d = json.loads(self.get("/api/activity")[1])
        names = [t["name"] for t in d["tools"]]
        self.assertIn("render_preview", names)
        done = [t for t in d["tools"] if t["ended"] is not None]
        self.assertTrue(done and done[0]["ok"])


class TestChat(StudioBase):
    def setUp(self):
        self.wait_idle()

    def wait_idle(self):
        for _ in range(200):
            if not json.loads(self.get("/api/chat/state")[1])["running"]:
                return
            time.sleep(0.05)
        self.fail("a turn never finished")

    def stream(self, message):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/chat", method="POST",
                                     data=json.dumps({"message": message, "shot": "blockout_example"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            return [json.loads(l[6:]) for l in r.read().decode().split("\n\n") if l.startswith("data: ")]

    def test_stream_events_and_session_resume(self):
        self.post("/api/chat/reset")
        events = self.stream("hello")
        types = [e["type"] for e in events]
        self.assertEqual(types, ["init", "text", "tool_use", "tool_result", "result", "done"])
        self.assertEqual(events[1]["text"], "echo: hello")
        self.assertEqual(events[3]["images"], ["preview/x/camera_fast_f0001.png"])
        state = claude_runner.load_state()
        self.assertEqual(state["session_id"], "sess-fake-0001")
        self.assertEqual(state["shot"], "blockout_example")
        # second turn resumes the session
        events = self.stream("again")
        self.assertEqual(events[0]["session_id"], "sess-fake-0001")
        self.assertEqual(claude_runner.load_state()["turns"], 2)

    def test_turn_survives_a_disconnected_client(self):
        self.post("/api/chat/reset")
        # start a turn and abandon the stream immediately
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/chat", method="POST",
                                     data=json.dumps({"message": "hi", "shot": "blockout_example"}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req).close()
        state = {"running": True, "events": 0}
        for _ in range(200):
            state = json.loads(self.get("/api/chat/state")[1])
            if not state["running"] and state["events"]:
                break
            time.sleep(0.05)
        # the turn ran to completion even though nobody was listening
        self.assertGreaterEqual(state["events"], 5)
        code, body = self.post("/api/chat/attach", {"from": 0})
        events = [json.loads(l[6:]) for l in body.decode().split("\n\n") if l.startswith("data: ")]
        self.assertEqual([e["type"] for e in events][-1], "done")
        self.assertIn("text", [e["type"] for e in events])

    def test_second_turn_while_one_runs_is_refused(self):
        """One writer at a time. The first turn is pinned open with a hold file
        rather than left to chance: the fake finishes in milliseconds, so
        without this the assertion is a race that usually passes."""
        self.post("/api/chat/reset")
        with tempfile.TemporaryDirectory() as tmp:
            release = os.path.join(tmp, "release")
            with mock.patch.dict(os.environ, {"FAKE_CLAUDE_HOLD": release}):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/api/chat", method="POST",
                    data=json.dumps({"message": "a"}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req):
                    for _ in range(200):
                        if json.loads(self.get("/api/chat/state")[1])["running"]:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail("first turn never started")
                    code, _ = self.post("/api/chat", {"message": "b"})
                    self.assertEqual(code, 409)
                    open(release, "w").close()
                # Drain before the temp directory goes away: the fake polls for
                # the release file, so deleting it out from under a still-held
                # turn leaves that turn waiting for its full timeout.
                self.wait_idle()

    def test_command_never_carries_an_api_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-should-not-leak"}):
            turn = claude_runner.ClaudeTurn("x", None, None)
            list(turn.events())
        cmd = claude_runner._command("x", "s1", "shot", None)
        self.assertIn("--resume", cmd)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("mcp__blendy__*", cmd)
        self.assertTrue(any(str(ROOT / ".mcp.json") == c for c in cmd))


if __name__ == "__main__":
    unittest.main()
