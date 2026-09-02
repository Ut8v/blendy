"""Modeling layer tests, headless against real Blender.

    tests/blender/run.sh
"""

import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

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


T0 = {"location": [0, 0, 0], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}


def one_part(pid, op, params, **extra):
    part = {"id": pid, "op": op, "parent": None, "material": None, "modifiers": [],
            "transform": dict(T0), "params": params}
    part.update(extra)
    return {"version": "1.0", "id": "t_" + pid, "kind": "prop", "reference": None, "height": None,
            "materials": {}, "landmarks": {}, "skeleton": None, "parts": [part]}


def bounds(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    return (Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
            Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))))


class TestLoft(unittest.TestCase):
    def test_quad_grid_of_the_expected_size(self):
        path = [{"position": [0, 0, z / 4.0], "size": [0.1, 0.05], "roundness": 1.0} for z in range(5)]
        model = one_part("tube", "loft", {"path": path, "segments": 16, "resolution": 1, "caps": True})
        _root, objects = build_recipe(model)
        mesh = objects["tube"].data
        self.assertEqual(len(mesh.vertices), 16 * 5)
        self.assertTrue(all(len(p.vertices) == 4 for p in mesh.polygons if len(p.vertices) != 16))
        lo, hi = bounds(objects["tube"])
        self.assertAlmostEqual(hi.x - lo.x, 0.2, places=3)
        self.assertAlmostEqual(hi.y - lo.y, 0.1, places=3)
        self.assertAlmostEqual(hi.z - lo.z, 1.0, places=3)

    def test_roundness_changes_the_section_not_its_extent(self):
        area = {}
        for name, r in (("round", 1.0), ("boxy", 2.0)):
            path = [{"position": [0, 0, 0], "size": [0.1, 0.1], "roundness": r},
                    {"position": [0, 0, 0.5], "size": [0.1, 0.1], "roundness": r}]
            _root, objects = build_recipe(one_part(name, "loft", {"path": path, "segments": 32, "resolution": 1}))
            obj = objects[name]
            lo, hi = bounds(obj)
            self.assertAlmostEqual(hi.x - lo.x, 0.2, places=3)      # same extent either way
            area[name] = sum(f.area for f in obj.data.polygons)
        self.assertGreater(area["boxy"], area["round"])              # a square holds more than a circle

    def test_path_resolution_adds_rings(self):
        path = [{"position": [0, 0, z / 2.0], "size": [0.1, 0.1]} for z in range(3)]
        counts = []
        for res in (1, 4):
            _root, objects = build_recipe(one_part("p", "loft", {"path": path, "segments": 12, "resolution": res}))
            counts.append(len(objects["p"].data.vertices))
        self.assertGreaterEqual(counts[1], counts[0] * 3)

    def test_publishes_start_and_end(self):
        path = [{"position": [0, 0, 0], "size": [0.1, 0.1]}, {"position": [0, 0, 0.4], "size": [0.05, 0.05]}]
        _root, objects = build_recipe(one_part("p", "loft", {"path": path, "segments": 12}))
        pts = objects["p"]["blendy_points"]
        for got, want in zip(pts["end"], (0.0, 0.0, 0.4)):
            self.assertAlmostEqual(got, want, places=5)


class TestHead(unittest.TestCase):
    def head(self, **over):
        params = {"height": 0.235, "width": 0.155, "depth": 0.205, "segments": 32, "rings": 28}
        params.update(over)
        _root, objects = build_recipe(one_part("head", "head", params))
        return objects["head"]

    def test_dimensions_follow_the_parameters(self):
        lo, hi = bounds(self.head(ears=0.0, nose=None))
        self.assertAlmostEqual(hi.z - lo.z, 0.235, delta=0.012)     # chin to crown
        self.assertAlmostEqual(hi.x - lo.x, 0.155, delta=0.004)
        self.assertGreater(hi.z - lo.z, hi.y - lo.y)                # the bare skull is taller than deep
        lo, hi = bounds(self.head())
        self.assertAlmostEqual(hi.x - lo.x, 0.155, delta=0.035)     # ears add a little

    def test_skull_is_asymmetric_front_to_back(self):
        obj = self.head()
        zs = [v.co.z for v in obj.data.vertices]
        top = [v.co for v in obj.data.vertices if v.co.z > 0.62 * 0.235]
        bottom = [v.co for v in obj.data.vertices if v.co.z < 0.18 * 0.235]
        # the cranium reaches further back than the jaw does
        self.assertGreater(max(v.y for v in top), max(v.y for v in bottom) + 0.02)
        # and the face plane stays forward all the way down
        self.assertLess(min(v.y for v in bottom), min(v.y for v in top) + 0.03)
        self.assertAlmostEqual(min(zs), 0.0, delta=0.005)

    def test_publishes_the_face_vocabulary(self):
        pts = self.head()["blendy_points"]
        for name in ("eye_l", "eye_r", "eye_midpoint", "ear_l", "ear_r", "chin",
                     "head_top", "nose_tip", "mouth", "jaw_l", "jaw_r", "neck", "brow"):
            self.assertIn(name, pts)
        self.assertGreater(pts["eye_l"][0], 0)                       # .L is +X
        self.assertLess(pts["eye_r"][0], 0)
        self.assertLess(pts["nose_tip"][1], pts["eye_midpoint"][1])  # the nose is in front of the eyes
        self.assertGreater(pts["head_top"][2], pts["chin"][2])

    def test_features_actually_move_the_surface(self):
        profile = lambda o: {round(v.co.z, 4): v.co.y for v in o.data.vertices
                             if abs(v.co.x) < 0.004 and v.co.y < 0}
        fy = profile(self.head(brow=0.0, socket=0.0, cheek=0.0, jaw=0.0, chin=0.0, age=0.0))
        cy = profile(self.head(brow=1.2, socket=1.2, cheek=1.0, jaw=1.0, chin=1.0, age=0.8))
        shared = set(fy) & set(cy)
        self.assertTrue(shared)
        self.assertGreater(max(abs(fy[z] - cy[z]) for z in shared), 0.004)   # >4 mm of brow/chin

    def test_nose_and_ears_can_be_switched_off(self):
        bare = self.head(ears=0.0, nose=None)
        n_bare, (lo, hi) = len(bare.data.vertices), bounds(bare)
        self.assertAlmostEqual(hi.x - lo.x, 0.155, delta=0.004)      # no ears sticking out
        self.assertLess(n_bare, len(self.head().data.vertices))


class TestHand(unittest.TestCase):
    def test_builds_fingers_and_a_thumb(self):
        _root, objects = build_recipe(one_part("hand", "hand", {"length": 0.19, "width": 0.09, "side": "l"}))
        obj = objects["hand"]
        lo, hi = bounds(obj)
        self.assertAlmostEqual(hi.z - lo.z, 0.19, delta=0.03)
        self.assertGreater(len(obj.data.polygons), 400)
        pts = obj["blendy_points"]
        self.assertGreater(pts["thumb_tip"][0], 0)                   # left hand: thumb toward +X

    def test_side_mirrors_the_thumb(self):
        made = {}
        for side in ("l", "r"):
            _root, objects = build_recipe(one_part("h", "hand", {"length": 0.19, "width": 0.09, "side": side}))
            made[side] = objects["h"]["blendy_points"]["thumb_tip"][0]
        self.assertAlmostEqual(made["l"], -made["r"], places=5)


class TestSheet(unittest.TestCase):
    def test_flat_sheet_is_a_quad_grid_of_the_right_size(self):
        model = one_part("s", "sheet", {"size": [0.6, 0.9], "resolution": [10, 12]})
        obj = build_recipe(model)[1]["s"]
        self.assertEqual(len(obj.data.vertices), 11 * 13)
        self.assertTrue(all(len(f.vertices) == 4 for f in obj.data.polygons))
        lo, hi = bounds(obj)
        self.assertAlmostEqual(hi.x - lo.x, 0.6, places=4)
        self.assertAlmostEqual(hi.z - lo.z, 0.9, places=4)
        self.assertAlmostEqual(hi.y - lo.y, 0.0, places=5)      # flat until it is draped

    def test_arc_wraps_the_sheet_around_the_origin(self):
        model = one_part("s", "sheet", {"size": [0.6, 0.5], "resolution": [12, 6], "arc": 180.0})
        obj = build_recipe(model)[1]["s"]
        lo, hi = bounds(obj)
        radius = 0.6 / math.pi
        self.assertAlmostEqual(hi.y, radius, delta=0.01)        # middle of the sheet sits behind
        self.assertAlmostEqual(hi.x - lo.x, 2 * radius, delta=0.01)
        self.assertLess(hi.x - lo.x, 0.6)                       # wrapped, so it spans less than flat

    def test_publishes_edge_points(self):
        obj = build_recipe(one_part("s", "sheet", {"size": [0.4, 0.7]}))[1]["s"]
        self.assertAlmostEqual(obj["blendy_points"]["bottom_center"][2], -0.7, places=5)


class TestHair(unittest.TestCase):
    def hair_model(self, **over):
        params = {"emitter": "ball", "count": 40, "length": 0.2, "radius": 0.004,
                  "segments": 5, "sides": 4, "seed": 1}
        params.update(over)
        model = one_part("ball", "primitive", {"shape": "sphere", "size": [0.3, 0.3, 0.3], "segments": 16})
        model["parts"].append({"id": "hair", "op": "hair", "parent": "ball", "material": None,
                               "modifiers": [], "transform": dict(T0), "params": params})
        return model

    def test_strands_grow_and_hang_below_their_roots(self):
        objects = build_recipe(self.hair_model(gravity=1.0))[1]
        hair = objects["hair"]
        self.assertGreater(len(hair.data.polygons), 200)
        lo, hi = bounds(hair)
        self.assertLess(lo.z, -0.3)                              # falls past the emitter
        self.assertGreater(hi.z, 0.1)                            # still rooted on top

    def test_gravity_changes_how_far_it_falls(self):
        drop = {}
        for g in (0.2, 1.4):
            obj = build_recipe(self.hair_model(gravity=g))[1]["hair"]
            drop[g] = bounds(obj)[0].z
        self.assertLess(drop[1.4], drop[0.2])

    def test_same_seed_is_the_same_hair(self):
        a = [tuple(round(c, 6) for c in v.co) for v in build_recipe(self.hair_model(seed=5))[1]["hair"].data.vertices]
        b = [tuple(round(c, 6) for c in v.co) for v in build_recipe(self.hair_model(seed=5))[1]["hair"].data.vertices]
        c = [tuple(round(c, 6) for c in v.co) for v in build_recipe(self.hair_model(seed=6))[1]["hair"].data.vertices]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_normal_bias_keeps_strands_off_the_far_side(self):
        model = self.hair_model(count=60, region={"axis": "z", "min": 0.0, "max": 1.0,
                                                  "normal_bias": {"direction": [0, 0, 1], "min_dot": 0.5}})
        obj = build_recipe(model)[1]["hair"]
        roots_high = [v.co.z for v in obj.data.vertices if v.co.z > 0]
        self.assertTrue(roots_high)
        lo, hi = bounds(obj)
        self.assertGreater(hi.z, 0.1)          # only the upper cap emitted


class TestClothDrape(unittest.TestCase):
    def test_cloth_falls_and_is_baked_into_the_mesh(self):
        model = one_part("post", "primitive", {"shape": "cylinder", "size": [0.3, 0.3, 1.0]})
        model["parts"].append({"id": "cape", "op": "sheet", "parent": None, "material": None,
                               "transform": {"location": [0, 0, 0.5], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]},
                               "modifiers": [{"type": "cloth", "frame": 14, "collide": ["post"],
                                              "pin_region": {"axis": "z", "min": 0.9, "max": 1.0},
                                              "stiffness": 5, "bending": 0.1}],
                               "params": {"size": [0.7, 0.6], "resolution": [14, 14], "arc": 150.0}})
        objects = build_recipe(model)[1]
        cape = objects["cape"]
        self.assertEqual([m.type for m in cape.modifiers], [])       # baked, no live solver left
        self.assertTrue(any(m.type == "COLLISION" for m in objects["post"].modifiers))
        ys = [v.co.y for v in cape.data.vertices]
        self.assertGreater(max(ys) - min(ys), 0.05)                  # it has folded, not stayed rigid


class TestPushModifier(unittest.TestCase):
    def test_push_dents_the_surface(self):
        params = {"shape": "sphere", "size": [0.4, 0.4, 0.4], "segments": 24}
        plain = build_recipe(one_part("s", "primitive", params))[1]["s"]
        before = max(v.co.y for v in plain.data.vertices)
        model = one_part("s", "primitive", params)
        model["parts"][0]["modifiers"] = [{"type": "push", "center": [0, 0.2, 0], "radius": 0.25,
                                           "direction": [0, -1, 0], "strength": 0.08}]
        dented = build_recipe(model)[1]["s"]
        self.assertLess(max(v.co.y for v in dented.data.vertices), before - 0.05)

    def test_radial_push_swells(self):
        params = {"shape": "cylinder", "size": [0.2, 0.2, 0.6], "segments": 24}
        model = one_part("c", "primitive", params)
        model["parts"][0]["modifiers"] = [{"type": "push", "center": [0, 0, 0], "radius": 0.3,
                                           "strength": 0.05, "axis": "z"}]
        obj = build_recipe(model)[1]["c"]
        lo, hi = bounds(obj)
        self.assertGreater(hi.x - lo.x, 0.2)


class TestAtPlacement(unittest.TestCase):
    def test_part_lands_on_a_published_point(self):
        model = one_part("head", "head", {"height": 0.235, "width": 0.155, "depth": 0.205,
                                          "segments": 24, "rings": 20})
        model["parts"].append({"id": "eye_l", "op": "primitive", "parent": "head", "material": None,
                               "modifiers": [], "transform": dict(T0),
                               "at": {"part": "head", "point": "eye_l", "offset": [0, 0, 0.01]},
                               "params": {"shape": "sphere", "size": [0.024, 0.024, 0.024]}})
        _root, objects = build_recipe(model)
        want = Vector(objects["head"]["blendy_points"]["eye_l"]) + Vector((0, 0, 0.01))
        got = objects["eye_l"].matrix_world.translation
        for a, b in zip(got, want):
            self.assertAlmostEqual(a, b, places=5)


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
