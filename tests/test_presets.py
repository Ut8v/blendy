import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.refs import ProfileIndex   # noqa: E402
from compiler.validate import validate   # noqa: E402
from server import presets               # noqa: E402
from server.patch import apply_patch     # noqa: E402


class TestPresets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = mock.patch.object(presets, "PRESET_DIR", self.tmp)
        self.p.start()
        self.spec = json.loads((ROOT / "spec/scenes/blockout_example.json").read_text())
        cam = self.spec["cameras"][0]
        cam["keyframes"], cam["track_target"] = [], None
        cam["move"] = {"preset": None, "keys": [
            {"frame": 1, "target": "@hero_block.top", "distance": 5, "azimuth": 20, "elevation": 10,
             "focal": 50.0, "interpolation": "BEZIER"}]}

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_promote_and_apply_lighting_round_trip(self):
        presets.promote_lighting(self.spec, "two_area", "key + fill")
        self.assertEqual([p["name"] for p in presets.list_presets("lighting")], ["two_area"])
        ops = presets.apply_operations("lighting", "two_area", spec=self.spec)
        out = apply_patch(self.spec, ops)
        self.assertEqual(out["lights"], self.spec["lights"])
        self.assertEqual(out["render"]["world_lighting_preset"], "two_area")
        self.assertTrue(validate(out, profiles=ProfileIndex()).ok)

    def test_camera_preset_generalizes_the_subject(self):
        presets.promote_camera(self.spec, "cam_main", "push_in", "slow push", ["medium"], "tense")
        data = presets.load_preset("camera", "push_in")
        self.assertEqual(data["move"]["keys"][0]["target"], "@{subject}.top")
        ops = presets.apply_operations("camera", "push_in", {"subject": "companion_sphere"})
        out = apply_patch(self.spec, ops)
        self.assertEqual(out["cameras"][0]["move"]["keys"][0]["target"], "@companion_sphere.top")
        self.assertEqual(out["cameras"][0]["move"]["preset"], "push_in")
        self.assertTrue(validate(out, profiles=ProfileIndex()).ok)

    def test_world_space_camera_is_not_promotable(self):
        self.spec["cameras"][0]["move"] = None
        with self.assertRaises(ValueError):
            presets.promote_camera(self.spec, "cam_main", "x", "y")

    def test_camera_apply_needs_subject(self):
        presets.promote_camera(self.spec, "cam_main", "push_in", "d")
        with self.assertRaises(ValueError):
            presets.apply_operations("camera", "push_in", {})

    def test_bad_name_rejected(self):
        with self.assertRaises(ValueError):
            presets.save_preset("lighting", "Bad Name", {})

    def test_unknown_preset_lists_available(self):
        presets.promote_lighting(self.spec, "a", "d")
        with self.assertRaises(FileNotFoundError) as ctx:
            presets.load_preset("lighting", "b")
        self.assertIn("a", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
