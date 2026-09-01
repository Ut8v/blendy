"""MCP tool layer, everything that does not need Blender. Runs against a temp
copy of the sequence so the repo is never touched."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import state as state_mod                                     # noqa: E402
from server.tools import (director_tools, learning_tools, sequence_tools,  # noqa: E402
                          spec_tools)
from server import director, presets, incidents, orchestrator, skills       # noqa: E402


class ToolBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "sequence", self.tmp / "sequence")
        shutil.copytree(ROOT / "profiles", self.tmp / "profiles")
        (self.tmp / "scenes").mkdir()
        shutil.copy(ROOT / "spec/scenes/blockout_example.json", self.tmp / "scenes" / "blockout_example.json")
        self.patches = [
            mock.patch.object(state_mod, "STATE", None),
            mock.patch.object(state_mod, "SHOTS_DIR", self.tmp / "sequence" / "shots"),
            mock.patch.object(state_mod, "SCENES_DIR", self.tmp / "scenes"),
            mock.patch.object(state_mod, "BUILD_DIR", self.tmp / "build"),
            mock.patch.object(sequence_tools, "BIBLE", self.tmp / "sequence" / "bible.json"),
            mock.patch.object(sequence_tools, "BREAKDOWN", self.tmp / "sequence" / "breakdown.json"),
            mock.patch.object(sequence_tools, "SHOTS", self.tmp / "sequence" / "shots"),
            mock.patch.object(presets, "PRESET_DIR", self.tmp / "profiles" / "presets"),
            mock.patch.object(director, "TAKES_DIR", self.tmp / "takes"),
            mock.patch.object(director, "PROXY_DIR", self.tmp / "proxies"),
            mock.patch.object(incidents, "INCIDENT_DIR", self.tmp / "incidents"),
            mock.patch.object(orchestrator, "PROPOSAL_DIR", self.tmp / "proposals"),
            mock.patch.object(skills, "SKILL_DIR", ROOT / "skills"),
        ]
        for p in self.patches:
            p.start()
        state_mod.STATE = state_mod.State(str(self.tmp / "t.sqlite"))
        import server.session as sess
        self.cp = mock.patch.object(sess, "CHECKPOINT_DIR", self.tmp / "checkpoints")
        self.cp.start()

    def tearDown(self):
        self.cp.stop()
        state_mod.STATE.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSpecTools(ToolBase):
    def test_open_read_patch_validate(self):
        info = spec_tools.open_spec("blockout_example")
        self.assertTrue(info["valid"])
        self.assertEqual(spec_tools.read_spec()["meta"]["name"], "blockout_example")
        r = spec_tools.patch_spec([{"op": "replace", "path": "/assets/id=hero_block/transform/location",
                                    "value": [1, 1, 1]}])
        self.assertTrue(r["applied"])
        r = spec_tools.patch_spec([{"op": "replace", "path": "/render/camera", "value": "nope"}])
        self.assertFalse(r["applied"])
        self.assertEqual(r["errors"][0]["code"], "unknown_ref")
        self.assertTrue(spec_tools.validate_spec()["ok"])

    def test_open_unknown_lists_available(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            spec_tools.open_spec("nothing")
        self.assertIn("s01_001", str(ctx.exception))

    def test_write_lock_via_tools(self):
        spec_tools.open_spec("blockout_example")
        spec_tools.acquire_write_lock("blocking")
        from server.session import WriteLockHeld
        with self.assertRaises(WriteLockHeld):
            spec_tools.acquire_write_lock("camera")
        spec_tools.release_write_lock("blocking")
        spec_tools.acquire_write_lock("camera")

    def test_checkpoint_restore(self):
        spec_tools.open_spec("blockout_example")
        spec_tools.checkpoint("start")
        spec_tools.patch_spec([{"op": "replace", "path": "/meta/seed", "value": 5}])
        spec_tools.restore("start")
        self.assertEqual(spec_tools.read_spec()["meta"]["seed"], 1)
        self.assertEqual([c["label"] for c in spec_tools.list_checkpoints()], ["start"])

    def test_read_image_path_checks_existence(self):
        with self.assertRaises(FileNotFoundError):
            spec_tools.read_image_path("preview/none.png")


class TestSequenceTools(ToolBase):
    def test_read_script_and_bible(self):
        s = sequence_tools.read_script("kitchen")
        self.assertEqual(s["scenes"][0]["lines"][2]["id"], "s01_l001")
        self.assertEqual(sequence_tools.read_bible()["cast"][0]["id"], "maya")

    def test_validate_breakdown_tool(self):
        self.assertTrue(sequence_tools.validate_breakdown_tool()["ok"])

    def test_write_breakdown_forces_unapproved_and_validates(self):
        bd = sequence_tools.read_breakdown()
        bd["shots"][0]["cast"].append("ghost")
        r = sequence_tools.write_breakdown(bd)
        self.assertFalse(r["written"])
        self.assertIn("unresolved_cast", {e["code"] for e in r["errors"]})
        bd["shots"][0]["cast"].remove("ghost")
        r = sequence_tools.write_breakdown(bd)
        self.assertTrue(r["written"])
        self.assertFalse(sequence_tools.read_breakdown()["approved"])

    def test_ingest_list(self):
        items = sequence_tools.ingest_list()
        refs = {i["ref"] for i in items}
        self.assertEqual(refs, {"characters/maya.fbx", "characters/dan.fbx"})
        self.assertTrue(all(i["class"] == "character" for i in items))

    def test_read_shot_resolves_bible(self):
        r = sequence_tools.read_shot("s01_002")
        self.assertEqual(r["resolved"]["look"]["id"], "kitchen_morning")
        self.assertEqual([c["id"] for c in r["resolved"]["cast"]], ["maya", "dan"])
        self.assertTrue(r["inheritance"]["ok"], r["inheritance"])

    def test_validate_continuity_from_shot_specs(self):
        self.assertTrue(sequence_tools.validate_continuity("s01")["ok"])
        p = self.tmp / "sequence" / "shots" / "s01_003.json"
        spec = json.loads(p.read_text())
        spec["sequence"]["enters_with"]["positions"]["dan"] = "counter"
        p.write_text(json.dumps(spec))
        r = sequence_tools.validate_continuity("s01")
        self.assertFalse(r["ok"])
        self.assertEqual(r["issues"][0]["entity_id"], "s01_003")

    def test_scaffold_new_shot_from_breakdown(self):
        (self.tmp / "sequence" / "shots" / "s01_002.json").unlink()
        r = sequence_tools.new_shot_from_breakdown("s01_002")
        self.assertEqual(r["cast"], ["maya", "dan"])
        spec = spec_tools.read_spec()
        self.assertEqual(spec["sequence"]["shot_id"], "s01_002")
        self.assertEqual(spec["meta"]["frame_end"], 96)
        self.assertEqual(spec["render"]["world_lighting_preset"], "kitchen_morning")
        self.assertEqual(len(spec["lights"]), 2)
        self.assertEqual({rg["id"] for rg in spec["rigs"]}, {"maya", "dan"})
        self.assertTrue(sequence_tools.read_shot("s01_002")["inheritance"]["ok"])

    def test_scaffold_refuses_unapproved(self):
        bd = sequence_tools.read_breakdown()
        bd["approved"] = False
        (self.tmp / "sequence" / "breakdown.json").write_text(json.dumps(bd))
        with self.assertRaises(RuntimeError):
            sequence_tools.new_shot_from_breakdown("s01_001")


class TestDirectorAndPresetTools(ToolBase):
    def test_apply_preset_lighting_and_camera(self):
        spec_tools.open_spec("blockout_example")
        r = director_tools.apply_preset("lighting", "kitchen_morning")
        self.assertFalse(r["applied"])          # preset tracks @table.top, blockout has no 'table'
        self.assertIn("unknown_ref", {e["code"] for e in r["errors"]})
        director_tools.promote_preset("lighting", "blockout_lights", "two areas")
        self.assertTrue(director_tools.apply_preset("lighting", "blockout_lights")["applied"])

    def test_take_round_trip_through_tools(self):
        spec_tools.open_spec("blockout_example")
        take = director.save_take("blockout_example", "keyframe", [
            {"frame": 1, "target": "@hero_block.top", "distance": 5, "azimuth": 30, "elevation": 10, "focal": 50},
            {"frame": 48, "target": "@hero_block.top", "distance": 4, "azimuth": 60, "elevation": 12, "focal": 50}],
            24, [50])
        self.assertEqual(len(director_tools.list_takes()), 1)
        r = director_tools.apply_take(take["id"])
        self.assertTrue(r["applied"], r)
        self.assertEqual(spec_tools.read_spec()["cameras"][0]["move"]["keys"][0]["target"], "@hero_block.top")
        director_tools.promote_take(take["id"], "orbit_in", "d", ["wide"])
        self.assertIn("orbit_in", [p["name"] for p in director_tools.list_presets("camera")])


class TestLearningTools(ToolBase):
    def test_incident_to_pattern(self):
        for i in range(3):
            learning_tools.write_incident(f"s01_00{i + 1}", "camera", "eyes upper third", "eyes centered",
                                          session=f"s{i}")
        self.assertEqual(len(learning_tools.list_incidents(agent="camera")), 3)
        [pat] = learning_tools.incident_patterns()
        self.assertEqual(pat["shots"], 3)
        skill = learning_tools.read_skill("camera")
        self.assertIn("lens_set", skill["core"])

    def test_bad_incident_rejected(self):
        with self.assertRaises(ValueError):
            learning_tools.write_incident("s", "nobody", "e", "o")

    def test_corrections_through_tools(self):
        learning_tools.add_correction("mixamo characters in interior kitchen scenes",
                                      "idle at counter", "arms hang", "one hand on counter")
        hits = learning_tools.retrieve_corrections(["kitchen"], "character idle at the counter")
        self.assertEqual(len(hits), 1)

    def test_render_status_empty(self):
        self.assertEqual(learning_tools.render_status(), "no shots tracked")


if __name__ == "__main__":
    unittest.main()
