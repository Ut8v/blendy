"""Modeling layer tests, headless against real Blender.

    tests/blender/run.sh
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

from compiler import scene as sc                                   # noqa: E402
from compiler.modeling.build_model import instantiate, resolve_landmarks, write_profile   # noqa: E402
from compiler.build import build                                    # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_validate_model import recipe                              # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="blendy_model_"))


def build_recipe(model):
    scn = sc.reset()
    coll = bpy.data.collections.new("Model")
    scn.collection.children.link(coll)
    return instantiate(model, model["id"], coll.objects.link)


class TestBuilders(unittest.TestCase):
    def test_fixture_builds_every_part(self):
        root, objects = build_recipe(recipe())
        self.assertEqual(set(objects), {"body", "hilt", "plate", "plate_r"})
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in objects.values():
            self.assertEqual(obj.type, "MESH")
            # skin parts carry only joints and edges until the modifier runs: check evaluated
            self.assertGreater(len(obj.evaluated_get(depsgraph).data.polygons), 0, obj.name)

    def test_skin_wraps_joints_and_subdivides(self):
        root, objects = build_recipe(recipe())
        body = objects["body"]
        self.assertEqual([m.type for m in body.modifiers], ["SKIN", "SUBSURF"])
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = body.evaluated_get(depsgraph)
        self.assertGreater(len(ev.data.polygons), 200)

    def test_mirror_part_is_flipped_in_x(self):
        root, objects = build_recipe(recipe())
        a, b = objects["plate"], objects["plate_r"]
        xa = [v.co.x for v in a.data.vertices]
        xb = [v.co.x for v in b.data.vertices]
        self.assertAlmostEqual(max(xa), -min(xb), places=5)

    def test_modifier_stack_applied_in_order(self):
        root, objects = build_recipe(recipe())
        self.assertEqual([m.type for m in objects["plate"].modifiers], ["BOOLEAN"])
        self.assertEqual(objects["plate"].modifiers[0].object, objects["hilt"])
        self.assertEqual([m.type for m in objects["hilt"].modifiers], ["BEVEL"])

    def test_materials_are_procedural_node_trees(self):
        root, objects = build_recipe(recipe())
        mat = objects["body"].data.materials[0]
        kinds = {n.bl_idname for n in mat.node_tree.nodes}
        self.assertIn("ShaderNodeBsdfPrincipled", kinds)
        self.assertIn("ShaderNodeTexNoise", kinds)       # grunge
        self.assertIn("ShaderNodeTexWave", kinds)        # scratches

    def test_landmarks_resolve_to_root_local_metres(self):
        model = recipe()
        root, objects = build_recipe(model)
        lm = resolve_landmarks(model, root, objects)
        self.assertAlmostEqual(lm["neck_joint"]["position"][2], 1.6, places=4)   # joint:head
        self.assertEqual(lm["tip"]["position"], [0, 0, 1.7])
        self.assertGreater(lm["top"]["position"][2], 1.6)                        # above the head joint radius

    def test_profile_measures_height(self):
        model = recipe()
        root, objects = build_recipe(model)
        prof = write_profile(model, root, objects, SCRATCH / "dummy.json")
        self.assertGreater(prof["height"], 1.6)
        self.assertLess(prof["height"], 1.9)
        self.assertEqual(prof["class"], "prop")

    def test_every_op_builds(self):
        T = {"location": [0, 0, 0], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}
        model = {"version": "1.0", "id": "ops", "kind": "prop", "reference": None, "height": None,
                 "materials": {}, "landmarks": {}, "skeleton": None, "parts": [
                     {"id": "a", "op": "primitive", "parent": None, "material": None, "modifiers": [], "transform": dict(T),
                      "params": {"shape": "torus", "size": [0.4, 0.4, 0.06]}},
                     {"id": "b", "op": "tube", "parent": None, "material": None, "modifiers": [], "transform": dict(T),
                      "params": {"points": [[0, 0, 0], [0, 0, 0.5], [0.2, 0, 0.8]], "radius": 0.02}},
                     {"id": "c", "op": "metaball", "parent": None, "material": None, "modifiers": [], "transform": dict(T),
                      "params": {"blobs": [{"position": [0, 0, 0], "radius": 0.2}, {"position": [0.15, 0, 0], "radius": 0.15}]}},
                     {"id": "d", "op": "extrude", "parent": None, "material": None,
                      "modifiers": [{"type": "cloth", "pin": "top", "frame": 3}], "transform": dict(T),
                      "params": {"outline": [[0, 0], [0.3, 0], [0.3, 0.02], [0, 0.02]], "depth": 0.5}}]}
        root, objects = build_recipe(model)
        for pid in "abcd":
            self.assertEqual(objects[pid].type, "MESH", pid)
            self.assertGreater(len(objects[pid].data.polygons), 0, pid)


class TestModelInShot(unittest.TestCase):
    def test_shot_references_model_by_profile(self):
        model = recipe()
        root, objects = build_recipe(model)
        prof_path = ROOT / "profiles" / "models" / "dummy.json"
        write_profile(model, root, objects, prof_path)
        (ROOT / "models" / "dummy.json").write_text(json.dumps(model), encoding="utf-8")
        try:
            spec = json.loads((ROOT / "spec/scenes/blockout_example.json").read_text())
            spec["assets"].append({"id": "dummy", "source": "model", "ref": "dummy",
                                   "transform": {"location": [2, 2, 0], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}})
            spec["cameras"][0]["track_target"] = "@dummy.top"
            spec["cameras"][0]["dof"]["focus_target"] = "@dummy.neck_joint"
            path = SCRATCH / "shot.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            res = build(str(path), None, "build")
            self.assertTrue(res.ok, res.error)
            self.assertIn("dummy.body", bpy.data.objects)
            lm = bpy.data.objects["LM_dummy_neck_joint"]
            self.assertAlmostEqual(lm.matrix_world.translation.x, 2.0, places=4)
            self.assertAlmostEqual(lm.matrix_world.translation.z, 1.6, places=4)
        finally:
            (ROOT / "models" / "dummy.json").unlink(missing_ok=True)
            prof_path.unlink(missing_ok=True)


def run():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
