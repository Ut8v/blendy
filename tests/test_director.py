import json
import math
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
from server import director, presets     # noqa: E402
from server.patch import apply_patch     # noqa: E402


def smooth_track(n=240, fps=24):
    """A 10 s orbit with one direction change at the midpoint, 60 Hz."""
    out = []
    for i in range(n * 60 // fps):
        t = i / 60
        az = 20 + 40 * (t / 10) if t < 5 else 40 - 40 * ((t - 5) / 10)
        out.append({"t": t, "target": "@hero_block.top", "distance": 5 - 0.1 * t, "azimuth": az,
                    "elevation": 10 + 0.3 * math.sin(t), "roll": 0, "focal": 47})
    return out


class TestDirector(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(director, "TAKES_DIR", self.tmp / "takes"),
                        mock.patch.object(presets, "PRESET_DIR", self.tmp / "presets")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_focal_snaps_to_lens_set(self):
        take = director.save_take("s01_001", "keyframe",
                                  [{"frame": 1, "target": "@a.b", "distance": 2, "azimuth": 0,
                                    "elevation": 0, "focal": 47}], 24, [24, 35, 50, 85])
        self.assertEqual(take["samples"][0]["focal"], 50)

    def test_live_take_is_decimated_to_sparse_keys(self):
        take = director.save_take("s01_001", "live", smooth_track(), 24, [50])
        self.assertEqual(len(take["samples"]), 600)
        move = director.take_to_move(take)
        n = len(move["keys"])
        self.assertLess(n, 40, f"{n} keys is not sparse")
        self.assertGreaterEqual(n, 3)
        frames = [k["frame"] for k in move["keys"]]
        self.assertEqual(frames, sorted(frames))
        self.assertTrue(all(b - a >= 8 for a, b in zip(frames, frames[1:])), frames)
        # the direction change at 5 s (frame ~121) must survive decimation
        self.assertTrue(any(abs(f - 121) <= 6 for f in frames), frames)

    def test_decimation_keeps_endpoints(self):
        take = director.save_take("s01_001", "live", smooth_track(), 24, [50])
        keys = director.take_to_move(take)["keys"]
        self.assertEqual(keys[0]["frame"], 1)
        self.assertEqual(keys[-1]["frame"], take["samples"][-1]["frame"])

    def test_apply_take_produces_valid_spec(self):
        spec = json.loads((ROOT / "spec/scenes/blockout_example.json").read_text())
        take = director.save_take("blockout_example", "live", smooth_track(48), 24, [50])
        ops = director.apply_take_operations(take["id"], "cam_main")
        out = apply_patch(spec, ops)
        result = validate(out, profiles=ProfileIndex())
        self.assertTrue(result.ok, result.format())
        self.assertIsNone(out["cameras"][0]["track_target"])

    def test_unsnapped_take_cannot_be_promoted(self):
        take = director.save_take("s", "keyframe", [
            {"frame": 1, "target": [1, 2, 3], "distance": 2, "azimuth": 0, "elevation": 0, "focal": 50}],
            24, [50])
        with self.assertRaises(ValueError):
            director.promote_take(take["id"], "bad", "d", ["wide"], None)

    def test_promote_generalizes_subject_and_marks_take(self):
        take = director.save_take("s", "keyframe", [
            {"frame": 1, "target": "@maya_mesh.eye_midpoint", "distance": 2, "azimuth": 200,
             "elevation": 4, "focal": 50},
            {"frame": 48, "target": "@maya_mesh.eye_midpoint", "distance": 1.8, "azimuth": 210,
             "elevation": 2, "focal": 50}], 24, [50])
        director.promote_take(take["id"], "creep_in", "slow creep", ["medium"], "tense")
        data = presets.load_preset("camera", "creep_in")
        self.assertEqual(data["move"]["keys"][0]["target"], "@{subject}.eye_midpoint")
        self.assertEqual(director.load_take(take["id"])["promoted_to"], "creep_in")
        self.assertEqual(director.list_takes("s")[0]["promoted_to"], "creep_in")


if __name__ == "__main__":
    unittest.main()
