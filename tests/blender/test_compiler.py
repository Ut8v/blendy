"""Compiler tests. Run headless against real Blender; never mock bpy.

    tests/blender/run.sh                      # all
    tests/blender/run.sh TestDeterminism      # one class

Each test compiles into a fresh scratch path. Nothing here touches a .blend a
human might have open.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402

from compiler.build import build                     # noqa: E402
from compiler.fingerprint import scene_fingerprint   # noqa: E402

BLOCKOUT = ROOT / "spec" / "scenes" / "blockout_example.json"
SCRATCH = Path(tempfile.mkdtemp(prefix="blendy_test_"))


def spec_with(**changes):
    with open(BLOCKOUT, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    for key, value in changes.items():
        spec[key] = value
    path = SCRATCH / f"spec_{len(os.listdir(SCRATCH))}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path, spec


class TestStaticBuild(unittest.TestCase):
    def test_blockout_builds(self):
        res = build(str(BLOCKOUT), str(SCRATCH / "blockout.blend"), "build")
        self.assertTrue(res.ok, res.error)
        self.assertTrue(os.path.exists(res.blend_path))
        names = {o.name for o in bpy.context.scene.objects}
        for expected in ("ground", "hero_block", "companion_sphere", "cam_main", "key", "fill"):
            self.assertIn(expected, names)

    def test_scene_settings_follow_spec(self):
        res = build(str(BLOCKOUT), None, "build")
        self.assertTrue(res.ok, res.error)
        s = bpy.context.scene
        self.assertEqual((s.render.fps, s.frame_start, s.frame_end), (24, 1, 48))
        self.assertEqual(s.render.engine, "BLENDER_WORKBENCH")
        self.assertEqual((s.render.resolution_x, s.render.resolution_y), (854, 480))
        self.assertEqual(s.camera.name, "cam_main")

    def test_track_target_becomes_constraint(self):
        build(str(BLOCKOUT), None, "build")
        cam = bpy.data.objects["cam_main"]
        cons = [c for c in cam.constraints if c.type == "TRACK_TO"]
        self.assertEqual(len(cons), 1)
        self.assertEqual(cons[0].target.name, "hero_block")

    def test_camera_keyframes_land(self):
        build(str(BLOCKOUT), None, "build")
        cam = bpy.data.objects["cam_main"]
        self.assertIsNotNone(cam.animation_data)
        self.assertIsNotNone(cam.animation_data.action)

    def test_landmark_ref_places_object(self):
        path, spec = spec_with()
        spec["assets"][2]["transform"]["location"] = "@hero_block.top"
        path.write_text(json.dumps(spec), encoding="utf-8")
        res = build(str(path), None, "build")
        self.assertTrue(res.ok, res.error)
        sphere = bpy.data.objects["companion_sphere"]
        # hero_block at z=1 with scale 1; its "top" socket is local (0,0,1) -> world z=2
        self.assertAlmostEqual(sphere.matrix_world.translation.z, 2.0, places=4)

    def test_anchored_child_keeps_its_own_scale(self):
        # hero_block is scaled (1,1,1) in the example; use a non-uniform parent to be sure.
        path, spec = spec_with()
        spec["assets"][1]["transform"]["scale"] = [0.2, 0.5, 1.5]
        spec["assets"][2]["transform"]["anchor"] = "@hero_block.top"
        spec["assets"][2]["transform"]["anchor_offset"] = [0.0, 0.0, 0.25]
        path.write_text(json.dumps(spec), encoding="utf-8")
        res = build(str(path), None, "build")
        self.assertTrue(res.ok, res.error)
        sphere = bpy.data.objects["companion_sphere"]
        ws = sphere.matrix_world.to_scale()
        for got, want in zip(ws, spec["assets"][2]["transform"]["scale"]):
            self.assertAlmostEqual(got, want, places=4)          # not multiplied by the parent
        # top of a cube scaled z=1.5 at z=1 is z=2.5; offset 0.25 m above it, in meters
        self.assertAlmostEqual(sphere.matrix_world.translation.z, 2.75, places=4)

    def test_camera_move_builds_helpers(self):
        path, spec = spec_with()
        cam = spec["cameras"][0]
        cam["keyframes"], cam["track_target"] = [], None
        cam["move"] = {"preset": None, "keys": [
            {"frame": 1, "target": "@hero_block.top", "distance": 6.0, "azimuth": 0.0,
             "elevation": 0.0, "focal": 50.0, "interpolation": "BEZIER"},
            {"frame": 48, "target": "@hero_block.top", "distance": 4.0, "azimuth": 90.0,
             "elevation": 20.0, "focal": 50.0, "interpolation": "BEZIER"}]}
        path.write_text(json.dumps(spec), encoding="utf-8")
        res = build(str(path), None, "build")
        self.assertTrue(res.ok, res.error)
        rig = bpy.data.objects["MOVE_cam_main_rig"]
        bpy.context.scene.frame_set(1)
        loc = rig.matrix_world.translation
        self.assertAlmostEqual(loc.x, 0.0, places=4)
        self.assertAlmostEqual(loc.y, -6.0, places=4)       # azimuth 0 = in front (-Y)
        self.assertAlmostEqual(loc.z, 2.0, places=4)        # level with the target


class TestFailures(unittest.TestCase):
    def test_invalid_spec_fails_before_touching_scene(self):
        build(str(BLOCKOUT), None, "build")
        before = len(bpy.data.objects)
        path, spec = spec_with()
        spec["render"]["camera"] = "ghost"
        path.write_text(json.dumps(spec), encoding="utf-8")
        res = build(str(path), None, "build")
        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "validate")
        self.assertIn("unknown_ref", res.error)
        self.assertEqual(len(bpy.data.objects), before)     # previous scene untouched

    def test_build_error_names_entity_and_stage(self):
        path, spec = spec_with()
        spec["assets"].append({"id": "missing", "source": "local", "ref": "nope/none.fbx",
                               "transform": spec["assets"][0]["transform"]})
        path.write_text(json.dumps(spec), encoding="utf-8")
        res = build(str(path), None, "build")
        self.assertFalse(res.ok)
        self.assertEqual((res.stage, res.entity_id), ("assets", "missing"))


class TestDeterminism(unittest.TestCase):
    def test_build_twice_same_fingerprint(self):
        a = build(str(BLOCKOUT), str(SCRATCH / "a.blend"), "build")
        b = build(str(BLOCKOUT), str(SCRATCH / "b.blend"), "build")
        self.assertTrue(a.ok and b.ok, (a.error, b.error))
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_fingerprint_changes_when_spec_changes(self):
        a = build(str(BLOCKOUT), None, "build")
        path, spec = spec_with()
        spec["assets"][1]["transform"]["location"] = [0.0, 0.0, 1.5]
        path.write_text(json.dumps(spec), encoding="utf-8")
        b = build(str(path), None, "build")
        self.assertNotEqual(a.fingerprint, b.fingerprint)

    def test_rebuild_leaves_no_orphans(self):
        build(str(BLOCKOUT), None, "build")
        n_mesh, n_obj = len(bpy.data.meshes), len(bpy.data.objects)
        build(str(BLOCKOUT), None, "build")
        self.assertEqual((len(bpy.data.meshes), len(bpy.data.objects)), (n_mesh, n_obj))


class TestPreview(unittest.TestCase):
    def test_three_angles_fast(self):
        import time
        t0 = time.time()
        res = build(str(BLOCKOUT), None, "preview", quality="fast",
                    out_dir=str(SCRATCH / "preview"))
        dt = time.time() - t0
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.outputs), 3)
        for p in res.outputs:
            self.assertTrue(os.path.exists(p), p)
            self.assertGreater(os.path.getsize(p), 1000)
        self.assertLess(dt, 30, f"preview round trip took {dt:.1f}s")
        self.assertEqual(bpy.context.scene.camera.name, "cam_main")   # restored

    def test_lookdev_uses_eevee(self):
        res = build(str(BLOCKOUT), None, "preview", quality="lookdev", angles=["camera"],
                    out_dir=str(SCRATCH / "lookdev"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(bpy.context.scene.render.engine, "BLENDER_EEVEE")


class TestFinalRender(unittest.TestCase):
    def test_frames_are_resumable(self):
        out = SCRATCH / "renders"
        res = build(str(BLOCKOUT), None, "final", frames="1-2", out_dir=str(out))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.outputs), 2)
        res2 = build(str(BLOCKOUT), None, "final", frames="1-3", out_dir=str(out))
        self.assertTrue(res2.ok, res2.error)
        self.assertEqual(len(res2.outputs), 1)              # only frame 3 rendered
        self.assertEqual(res2.extra["skipped"], [1, 2])


class TestProxy(unittest.TestCase):
    def test_proxy_exports_landmarks(self):
        out = SCRATCH / "proxy.glb"
        res = build(str(BLOCKOUT), None, "proxy", out_path=str(out))
        self.assertTrue(res.ok, res.error)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 500)


def run():
    loader = unittest.TestLoader()
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if argv:
        suite = unittest.TestSuite([loader.loadTestsFromName(f"__main__.{n}") for n in argv])
    else:
        suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
