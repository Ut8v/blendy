"""Proportion measurement and reference comparison. Plain Python, no Blender."""

import json
import unittest
from pathlib import Path

from compiler.proportions import (NOTABLE, ProportionError, compare, measure,
                                  model_points, png_size, summarize,
                                  to_isotropic, validate_points)

ROOT = Path(__file__).resolve().parent.parent

# A deliberately plain figure: 8 heads tall, eyes halfway down the skull,
# shoulders two head-heights across. Every expected value below is arithmetic
# on these numbers, not a value copied out of a previous run.
IDEAL = {
    "head_top": (0.5, 0.00),
    "chin": (0.5, 0.10),
    "eye": (0.5, 0.05),
    "shoulder_l": (0.6, 0.16),
    "shoulder_r": (0.4, 0.16),
    "ground": (0.5, 0.80),
    "hip": (0.5, 0.42),
    "ear_l": (0.54, 0.05),
    "ear_r": (0.46, 0.05),
}


class TestMeasure(unittest.TestCase):
    def test_known_figure_measures_as_drawn(self):
        m = measure(IDEAL)
        self.assertAlmostEqual(m["heads_tall"], 8.0, places=6)
        self.assertAlmostEqual(m["eye_line"], 0.5, places=6)
        self.assertAlmostEqual(m["shoulder_span_heads"], 2.0, places=6)
        self.assertAlmostEqual(m["hip_height_frac"], 0.475, places=6)

    def test_scale_invariant(self):
        """The whole point: a reference at any size compares to a model at any
        height. Doubling every coordinate must change nothing."""
        doubled = {k: (x * 2, y * 2) for k, (x, y) in IDEAL.items()}
        self.assertEqual(measure(IDEAL), measure(doubled))

    def test_translation_invariant(self):
        moved = {k: (x + 0.1, y + 0.05) for k, (x, y) in IDEAL.items()}
        for key, value in measure(IDEAL).items():
            self.assertAlmostEqual(measure(moved)[key], value, places=9)

    def test_optional_measures_are_omitted_not_invented(self):
        """A reference that does not show the hands must yield no arm row,
        rather than a number the agent would then act on."""
        without = {k: v for k, v in IDEAL.items() if k not in ("ear_l", "ear_r")}
        m = measure(without)
        self.assertNotIn("head_aspect", m)
        self.assertNotIn("arm_drop_frac", m)
        self.assertIn("heads_tall", m)

    def test_missing_required_point_raises(self):
        without = {k: v for k, v in IDEAL.items() if k != "chin"}
        with self.assertRaises(ProportionError):
            measure(without)

    def test_inverted_figure_raises_rather_than_returning_nonsense(self):
        """Chin above head_top means the points were marked upside down. A
        negative head height would silently produce plausible-looking ratios."""
        upside_down = dict(IDEAL, chin=(0.5, -0.10))
        with self.assertRaises(ProportionError):
            measure(upside_down)


class TestCompare(unittest.TestCase):
    def test_identical_inputs_are_all_ok(self):
        for row in compare(IDEAL, dict(IDEAL)):
            self.assertEqual(row["verdict"], "ok", row)

    def test_worst_disagreement_sorts_first(self):
        # Head 25% larger: heads_tall and shoulder_span_heads both shift.
        squat = dict(IDEAL, chin=(0.5, 0.125), eye=(0.5, 0.0625),
                     ear_l=(0.54, 0.0625), ear_r=(0.46, 0.0625))
        rows = compare(IDEAL, squat)
        self.assertEqual(rows[0]["verdict"], "strong")
        self.assertGreaterEqual(rows[0]["relative"], rows[-1]["relative"])

    def test_small_drift_stays_inside_tolerance(self):
        """Marking points by eye on stylized art is noisy. A 2% difference must
        not send the agent off to chase it."""
        nudged = dict(IDEAL, ground=(0.5, 0.8 * 1.02))
        for row in compare(IDEAL, nudged):
            self.assertLess(row["relative"], NOTABLE, row)

    def test_only_shared_measures_compare(self):
        no_ears = {k: v for k, v in IDEAL.items() if k not in ("ear_l", "ear_r")}
        names = {row["measure"] for row in compare(no_ears, IDEAL)}
        self.assertNotIn("head_aspect", names)
        self.assertIn("heads_tall", names)

    def test_summary_names_the_worst_measure(self):
        tall = dict(IDEAL, ground=(0.5, 1.2))
        text = summarize(compare(IDEAL, tall))
        self.assertIn("heads_tall", text)
        self.assertIn("high", text)


class TestModelPoints(unittest.TestCase):
    def test_z_up_landmarks_become_y_down_image_points(self):
        """Blender is Z-up; marked points are Y-down. Getting this backwards
        inverts every ratio while still returning numbers."""
        profile = {"landmarks": {
            "head_top": {"position": [0.0, 0.0, 1.8]},
            "chin": {"position": [0.0, 0.0, 1.6]},
            "ground_contact": {"position": [0.0, 0.0, 0.0]},
        }}
        pts = model_points(profile)
        self.assertLess(pts["head_top"][1], pts["chin"][1])
        self.assertLess(pts["chin"][1], pts["ground"][1])

    def test_haldin_profile_yields_a_full_measurement(self):
        """The real profile must produce every measure, or the comparison tool
        is useless on the one character that exists."""
        path = ROOT / "profiles" / "models" / "haldin.json"
        m = measure(model_points(json.loads(path.read_text())))
        for key in ("heads_tall", "eye_line", "shoulder_span_heads",
                    "head_aspect", "hip_height_frac"):
            self.assertIn(key, m)
        # Sanity, not a quality judgement: a human figure is 5 to 10 heads.
        self.assertTrue(5.0 < m["heads_tall"] < 10.0, m["heads_tall"])


class TestValidatePoints(unittest.TestCase):
    def test_accepts_a_good_set(self):
        self.assertEqual(validate_points(IDEAL), [])

    def test_rejects_coordinates_outside_the_image(self):
        problems = validate_points(dict(IDEAL, chin=(0.5, 1.4)))
        self.assertTrue(any("outside the image" in p for p in problems), problems)

    def test_rejects_unknown_point_names(self):
        problems = validate_points(dict(IDEAL, elbow=(0.5, 0.3)))
        self.assertTrue(any("unknown point" in p for p in problems), problems)

    def test_reports_the_missing_required_point(self):
        problems = validate_points({"head_top": (0.5, 0.0)})
        self.assertTrue(any("chin" in p for p in problems), problems)

    def test_head_and_chin_alone_are_enough(self):
        """A tightly cropped portrait must still be markable."""
        self.assertEqual(
            validate_points({"head_top": (0.5, 0.1), "chin": (0.5, 0.3)}), [])


if __name__ == "__main__":
    unittest.main()


class TestAspectCorrection(unittest.TestCase):
    """Normalized coordinates divide x by width and y by height. On a portrait
    crop that is a 25% distortion on any width-against-height ratio, which is
    larger than the threshold at which the agent is told to go fix something."""

    def test_isotropic_conversion_scales_x_only(self):
        pts = to_isotropic({"a": (1.0, 1.0)}, 800, 1000)
        self.assertAlmostEqual(pts["a"][0], 0.8)
        self.assertAlmostEqual(pts["a"][1], 1.0)

    def test_uncorrected_points_measure_wrong_by_the_aspect(self):
        raw = {"head_top": (0.5, 0.10), "chin": (0.5, 0.20),
               "shoulder_l": (0.65, 0.30), "shoulder_r": (0.35, 0.30)}
        wrong = measure(raw)["shoulder_span_heads"]
        right = measure(to_isotropic(raw, 800, 1000))["shoulder_span_heads"]
        self.assertAlmostEqual(right / wrong, 0.8, places=6)

    def test_square_image_is_a_no_op(self):
        raw = {"head_top": (0.5, 0.1), "chin": (0.5, 0.2)}
        self.assertEqual(to_isotropic(raw, 512, 512), {k: tuple(v) for k, v in raw.items()})

    def test_zero_size_raises(self):
        with self.assertRaises(ProportionError):
            to_isotropic({"a": (0.5, 0.5)}, 0, 100)


class TestPartialReference(unittest.TestCase):
    """A reference cropped above the feet is the normal case, not an error."""

    CROPPED = {"head_top": (0.5, 0.05), "chin": (0.5, 0.18), "eye": (0.5, 0.12),
               "shoulder_l": (0.68, 0.34), "shoulder_r": (0.32, 0.34),
               "ear_l": (0.55, 0.12), "ear_r": (0.45, 0.12)}

    def test_yields_head_and_shoulder_measures_without_ground(self):
        m = measure(self.CROPPED)
        for key in ("eye_line", "head_aspect", "shoulder_span_heads",
                    "shoulder_span_head_widths", "shoulder_drop_heads"):
            self.assertIn(key, m)

    def test_omits_every_height_measure(self):
        m = measure(self.CROPPED)
        for key in ("heads_tall", "hip_height_frac", "knee_height_frac"):
            self.assertNotIn(key, m)

    def test_comparison_reports_what_it_could_not_measure(self):
        profile = json.loads((ROOT / "profiles" / "models" / "haldin.json").read_text())
        built = model_points(profile)
        rows = compare(self.CROPPED, built)
        names = {r["measure"] for r in rows}
        self.assertNotIn("heads_tall", names)
        self.assertIn("shoulder_span_heads", names)
        skipped = sorted(set(measure(built)) - names)
        self.assertIn("heads_tall", skipped)
        self.assertIn("heads_tall", summarize(rows, skipped))


class TestPngSize(unittest.TestCase):
    def test_reads_the_header(self):
        ref = ROOT / "assets" / "references" / "haldin.png"
        if not ref.exists():
            self.skipTest("reference image is local-only and gitignored")
        self.assertEqual(png_size(str(ref)), (1122, 1402))

    def test_refuses_a_non_png_rather_than_guessing(self):
        with self.assertRaises(ProportionError):
            png_size(str(ROOT / "README.md"))
