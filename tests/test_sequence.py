import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.refs import CHARACTER_CORE_LANDMARKS, ProfileIndex               # noqa: E402
from compiler.validate import validate                                          # noqa: E402
from compiler.validate_sequence import (continuity_issues, validate_bible,     # noqa: E402
                                        validate_breakdown, validate_shot_inheritance)
from server.script import parse_file                                            # noqa: E402


def load(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def fake_profiles():
    lm = {n: {"kind": "bone", "bone": n, "end": "head"} for n in CHARACTER_CORE_LANDMARKS}
    lm["chest"] = {"kind": "bone", "bone": "chest", "end": "head"}
    prof = {"profile_version": 1, "class": "character", "landmarks": lm,
            "flags": {"generated": False, "rig_ok": True}, "height": 1.68}
    manifest = {"assets": {"local:characters/maya.fbx": {"hash": "m"}, "local:characters/dan.fbx": {"hash": "d"}}}
    return ProfileIndex(manifest, {"m": prof, "d": {**prof, "height": 1.82}})


class TestBible(unittest.TestCase):
    def test_shipped_bible_valid(self):
        self.assertTrue(validate_bible(load("sequence/bible.json")).ok)

    def test_look_must_point_at_a_location(self):
        b = load("sequence/bible.json")
        b["looks"][0]["location_id"] = "moon"
        self.assertIn("unknown_ref", {i.code for i in validate_bible(b).issues})


class TestBreakdown(unittest.TestCase):
    def setUp(self):
        self.b = load("sequence/bible.json")
        self.bd = load("sequence/breakdown.json")
        self.lines = parse_file(ROOT / "script" / "kitchen.fountain").line_ids()

    def codes(self):
        return {i.code for i in validate_breakdown(self.bd, self.b, self.lines).issues}

    def test_shipped_breakdown_valid(self):
        self.assertEqual(self.codes(), set())

    def test_invented_character_fails_loudly(self):
        self.bd["shots"][1]["cast"].append("narrator")
        self.assertIn("unresolved_cast", self.codes())

    def test_unknown_script_ref(self):
        self.bd["shots"][0]["script_ref"] = ["s09_b001"]
        self.assertIn("unknown_script_ref", self.codes())

    def test_look_must_match_location(self):
        self.b["locations"].append({"id": "street", "assets": [], "layout_preset": None, "ground_plane": 0})
        self.bd["shots"][0]["location"] = "street"
        self.assertIn("look_location_mismatch", self.codes())

    def test_boundary_mismatch(self):
        self.bd["shots"][2]["enters_with"]["world"]["door_open"] = False
        [issue] = [i for i in validate_breakdown(self.bd, self.b, self.lines).issues
                   if i.code == "boundary_mismatch"]
        self.assertEqual(issue.entity_id, "s01_003")
        self.assertIn("door_open", issue.message)

    def test_cut_resets_the_chain(self):
        self.bd["shots"][2]["enters_with"]["world"]["door_open"] = False
        self.bd["shots"][2]["cut_before"] = True
        self.assertNotIn("boundary_mismatch", self.codes())

    def test_scene_change_resets_the_chain(self):
        shots = copy.deepcopy(self.bd["shots"])
        shots[1]["scene_id"] = "s02"
        shots[1]["id"] = "s02_001"
        shots[1]["enters_with"]["positions"] = {"maya": "elsewhere"}
        self.assertEqual(continuity_issues(shots[:2]), [])


class TestInheritance(unittest.TestCase):
    def setUp(self):
        self.b = load("sequence/bible.json")
        self.bd = load("sequence/breakdown.json")
        self.shot = load("sequence/shots/s01_002.json")
        self.bshot = self.bd["shots"][1]

    def codes(self, heights=None):
        return {i.code for i in validate_shot_inheritance(self.shot, self.b, self.bshot, heights).issues}

    def test_shipped_shot_inherits_cleanly(self):
        self.assertEqual(self.codes(), set())

    def test_shot_validates_with_profiles(self):
        r = validate(self.shot, profiles=fake_profiles())
        self.assertTrue(r.ok, r.format())

    def test_cannot_redefine_fps(self):
        self.shot["meta"]["fps"] = 30
        self.assertIn("redefines_style", self.codes())

    def test_focal_must_be_in_lens_set(self):
        self.shot["cameras"][0]["move"]["keys"][0]["focal"] = 42
        self.assertIn("focal_not_in_lens_set", self.codes())

    def test_recast_rejected(self):
        self.shot["assets"][2]["ref"] = "characters/other.fbx"
        self.assertIn("recast", self.codes())

    def test_scale_drift_rejected(self):
        self.assertIn("scale_drift", self.codes({"maya": 1.90}))
        self.assertNotIn("scale_drift", self.codes({"maya": 1.69}))

    def test_duration_follows_breakdown(self):
        self.shot["meta"]["frame_end"] = 50
        self.assertIn("duration_mismatch", self.codes())

    def test_continuity_state_cannot_be_redefined_in_shot(self):
        self.shot["sequence"]["exits_with"]["world"]["door_open"] = False
        self.assertIn("continuity_redefined", self.codes())

    def test_engine_override_only_via_breakdown(self):
        self.shot["render"]["engine"] = "CYCLES"
        self.assertIn("redefines_style", self.codes())
        self.bshot["render_engine"] = "CYCLES"
        self.assertNotIn("redefines_style", self.codes())

    def test_lighting_preset_must_be_the_looks(self):
        self.shot["render"]["world_lighting_preset"] = "noir"
        self.assertIn("redefines_look", self.codes())


if __name__ == "__main__":
    unittest.main()
