"""Coordinate and lens conversion. Pure Python, no bpy.

Blender is Z-up, right-handed. glTF and three.js are Y-up, right-handed. This is
the ONE module that converts between them, in both directions, for positions,
euler rotations and camera parameters. Nothing else in the repo may do this
arithmetic. Tested against a deliberately asymmetric scene (tests/test_coords.py),
because a symmetric one passes while the conversion is wrong.

Convention:  blender (x, y, z)  ->  gltf (x, z, -y)
             gltf    (x, y, z)  ->  blender (x, -z, y)
This is a +90 degree rotation about X, which is what Blender's own glTF exporter
does with its default "+Y up" setting.
"""

from __future__ import annotations

import math
from typing import Sequence

Vec3 = tuple[float, float, float]

DEFAULT_SENSOR_WIDTH_MM = 36.0


# --- positions and directions ---------------------------------------------

def blender_to_gltf(v: Sequence[float]) -> Vec3:
    x, y, z = v
    return (float(x), float(z), float(-y))


def gltf_to_blender(v: Sequence[float]) -> Vec3:
    x, y, z = v
    return (float(x), float(-z), float(y))


# --- quaternions (w, x, y, z) -------------------------------------------------

# Rotation taking Blender frame to glTF frame: -90 degrees about X.
_S = math.sqrt(0.5)
_Q_B2G = (_S, -_S, 0.0, 0.0)   # w, x, y, z
_Q_G2B = (_S, _S, 0.0, 0.0)


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def quat_conj(q):
    w, x, y, z = q
    return (w, -x, -y, -z)


def quat_rotate(q, v):
    """Rotate vector v by unit quaternion q."""
    p = (0.0, *v)
    w, x, y, z = quat_mul(quat_mul(q, p), quat_conj(q))
    return (x, y, z)


def euler_xyz_to_quat(e: Sequence[float]):
    """Blender XYZ euler (radians) -> quaternion (w, x, y, z)."""
    cx, sx = math.cos(e[0] / 2), math.sin(e[0] / 2)
    cy, sy = math.cos(e[1] / 2), math.sin(e[1] / 2)
    cz, sz = math.cos(e[2] / 2), math.sin(e[2] / 2)
    qx = (cx, sx, 0.0, 0.0)
    qy = (cy, 0.0, sy, 0.0)
    qz = (cz, 0.0, 0.0, sz)
    return quat_mul(quat_mul(qz, qy), qx)   # apply X, then Y, then Z


def quat_to_euler_xyz(q) -> Vec3:
    """Quaternion -> Blender XYZ euler (radians). Same decomposition as Blender's
    mat3_to_eul for rotation order XYZ (R = Rz * Ry * Rx)."""
    w, x, y, z = q
    m00 = 1 - 2 * (y * y + z * z)
    m10 = 2 * (x * y + w * z)
    m20 = 2 * (x * z - w * y)
    m21 = 2 * (y * z + w * x)
    m22 = 1 - 2 * (x * x + y * y)
    m11 = 1 - 2 * (x * x + z * z)
    m12 = 2 * (y * z - w * x)
    cy = math.hypot(m00, m10)
    if cy > 1e-9:
        return (math.atan2(m21, m22), math.atan2(-m20, cy), math.atan2(m10, m00))
    return (math.atan2(-m12, m11), math.atan2(-m20, cy), 0.0)   # gimbal lock


def quat_blender_to_gltf(q):
    """Rotation quaternion of an object, re-expressed in the glTF frame."""
    return quat_mul(quat_mul(_Q_B2G, q), _Q_G2B)


def quat_gltf_to_blender(q):
    return quat_mul(quat_mul(_Q_G2B, q), _Q_B2G)


def quat_wxyz_to_xyzw(q):
    w, x, y, z = q
    return (x, y, z, w)


def quat_xyzw_to_wxyz(q):
    x, y, z, w = q
    return (w, x, y, z)


# --- lens --------------------------------------------------------------------

def focal_to_hfov_deg(focal_mm: float, sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> float:
    return math.degrees(2 * math.atan(sensor_width_mm / (2 * focal_mm)))


def hfov_deg_to_focal(hfov_deg: float, sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> float:
    return sensor_width_mm / (2 * math.tan(math.radians(hfov_deg) / 2))


def hfov_to_vfov_deg(hfov_deg: float, aspect: float) -> float:
    """three.js PerspectiveCamera takes a VERTICAL fov; Blender's sensor fit is
    horizontal by default. aspect = width / height."""
    return math.degrees(2 * math.atan(math.tan(math.radians(hfov_deg) / 2) / aspect))


def vfov_to_hfov_deg(vfov_deg: float, aspect: float) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(vfov_deg) / 2) * aspect))


def focal_to_three_vfov(focal_mm: float, aspect: float,
                        sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> float:
    return hfov_to_vfov_deg(focal_to_hfov_deg(focal_mm, sensor_width_mm), aspect)


def three_vfov_to_focal(vfov_deg: float, aspect: float,
                        sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> float:
    return hfov_deg_to_focal(vfov_to_hfov_deg(vfov_deg, aspect), sensor_width_mm)


def snap_focal(focal_mm: float, lens_set: Sequence[float]) -> float:
    """Nearest allowed focal length. Continuous zoom is not allowed (director mode)."""
    if not lens_set:
        return focal_mm
    return float(min(lens_set, key=lambda f: abs(f - focal_mm)))


# --- spherical camera placement (landmark-anchored moves) --------------------

def spherical_to_offset(distance: float, azimuth_deg: float, elevation_deg: float) -> Vec3:
    """Blender-frame offset of a camera from its target.

    Blender's front view looks along +Y, so an object's "front" is its -Y side and a
    Mixamo character imported with default settings faces -Y. azimuth 0 therefore
    puts the camera at target + (0, -d, 0), looking at the face; 90 puts it on +X;
    elevation is degrees above the target's horizontal plane.
    """
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    r = distance * math.cos(el)
    return (r * math.sin(az), -r * math.cos(az), distance * math.sin(el))


def offset_to_spherical(offset: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = offset
    distance = math.sqrt(x * x + y * y + z * z)
    if distance == 0:
        return (0.0, 0.0, 0.0)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, z / distance))))
    azimuth = math.degrees(math.atan2(x, -y))
    return (distance, azimuth, elevation)


def look_at_euler(eye: Sequence[float], target: Sequence[float], roll_deg: float = 0.0) -> Vec3:
    """Blender XYZ euler that points a camera (looking down its local -Z, +Y up)
    from eye at target, then rolls it about its own view axis. Matches a TRACK_TO
    constraint with TRACK_NEGATIVE_Z / UP_Y, so a baked move and a constrained
    camera agree."""
    dx, dy, dz = (target[i] - eye[i] for i in range(3))
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist == 0:
        return (0.0, 0.0, 0.0)
    horiz = math.hypot(dx, dy)
    tilt = math.atan2(horiz, -dz)                    # 0 = straight down, pi/2 = level
    yaw = math.atan2(-dx, dy) if horiz > 1e-12 else 0.0
    qz = (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))
    qx = (math.cos(tilt / 2), math.sin(tilt / 2), 0.0, 0.0)
    roll = math.radians(roll_deg)
    qr = (math.cos(roll / 2), 0.0, 0.0, math.sin(roll / 2))   # about local view axis
    return quat_to_euler_xyz(quat_mul(quat_mul(qz, qx), qr))
