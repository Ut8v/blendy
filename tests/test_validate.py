"""Validator tests. Plain Python, no Blender.

Each test takes a known-good spec, breaks exactly one thing, and asserts the
validator reports that specific code against the right entity id.

    ./.venv/bin/python -m unittest discover -s tests -v
"""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.validate import (  # noqa: E402
    SpecValidationError,
    require_valid,
    validate,
    validate_file,
)

BLOCKOUT = ROOT / "spec" / "scenes" / "blockout_example.json"
CHARACTER = ROOT / "spec" / "scenes" / "character_example.json"


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class SpecTestCase(unittest.TestCase):
    def setUp(self):
        self.spec = load(BLOCKOUT)

    def codes(self, spec=None, level=None):
        result = validate(spec if spec is not None else self.spec)
        return {i.code for i in result.issues if level is None or i.level == level}

    def assertCode(self, code, spec=None, entity_id=None):
        result = validate(spec if spec is not None else self.spec)
        matches = [i for i in result.issues if i.code == code]
        self.assertTrue(matches, f"expected code {code!r}, got {result.format()}")
        if entity_id is not None:
            self.assertIn(entity_id, {i.entity_id for i in matches},
                          f"{code} was not attributed to {entity_id!r}: {result.format()}")
        return matches


class TestShippedSpecs(SpecTestCase):
    def test_blockout_is_valid(self):
        self.assertTrue(validate_file(BLOCKOUT).ok)

    def test_character_is_clean(self):
        result = validate_file(CHARACTER)
        self.assertTrue(result.ok, result.format())
        self.assertEqual(result.warnings, [], result.format())

    def test_blockout_warns_only_about_primitives(self):
        result = validate_file(BLOCKOUT)
        self.assertEqual({i.code for i in result.warnings}, {"primitive_asset"})

    def test_strict_promotes_warnings(self):
        self.assertFalse(validate(self.spec, strict=True).ok)


class TestSchemaLayer(SpecTestCase):
    def test_missing_section(self):
        del self.spec["lights"]
        self.assertCode("missing_section")

    def test_unknown_top_level_key(self):
        self.spec["physics"] = {}
        self.assertCode("unknown_key")

    def test_unknown_key_inside_entity(self):
        self.spec["assets"][0]["color"] = [1, 0, 0]
        self.assertCode("invalid_value", entity_id="ground")

    def test_bad_version(self):
        self.spec["version"] = "2.0"
        self.assertCode("bad_version")

    def test_bad_engine_identifier(self):
        # 4.2-era identifier, removed in Blender 5.x.
        self.spec["render"]["engine"] = "BLENDER_EEVEE_NEXT"
        self.assertCode("invalid_value")

    def test_transform_requires_all_three_components(self):
        del self.spec["assets"][0]["transform"]["scale"]
        self.assertCode("invalid_value", entity_id="ground")

    def test_vec3_arity_enforced(self):
        self.spec["assets"][0]["transform"]["location"] = [0.0, 0.0]
        self.assertCode("invalid_value", entity_id="ground")

    def test_id_pattern_rejects_camel_case(self):
        self.spec["assets"][0]["id"] = "HeroBlock"
        self.assertCode("invalid_value")

    def test_every_bad_entity_is_reported_not_just_the_first(self):
        self.spec["assets"][0]["focal"] = 50
        self.spec["assets"][1]["focal"] = 50
        self.spec["lights"][0]["energy"] = "bright"
        result = validate(self.spec)
        self.assertEqual(len(result.errors), 3, result.format())

    def test_malformed_json_is_an_issue_not_a_crash(self):
        bad = ROOT / "tests" / "_tmp_malformed.json"
        bad.write_text('{"version": "1.0",,}', encoding="utf-8")
        try:
            result = validate_file(bad)
            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0].code, "malformed_json")
        finally:
            bad.unlink()


class TestReferentialIntegrity(SpecTestCase):
    def test_duplicate_id_across_sections(self):
        self.spec["lights"][0]["id"] = "hero_block"
        self.assertCode("duplicate_id", entity_id="hero_block")

    def test_render_camera_must_exist(self):
        self.spec["render"]["camera"] = "cam_nonexistent"
        self.assertCode("unknown_ref")

    def test_render_camera_must_be_a_camera(self):
        self.spec["render"]["camera"] = "hero_block"
        self.assertCode("wrong_ref_kind")

    def test_unknown_track_target(self):
        self.spec["cameras"][0]["track_target"] = "ghost"
        self.assertCode("unknown_ref", entity_id="cam_main")

    def test_camera_cannot_track_itself(self):
        self.spec["cameras"][0]["track_target"] = "cam_main"
        self.assertCode("self_reference", entity_id="cam_main")

    def test_unknown_dof_focus_target(self):
        self.spec["cameras"][0]["dof"]["focus_target"] = "ghost"
        self.assertCode("unknown_ref", entity_id="cam_main")

    def test_rig_asset_must_exist(self):
        spec = load(CHARACTER)
        spec["rigs"][0]["asset_id"] = "ghost"
        self.assertCode("unknown_ref", spec=spec, entity_id="walker")

    def test_rig_asset_must_be_an_asset(self):
        spec = load(CHARACTER)
        spec["rigs"][0]["asset_id"] = "cam_main"
        self.assertCode("wrong_ref_kind", spec=spec, entity_id="walker")

    def test_animation_rig_must_exist(self):
        spec = load(CHARACTER)
        spec["animation"][0]["rig_id"] = "ghost"
        self.assertCode("unknown_ref", spec=spec, entity_id="walk_in")


class TestArchitecturalRules(SpecTestCase):
    def test_primitive_cannot_be_rigged(self):
        spec = load(CHARACTER)
        spec["assets"][1]["source"] = "primitive"
        spec["assets"][1]["ref"] = "cube"
        self.assertCode("primitive_rigged", spec=spec, entity_id="walker")

    def test_cycles_in_a_preview_warns(self):
        self.spec["render"]["engine"] = "CYCLES"
        self.assertCode("cycles_engine")


class TestKeyframes(SpecTestCase):
    def kf(self, **kw):
        base = {"channel": "location", "frame": 10,
                "value": [0.0, 0.0, 0.0], "interpolation": "LINEAR"}
        base.update(kw)
        self.spec["cameras"][0]["keyframes"].append(base)
        return self.spec

    def test_scalar_channel_rejects_vector_value(self):
        self.kf(channel="focal", value=[50.0, 0.0, 0.0])
        self.assertCode("keyframe_arity", entity_id="cam_main")

    def test_vector_channel_rejects_scalar_value(self):
        self.kf(channel="location", value=3.0)
        self.assertCode("keyframe_arity", entity_id="cam_main")

    def test_light_channel_on_a_camera_is_rejected(self):
        self.kf(channel="energy", value=100.0)
        self.assertCode("bad_channel", entity_id="cam_main")

    def test_camera_channel_on_a_light_is_rejected(self):
        self.spec["lights"][0]["keyframes"] = [
            {"channel": "focal", "frame": 1, "value": 50.0, "interpolation": "LINEAR"}]
        self.assertCode("bad_channel", entity_id="key")

    def test_duplicate_keyframe_on_a_channel(self):
        self.kf(channel="location", frame=1, value=[0.0, 0.0, 0.0])
        self.assertCode("duplicate_keyframe", entity_id="cam_main")

    def test_keyframe_outside_shot_range_warns(self):
        self.kf(frame=500)
        self.assertCode("keyframe_outside_range", entity_id="cam_main")

    def test_dof_keyframe_without_dof_block(self):
        self.spec["cameras"][0]["dof"] = None
        self.kf(channel="dof.f_stop", value=2.8)
        self.assertCode("keyframe_without_dof", entity_id="cam_main")


class TestSceneSanity(SpecTestCase):
    def test_reversed_frame_range(self):
        self.spec["meta"]["frame_end"] = 0
        self.assertCode("bad_frame_range")

    def test_zero_scale_warns(self):
        self.spec["assets"][1]["transform"]["scale"] = [1.0, 0.0, 1.0]
        self.assertCode("zero_scale", entity_id="hero_block")

    def test_degrees_mistaken_for_radians_warns(self):
        self.spec["assets"][1]["transform"]["rotation_euler"] = [0.0, 0.0, 90.0]
        self.assertCode("suspicious_rotation", entity_id="hero_block")

    def test_area_light_needs_a_size(self):
        del self.spec["lights"][0]["size"]
        self.assertCode("missing_size", entity_id="key")

    def test_spot_fields_on_a_non_spot_light(self):
        self.spec["lights"][0]["spot_blend"] = 0.5
        self.assertCode("irrelevant_field", entity_id="key")

    def test_unlit_eevee_scene_warns(self):
        self.spec["render"]["engine"] = "BLENDER_EEVEE"
        self.spec["lights"] = []
        self.spec["world"]["background_color"] = [0.0, 0.0, 0.0]
        self.assertCode("unlit_scene")

    def test_lit_by_world_alone_does_not_warn(self):
        self.spec["render"]["engine"] = "BLENDER_EEVEE"
        self.spec["render"]["samples"] = 16
        self.spec["lights"] = []
        self.assertNotIn("unlit_scene", self.codes())

    def test_coincident_strips_on_one_rig_warn(self):
        spec = load(CHARACTER)
        spec["animation"][1]["frame_start"] = spec["animation"][0]["frame_start"]
        self.assertCode("coincident_strips", spec=spec, entity_id="turn_and_wave")

    def test_ortho_camera_with_dof_warns(self):
        self.spec["cameras"][0]["type"] = "ORTHO"
        self.assertCode("ortho_dof", entity_id="cam_main")


class TestCompilerGate(SpecTestCase):
    def test_require_valid_raises_before_touching_a_scene(self):
        self.spec["render"]["camera"] = "ghost"
        with self.assertRaises(SpecValidationError) as ctx:
            require_valid(self.spec)
        self.assertIn("unknown_ref", str(ctx.exception))

    def test_require_valid_passes_a_good_spec(self):
        self.assertTrue(require_valid(self.spec).ok)

    def test_validation_does_not_mutate_the_spec(self):
        before = copy.deepcopy(self.spec)
        validate(self.spec)
        self.assertEqual(before, self.spec)

    def test_semantic_checks_are_skipped_when_shape_is_broken(self):
        del self.spec["cameras"]
        codes = self.codes()
        self.assertIn("missing_section", codes)
        self.assertNotIn("unknown_ref", codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
