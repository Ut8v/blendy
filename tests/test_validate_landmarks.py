"""v1.1 validator rules: landmark references, camera moves, sequence block."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.refs import CHARACTER_CORE_LANDMARKS, ProfileIndex, parse_ref  # noqa: E402
from compiler.validate import validate  # noqa: E402

CHARACTER = ROOT / "spec" / "scenes" / "character_example.json"
BLOCKOUT = ROOT / "spec" / "scenes" / "blockout_example.json"

WALKER_HASH = "abc123"


def character_profile(**overrides):
    landmarks = {n: {"kind": "bone", "bone": n, "end": "head"} for n in CHARACTER_CORE_LANDMARKS}
    profile = {"profile_version": 1, "class": "character", "landmarks": landmarks,
               "flags": {"generated": False, "rig_ok": True}}
    profile.update(overrides)
    return profile


def index(profile=None):
    manifest = {"assets": {"local:characters/walker.fbx": {"hash": WALKER_HASH}}}
    profiles = {WALKER_HASH: profile} if profile is not None else {}
    return ProfileIndex(manifest, profiles)


class Base(unittest.TestCase):
    def load(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def assertCode(self, result, code, entity_id=None):
        matches = [i for i in result.issues if i.code == code]
        self.assertTrue(matches, f"expected {code!r}; got:\n{result.format()}")
        if entity_id is not None:
            self.assertIn(entity_id, {i.entity_id for i in matches})
        return matches

    def assertNoCode(self, result, code):
        self.assertNotIn(code, {i.code for i in result.issues}, result.format())


class TestRefParsing(unittest.TestCase):
    def test_parse(self):
        r = parse_ref("@hero.eye_l")
        self.assertEqual((r.asset_id, r.landmark), ("hero", "eye_l"))

    def test_rejects_malformed(self):
        for bad in ("hero.eye", "@hero", "@Hero.eye", "@hero.eye.x", "@hero."):
            with self.assertRaises(ValueError, msg=bad):
                parse_ref(bad)


class TestLandmarkRefs(Base):
    def setUp(self):
        self.spec = self.load(CHARACTER)
        self.cam = self.spec["cameras"][0]

    def test_valid_landmark_on_ingested_character(self):
        self.cam["track_target"] = "@walker_mesh.eye_midpoint"
        result = validate(self.spec, profiles=index(character_profile()))
        self.assertTrue(result.ok, result.format())

    def test_unknown_landmark_lists_the_vocabulary(self):
        self.cam["track_target"] = "@walker_mesh.nose"
        result = validate(self.spec, profiles=index(character_profile()))
        [issue] = self.assertCode(result, "unknown_landmark", "cam_main")
        self.assertIn("eye_midpoint", issue.message)
        self.assertIn("hand_L", issue.message)

    def test_uningested_asset_is_an_error_not_a_guess(self):
        self.cam["track_target"] = "@walker_mesh.eye_midpoint"
        result = validate(self.spec, profiles=index(None))
        self.assertCode(result, "asset_not_ingested", "cam_main")

    def test_landmark_on_unknown_asset(self):
        self.cam["track_target"] = "@ghost.eye_midpoint"
        self.assertCode(validate(self.spec, profiles=index(character_profile())),
                        "unknown_ref", "cam_main")

    def test_primitives_have_a_builtin_vocabulary(self):
        spec = self.load(BLOCKOUT)
        spec["cameras"][0]["track_target"] = "@hero_block.top"
        spec["cameras"][0]["dof"]["focus_target"] = "@hero_block.front"
        result = validate(spec, profiles=ProfileIndex())
        self.assertTrue(result.ok, result.format())

    def test_primitive_unknown_landmark(self):
        spec = self.load(BLOCKOUT)
        spec["cameras"][0]["track_target"] = "@hero_block.seat"
        [issue] = self.assertCode(validate(spec, profiles=ProfileIndex()), "unknown_landmark")
        self.assertIn("top", issue.message)

    def test_location_may_be_a_landmark(self):
        spec = self.load(BLOCKOUT)
        spec["assets"][2]["transform"]["location"] = "@hero_block.top"
        self.assertTrue(validate(spec, profiles=ProfileIndex()).ok)

    def test_anchor_offset_requires_anchor(self):
        spec = self.load(BLOCKOUT)
        spec["assets"][2]["transform"]["anchor_offset"] = [0, 0, 0.1]
        self.assertCode(validate(spec, profiles=ProfileIndex()), "offset_without_anchor")

    def test_anchor_and_landmark_location_conflict(self):
        spec = self.load(BLOCKOUT)
        spec["assets"][2]["transform"]["location"] = "@hero_block.top"
        spec["assets"][2]["transform"]["anchor"] = "@hero_block.front"
        self.assertCode(validate(spec, profiles=ProfileIndex()), "anchor_and_landmark_location")

    def test_incomplete_character_profile_fails_rig(self):
        profile = character_profile()
        del profile["landmarks"]["eye_midpoint"]
        result = validate(self.spec, profiles=index(profile))
        [issue] = self.assertCode(result, "incomplete_character_profile", "walker")
        self.assertIn("eye_midpoint", issue.message)

    def test_generated_mesh_without_cleanup_flag_cannot_be_rigged(self):
        profile = character_profile(flags={"generated": True, "rig_ok": False})
        result = validate(self.spec, profiles=index(profile))
        [issue] = self.assertCode(result, "generated_mesh_rigged", "walker")
        self.assertEqual(issue.level, "error")

    def test_generated_mesh_with_cleanup_flag_only_warns(self):
        profile = character_profile(flags={"generated": True, "rig_ok": True})
        result = validate(self.spec, profiles=index(profile))
        [issue] = self.assertCode(result, "generated_mesh_rigged", "walker")
        self.assertEqual(issue.level, "warning")


class TestCameraMoves(Base):
    def setUp(self):
        self.spec = self.load(BLOCKOUT)
        self.cam = self.spec["cameras"][0]
        self.cam["keyframes"] = []
        self.cam["track_target"] = None
        self.cam["move"] = {"preset": None, "keys": [
            {"frame": 1, "target": "@hero_block.top", "distance": 6.0, "azimuth": 30.0,
             "elevation": 15.0, "focal": 50.0, "interpolation": "BEZIER"},
            {"frame": 48, "target": "@hero_block.top", "distance": 4.0, "azimuth": 60.0,
             "elevation": 10.0, "focal": 50.0, "interpolation": "BEZIER"}]}

    def run_it(self):
        return validate(self.spec, profiles=ProfileIndex())

    def test_valid_move(self):
        self.assertTrue(self.run_it().ok, self.run_it().format())

    def test_move_excludes_track_target(self):
        self.cam["track_target"] = "hero_block"
        self.assertCode(self.run_it(), "move_and_track_target", "cam_main")

    def test_move_excludes_location_keyframes(self):
        self.cam["keyframes"] = [{"channel": "location", "frame": 1, "value": [0, 0, 0],
                                  "interpolation": "LINEAR"}]
        self.assertCode(self.run_it(), "move_and_transform_keyframes", "cam_main")

    def test_focal_keyframes_are_still_fine_with_a_move(self):
        self.cam["keyframes"] = [{"channel": "focal", "frame": 1, "value": 50.0,
                                  "interpolation": "LINEAR"}]
        self.assertNoCode(self.run_it(), "move_and_transform_keyframes")

    def test_unsorted_keys(self):
        self.cam["move"]["keys"][1]["frame"] = 1
        self.assertCode(self.run_it(), "unsorted_move_keys", "cam_main")

    def test_unsnapped_key_is_flagged_not_rejected(self):
        self.cam["move"]["keys"][1]["target"] = [1.0, 2.0, 3.0]
        result = self.run_it()
        self.assertTrue(result.ok)
        self.assertCode(result, "unsnapped_key", "cam_main")

    def test_entity_id_target_is_allowed(self):
        self.cam["move"]["keys"][0]["target"] = "hero_block"
        self.assertTrue(self.run_it().ok)

    def test_unknown_landmark_in_move(self):
        self.cam["move"]["keys"][0]["target"] = "@hero_block.nose"
        self.assertCode(self.run_it(), "unknown_landmark", "cam_main")


class TestSequenceBlock(Base):
    def setUp(self):
        self.spec = self.load(CHARACTER)
        self.spec["sequence"] = {
            "shot_id": "s01_001", "scene_id": "s01", "location": "stage", "look": "stage_day",
            "cast": [{"cast_id": "walker", "rig_id": "walker"}],
            "dialogue": [{"line_id": "s01_l001", "cast_id": "walker", "frame_start": 10}],
            "enters_with": {"positions": {}, "props": {}, "world": {}},
            "exits_with": {"positions": {}, "props": {}, "world": {}}}

    def run_it(self):
        return validate(self.spec, profiles=index(character_profile()))

    def test_valid(self):
        self.assertTrue(self.run_it().ok, self.run_it().format())

    def test_cast_rig_must_exist(self):
        self.spec["sequence"]["cast"][0]["rig_id"] = "ghost"
        self.assertCode(self.run_it(), "unknown_ref")

    def test_dialogue_speaker_must_be_in_cast(self):
        self.spec["sequence"]["dialogue"][0]["cast_id"] = "narrator"
        self.assertCode(self.run_it(), "dialogue_cast_absent")

    def test_shot_id_pattern(self):
        self.spec["sequence"]["shot_id"] = "shot1"
        self.assertCode(self.run_it(), "invalid_value")

    def test_audio_ids_share_the_namespace(self):
        self.spec["audio"] = [{"id": "walker", "path": "audio/dialogue/x.wav", "frame_start": 1}]
        self.assertCode(self.run_it(), "duplicate_id", "walker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
