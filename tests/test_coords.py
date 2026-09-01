"""Coordinate conversion, on a deliberately asymmetric scene.

A symmetric scene passes while the conversion is wrong, so every point here has
three distinct, non-zero, differently-signed components.
"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler import coords as c  # noqa: E402

ASYM = [(1.0, 2.5, -0.75), (-3.2, 0.4, 6.1), (0.3, -7.0, 2.2)]


def close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b))


class TestAxes(unittest.TestCase):
    def test_round_trip(self):
        for p in ASYM:
            self.assertTrue(close(c.gltf_to_blender(c.blender_to_gltf(p)), p))
            self.assertTrue(close(c.blender_to_gltf(c.gltf_to_blender(p)), p))

    def test_up_axis_maps(self):
        self.assertEqual(c.blender_to_gltf((0, 0, 1)), (0.0, 1.0, 0.0))   # Blender up -> glTF up
        self.assertEqual(c.blender_to_gltf((0, 1, 0)), (0.0, 0.0, -1.0))  # Blender +Y (back) -> glTF -Z
        self.assertEqual(c.gltf_to_blender((0, 0, -1)), (0.0, 1.0, 0.0))

    def test_handedness_is_preserved(self):
        # cross(x, y) = z in both frames after conversion
        def cross(a, b):
            return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
        x, y = c.blender_to_gltf((1, 0, 0)), c.blender_to_gltf((0, 1, 0))
        self.assertTrue(close(cross(x, y), c.blender_to_gltf((0, 0, 1))))

    def test_quaternion_frame_change_matches_vector_change(self):
        # Rotating a vector then converting == converting then rotating with the converted quat
        q = c.euler_xyz_to_quat((0.3, -1.1, 2.0))
        for p in ASYM:
            lhs = c.blender_to_gltf(c.quat_rotate(q, p))
            rhs = c.quat_rotate(c.quat_blender_to_gltf(q), c.blender_to_gltf(p))
            self.assertTrue(close(lhs, rhs, 1e-9), (lhs, rhs))


class TestEuler(unittest.TestCase):
    def test_euler_round_trip(self):
        for e in [(0.3, -1.1, 2.0), (-2.0, 0.2, 0.9), (1.2, 1.3, -0.4)]:
            back = c.quat_to_euler_xyz(c.euler_xyz_to_quat(e))
            # compare as rotations, not angles, since eulers are not unique
            for p in ASYM:
                self.assertTrue(close(c.quat_rotate(c.euler_xyz_to_quat(back), p),
                                      c.quat_rotate(c.euler_xyz_to_quat(e), p), 1e-9))

    def test_order_is_x_then_y_then_z(self):
        # 90 deg about X takes +Y to +Z; then 90 about Z takes +Z to +Z (unchanged).
        q = c.euler_xyz_to_quat((math.pi / 2, 0, math.pi / 2))
        self.assertTrue(close(c.quat_rotate(q, (0, 1, 0)), (0, 0, 1), 1e-9))
        # but +X goes: X -> X (after Rx) -> +Y (after Rz)
        self.assertTrue(close(c.quat_rotate(q, (1, 0, 0)), (0, 1, 0), 1e-9))

    def test_gimbal_lock_does_not_blow_up(self):
        e = c.quat_to_euler_xyz(c.euler_xyz_to_quat((0.4, math.pi / 2, 0.0)))
        self.assertTrue(all(math.isfinite(v) for v in e))


class TestLens(unittest.TestCase):
    def test_focal_hfov_round_trip(self):
        for f in (18.0, 35.0, 50.0, 85.0, 135.0):
            self.assertAlmostEqual(c.hfov_deg_to_focal(c.focal_to_hfov_deg(f)), f, places=9)

    def test_known_values(self):
        self.assertAlmostEqual(c.focal_to_hfov_deg(50.0), 39.5977, places=3)
        self.assertAlmostEqual(c.focal_to_hfov_deg(18.0), 90.0, places=0)

    def test_three_vfov_round_trip_at_16_by_9(self):
        aspect = 16 / 9
        for f in (24.0, 50.0, 85.0):
            v = c.focal_to_three_vfov(f, aspect)
            self.assertLess(v, c.focal_to_hfov_deg(f))       # vertical is narrower than horizontal
            self.assertAlmostEqual(c.three_vfov_to_focal(v, aspect), f, places=9)

    def test_snap_focal(self):
        lens = [24, 35, 50, 85]
        self.assertEqual(c.snap_focal(41.0, lens), 35)
        self.assertEqual(c.snap_focal(43.0, lens), 50)
        self.assertEqual(c.snap_focal(200.0, lens), 85)


class TestSpherical(unittest.TestCase):
    def test_azimuth_zero_is_in_front(self):
        self.assertTrue(close(c.spherical_to_offset(5.0, 0.0, 0.0), (0, -5, 0), 1e-9))

    def test_azimuth_90_is_camera_right(self):
        self.assertTrue(close(c.spherical_to_offset(5.0, 90.0, 0.0), (5, 0, 0), 1e-9))

    def test_elevation(self):
        self.assertTrue(close(c.spherical_to_offset(2.0, 0.0, 90.0), (0, 0, 2), 1e-9))

    def test_round_trip(self):
        for d, az, el in [(3.3, 37.0, 12.5), (0.8, -120.0, -40.0), (10.0, 179.0, 60.0)]:
            got = c.offset_to_spherical(c.spherical_to_offset(d, az, el))
            self.assertAlmostEqual(got[0], d, places=9)
            self.assertAlmostEqual(got[1], az, places=9)
            self.assertAlmostEqual(got[2], el, places=9)


class TestLookAt(unittest.TestCase):
    def forward(self, euler):
        return c.quat_rotate(c.euler_xyz_to_quat(euler), (0, 0, -1))

    def test_level_look_along_plus_y(self):
        e = c.look_at_euler((0, -5, 1), (0, 0, 1))
        self.assertTrue(close(self.forward(e), (0, 1, 0), 1e-9))
        self.assertAlmostEqual(e[0], math.pi / 2)

    def test_asymmetric_target_forward_vector(self):
        eye, tgt = (1.0, -4.0, 2.5), (-2.0, 3.0, 0.5)
        d = [tgt[i] - eye[i] for i in range(3)]
        n = math.sqrt(sum(v * v for v in d))
        want = tuple(v / n for v in d)
        self.assertTrue(close(self.forward(c.look_at_euler(eye, tgt)), want, 1e-9))

    def test_up_stays_up_without_roll(self):
        e = c.look_at_euler((1.0, -4.0, 2.5), (-2.0, 3.0, 0.5))
        up = c.quat_rotate(c.euler_xyz_to_quat(e), (0, 1, 0))
        self.assertGreater(up[2], 0.9)      # camera +Y points mostly toward world +Z

    def test_roll_tilts_up_vector_but_keeps_forward(self):
        eye, tgt = (1.0, -4.0, 2.5), (-2.0, 3.0, 0.5)
        e0, e1 = c.look_at_euler(eye, tgt), c.look_at_euler(eye, tgt, roll_deg=30)
        self.assertTrue(close(self.forward(e0), self.forward(e1), 1e-9))
        up0 = c.quat_rotate(c.euler_xyz_to_quat(e0), (0, 1, 0))
        up1 = c.quat_rotate(c.euler_xyz_to_quat(e1), (0, 1, 0))
        self.assertAlmostEqual(math.degrees(math.acos(sum(a * b for a, b in zip(up0, up1)))), 30, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
