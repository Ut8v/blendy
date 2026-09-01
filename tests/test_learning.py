"""Skills partition, incidents, corrections, orchestrator proposal mode."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import corrections, incidents, orchestrator, skills   # noqa: E402
from server.db import Database                                     # noqa: E402

SKILL = """---
name: camera
description: test
---

# Core
<!-- IMMUTABLE -->

- Lens from the lens_set.

<!-- LEARNED -->
<!-- Orchestrator-writable. -->

- [INC-0001, INC-0002, INC-0003] Close-ups want the eyes in the upper third.
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "skills").mkdir()
        (self.tmp / "skills" / "camera.md").write_text(SKILL, encoding="utf-8")
        self.patches = [mock.patch.object(skills, "SKILL_DIR", self.tmp / "skills"),
                        mock.patch.object(incidents, "INCIDENT_DIR", self.tmp / "incidents"),
                        mock.patch.object(orchestrator, "PROPOSAL_DIR", self.tmp / "proposals")]
        for p in self.patches:
            p.start()
        self.db = Database(self.tmp / "t.sqlite")

    def tearDown(self):
        self.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def incident(self, shot, session, agent="camera", category=None):
        return incidents.write_incident(self.db, {"shot": shot, "agent": agent, "category": category,
                                                  "expected": "eyes upper third",
                                                  "observed": "eyes centered"}, session=session)


class TestSkills(Base):
    def test_read_splits_core_and_learned(self):
        s = skills.read("camera")
        self.assertIn("Lens from the lens_set", s["core"])
        self.assertEqual(len(s["learned"]), 1)

    def test_core_is_never_rewritten(self):
        before = skills.read("camera")["core"]
        skills.write_learned("camera", ["- [INC-0009] New pattern."])
        self.assertEqual(skills.read("camera")["core"], before)
        self.assertEqual(skills.read("camera")["learned"], ["- [INC-0009] New pattern."])

    def test_stale_core_refused(self):
        with self.assertRaises(skills.SkillError):
            skills.write_learned("camera", [], expected_core="something else")

    def test_lines_need_incident_refs(self):
        with self.assertRaises(skills.SkillError):
            skills.write_learned("camera", ["- Just a thought."])

    def test_cap_enforced(self):
        lines = [f"- [INC-{i:04d}] pattern {i}" for i in range(41)]
        with self.assertRaises(skills.SkillError) as ctx:
            skills.write_learned("camera", lines)
        self.assertIn("cap", str(ctx.exception))

    def test_missing_marker_refuses_write(self):
        (self.tmp / "skills" / "camera.md").write_text("# no marker", encoding="utf-8")
        with self.assertRaises(skills.SkillError):
            skills.write_learned("camera", [])


class TestIncidents(Base):
    def test_shape_enforced(self):
        with self.assertRaises(ValueError):
            incidents.write_incident(self.db, {"shot": "s", "agent": "camera", "expected": "", "observed": "x"})
        with self.assertRaises(ValueError):
            incidents.write_incident(self.db, {"shot": "s", "agent": "someone", "expected": "e", "observed": "o"})

    def test_write_and_triage(self):
        rec = self.incident("s01_001", "a")
        self.assertEqual(rec["id"], "INC-0001")
        self.assertTrue((self.tmp / "incidents" / "INC-0001.json").exists())
        incidents.triage(self.db, "INC-0001", "schema")
        self.assertEqual(incidents.list_incidents(self.db)[0]["category"], "schema")


class TestCorrections(Base):
    def test_broad_scope_rejected(self):
        with self.assertRaises(ValueError):
            corrections.add(self.db, {"scope": "for animation", "situation": "x", "wrong": "w", "right": "r"})

    def test_supersede_not_accumulate(self):
        a = corrections.add(self.db, {"scope": "mixamo characters in interior kitchen scenes",
                                      "situation": "character idles near the counter",
                                      "wrong": "hands hang", "right": "hands rest on counter"})
        b = corrections.add(self.db, {"scope": "mixamo characters in interior kitchen scenes",
                                      "situation": "character idles near the counter",
                                      "wrong": "hands hang", "right": "one hand on counter"})
        self.assertIn(a["id"], b["supersedes"])
        active = self.db.query("SELECT id FROM corrections WHERE active=1")
        self.assertEqual([r["id"] for r in active], [b["id"]])

    def test_retrieve_filters_by_scope_first(self):
        corrections.add(self.db, {"scope": "mixamo characters in interior kitchen scenes",
                                  "situation": "idle near counter", "wrong": "w", "right": "r"})
        corrections.add(self.db, {"scope": "polyhaven props on exterior street sets",
                                  "situation": "idle near counter", "wrong": "w", "right": "r"})
        hits = corrections.retrieve(self.db, ["kitchen"], "character idle near the counter")
        self.assertEqual(len(hits), 1)
        self.assertIn("kitchen", hits[0]["scope"])
        self.assertEqual(self.db.one("SELECT hits FROM corrections WHERE id=?", (hits[0]["id"],))["hits"], 1)

    def test_budget_prunes(self):
        with mock.patch.object(corrections, "MAX_ENTRIES", 2):
            for i in range(3):
                corrections.add(self.db, {"scope": f"scope kind number {i} interior",
                                          "situation": f"situation {i}", "wrong": "w", "right": "r"})
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM corrections WHERE active=1")["n"], 2)


class TestOrchestrator(Base):
    def seed(self, n=3, sessions=None):
        ids = []
        for i in range(n):
            ids.append(self.incident(f"s01_{i:03d}", (sessions or [f"sess{i}" for i in range(n)])[i])["id"])
        return ids

    def just(self):
        return {"not_schema": "framing is not expressible as a validator rule",
                "not_tool": "no tool returns eye position in frame", "why_judgment": "composition taste"}

    def test_patterns_need_three_shots(self):
        self.seed(2)
        self.assertEqual(orchestrator.patterns(self.db), [])
        self.incident("s01_099", "sess9")
        [pat] = orchestrator.patterns(self.db)
        self.assertEqual((pat["agent"], pat["shots"]), ("camera", 3))

    def test_proposal_requires_justification(self):
        ids = self.seed()
        with self.assertRaises(ValueError):
            orchestrator.propose(self.db, "camera", ids, "pattern", {"not_schema": "x"})

    def test_proposal_rejects_single_session(self):
        ids = self.seed(3, sessions=["same"] * 3)
        with self.assertRaises(ValueError):
            orchestrator.propose(self.db, "camera", ids, "pattern", self.just())

    def test_proposal_rejects_structural_incidents(self):
        ids = self.seed()
        incidents.triage(self.db, ids[0], "schema")
        with self.assertRaises(ValueError):
            orchestrator.propose(self.db, "camera", ids, "pattern", self.just())

    def test_propose_evaluate_apply_flow(self):
        ids = self.seed()
        prop = orchestrator.propose(self.db, "camera", ids, "Medium shots keep the eyes in the upper third.",
                                    self.just())
        self.assertEqual(prop["status"], "proposed")
        self.assertIn("+- [INC-0001, INC-0002, INC-0003] Medium shots", prop["diff"])
        self.assertEqual(skills.read("camera")["learned"], prop["learned_before"])   # nothing applied

        with self.assertRaises(ValueError):
            orchestrator.apply(prop["id"], "me")                                  # not evaluated

        seen = {}
        def runner(diff):
            seen["learned_during"] = skills.read("camera")["learned"]
            return {"id": "run1", "passed": False, "regressions": [{"shot": "e", "metric": "pixel"}]}
        prop = orchestrator.evaluate(self.db, prop["id"], runner)
        self.assertEqual(seen["learned_during"], prop["learned_after"])            # applied during eval
        self.assertEqual(skills.read("camera")["learned"], prop["learned_before"])  # restored after
        self.assertEqual(prop["status"], "eval_failed")
        with self.assertRaises(ValueError):
            orchestrator.apply(prop["id"], "me")

        prop = orchestrator.evaluate(self.db, prop["id"], lambda d: {"id": "run2", "passed": True})
        self.assertEqual(prop["status"], "eval_passed")
        prop = orchestrator.apply(prop["id"], "me")
        self.assertEqual(prop["status"], "applied")
        self.assertEqual(skills.read("camera")["learned"], prop["learned_after"])


if __name__ == "__main__":
    unittest.main()
