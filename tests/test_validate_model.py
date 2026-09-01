import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.refs import CHARACTER_CORE_LANDMARKS   # noqa: E402
from compiler.validate_model import validate_model   # noqa: E402

T = {"location": [0, 0, 0], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}


def recipe():
    return {
        "version": "1.0", "id": "dummy", "kind": "prop", "reference": None, "height": 1.0,
        "parts": [
            {"id": "body", "op": "skin", "parent": None, "material": "steel", "modifiers": [], "transform": dict(T),
             "params": {"joints": {"hip": {"position": [0, 0, 0.9], "radius": 0.15},
                                   "chest": {"position": [0, 0, 1.3], "radius": 0.18},
                                   "head": {"position": [0, 0, 1.6], "radius": 0.1}},
                        "edges": [["hip", "chest"], ["chest", "head"]], "root": "hip", "subdivision": 2}},
            {"id": "hilt", "op": "revolve", "parent": "body", "material": None, "modifiers": [{"type": "bevel", "width": 0.002}],
             "transform": dict(T), "params": {"profile": [[0.01, 0], [0.015, 0.1], [0.03, 0.12]], "segments": 16}},
            {"id": "plate", "op": "extrude", "parent": "body", "material": "steel",
             "modifiers": [{"type": "boolean", "operation": "difference", "part": "hilt"}],
             "transform": dict(T), "params": {"outline": [[0, 0], [0.2, 0], [0.2, 0.1], [0, 0.1]], "depth": 0.01}},
            {"id": "plate_r", "op": "extrude", "parent": "body", "material": "steel", "modifiers": [],
             "transform": dict(T), "params": {}, "mirror_of": "plate"},
        ],
        "materials": {"steel": {"base_color": [0.3, 0.3, 0.32], "metallic": 0.9, "roughness": 0.4,
                                "grunge": {"strength": 0.4}, "scratches": {"strength": 0.2}}},
        "landmarks": {"top": {"part": "body", "anchor": "top"}, "neck_joint": {"part": "body", "anchor": "joint:head"},
                      "tip": {"position": [0, 0, 1.7]}},
        "skeleton": None,
    }


class TestModelValidation(unittest.TestCase):
    def codes(self, m):
        return {i.code for i in validate_model(m).issues}

    def assertCode(self, m, code):
        r = validate_model(m)
        self.assertIn(code, {i.code for i in r.issues}, r.format())
        return r

    def test_fixture_valid(self):
        r = validate_model(recipe())
        self.assertTrue(r.ok, r.format())

    def test_op_params_validated_per_op(self):
        m = recipe()
        m["parts"][0]["params"] = {"shape": "cube", "size": [1, 1, 1]}    # primitive params on a skin op
        r = self.assertCode(m, "invalid_params")
        self.assertEqual(r.errors[0].entity_id, "body")

    def test_unknown_parent_lists_parts(self):
        m = recipe(); m["parts"][1]["parent"] = "ghost"
        r = self.assertCode(m, "unknown_ref")
        self.assertIn("body", r.errors[0].message)

    def test_parent_cycle(self):
        m = recipe(); m["parts"][0]["parent"] = "hilt"
        self.assertCode(m, "parent_cycle")

    def test_unknown_material(self):
        m = recipe(); m["parts"][0]["material"] = "gold"
        self.assertCode(m, "unknown_material")

    def test_boolean_target_must_exist_and_differ(self):
        m = recipe(); m["parts"][2]["modifiers"][0]["part"] = "plate"
        self.assertCode(m, "self_reference")
        m["parts"][2]["modifiers"][0]["part"] = "nope"
        self.assertCode(m, "unknown_ref")

    def test_modifier_required_params(self):
        m = recipe(); m["parts"][1]["modifiers"] = [{"type": "subdivision"}]
        self.assertCode(m, "modifier_param")

    def test_skin_edges_must_reference_joints(self):
        m = recipe(); m["parts"][0]["params"]["edges"].append(["head", "tail"])
        r = self.assertCode(m, "unknown_joint")
        self.assertIn("chest", r.errors[0].message)

    def test_skin_must_be_connected(self):
        m = recipe(); m["parts"][0]["params"]["joints"]["toe"] = {"position": [0, 0, 0], "radius": 0.05}
        r = self.assertCode(m, "skin_disconnected")
        self.assertIn("toe", r.errors[0].message)

    def test_revolve_negative_radius(self):
        m = recipe(); m["parts"][1]["params"]["profile"][0] = [-0.1, 0]
        self.assertCode(m, "bad_profile")

    def test_tube_radius_count(self):
        m = recipe()
        m["parts"].append({"id": "rope", "op": "tube", "parent": None, "material": None, "modifiers": [], "transform": dict(T),
                           "params": {"points": [[0, 0, 0], [0, 0, 1], [0, 1, 1]], "radius": [0.01, 0.02]}})
        self.assertCode(m, "radius_count")

    def test_landmark_joint_must_exist(self):
        m = recipe(); m["landmarks"]["neck_joint"]["anchor"] = "joint:nose"
        r = self.assertCode(m, "unknown_joint")
        self.assertIn("head", r.errors[0].message)

    def test_landmark_part_must_exist(self):
        m = recipe(); m["landmarks"]["top"]["part"] = "nothing"
        self.assertCode(m, "unknown_ref")

    def test_mirror_of_chain_rejected(self):
        m = recipe()
        m["parts"].append({**copy.deepcopy(m["parts"][3]), "id": "plate_rr", "mirror_of": "plate_r"})
        self.assertCode(m, "bad_mirror")

    def test_character_core_landmarks_warn_then_error_when_rigged(self):
        m = recipe(); m["kind"] = "character"
        r = validate_model(m)
        [issue] = [i for i in r.issues if i.code == "incomplete_character_landmarks"]
        self.assertEqual(issue.level, "warning")
        self.assertIn("eye_midpoint", issue.message)
        m["skeleton"] = {"type": "rigify_human"}
        [issue] = [i for i in validate_model(m).issues if i.code == "incomplete_character_landmarks"]
        self.assertEqual(issue.level, "error")
        for n in CHARACTER_CORE_LANDMARKS:
            m["landmarks"][n] = {"part": "body", "anchor": "center"}
        self.assertNotIn("incomplete_character_landmarks", self.codes(m))

    def test_schema_rejects_unknown_op(self):
        m = recipe(); m["parts"][0]["op"] = "sculpt"
        self.assertCode(m, "invalid_value")


if __name__ == "__main__":
    unittest.main()
