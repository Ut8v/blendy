"""Proportion measurement, for comparing a built model against its reference.

The agent can see both images, but two pictures at different framings cannot be
measured against each other by eye, and "looks about right" does not converge.
This turns the comparison into scale-invariant ratios that get better or worse.

Both sides reduce to the same thing: a handful of named 2D points. The reference
side is marked on the image by the agent, in normalized coordinates. The model
side is its own landmarks projected to the front view. One measurement function
then runs over either, so the two are guaranteed to be computed the same way.

Image convention throughout: x increases right, y increases DOWN, because that
is how the marked points arrive. Model landmarks are Z-up world space and get
flipped on the way in.

No bpy here: this runs in CI, in the server, and inside Blender alike.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

Point = Sequence[float]
Points = Mapping[str, Point]

# What the agent marks on the reference image. The first six are required
# because every ratio below depends on at least one of them; the rest sharpen
# the comparison when the reference actually shows them.
REQUIRED_POINTS = ("head_top", "chin", "eye", "shoulder_l", "shoulder_r", "ground")
OPTIONAL_POINTS = ("hip", "ear_l", "ear_r", "hand_l", "hand_r", "knee")
REFERENCE_POINTS = REQUIRED_POINTS + OPTIONAL_POINTS

# How each landmark in a model profile maps onto the marked-point vocabulary.
# ground_contact is the origin plane rather than a mesh feature, which is why
# it is the one the model always has and the reference has to be told.
_FROM_LANDMARK = {
    "head_top": "head_top", "chin": "chin", "eye_midpoint": "eye",
    "shoulder_L": "shoulder_l", "shoulder_R": "shoulder_r",
    "hip": "hip", "ear_L": "ear_l", "ear_R": "ear_r",
    "hand_L": "hand_l", "hand_R": "hand_r", "ground_contact": "ground",
}


class ProportionError(ValueError):
    """Raised when points are missing or degenerate, rather than returning a
    number that looks like a measurement and is not."""


def model_points(profile: Mapping) -> dict[str, tuple[float, float]]:
    """Project a model profile's landmarks into front-view image coordinates.

    Front view looks down -Y, so world x is image x and world z is image y with
    the sign flipped. Scale is irrelevant: every ratio below divides it out.
    """
    landmarks = profile.get("landmarks") or {}
    points: dict[str, tuple[float, float]] = {}
    for name, marked in _FROM_LANDMARK.items():
        entry = landmarks.get(name)
        if not entry or "position" not in entry:
            continue
        x, _y, z = entry["position"]
        points[marked] = (float(x), -float(z))
    return points


def _need(points: Points, *names: str) -> list[tuple[float, float]]:
    out = []
    for name in names:
        p = points.get(name)
        if p is None:
            raise ProportionError(f"missing point '{name}'")
        out.append((float(p[0]), float(p[1])))
    return out


def measure(points: Points) -> dict[str, float]:
    """Scale-invariant proportions from marked points.

    Every value is a ratio, so a reference photographed at any size and a model
    built at any height produce directly comparable numbers.
    """
    (head_top, chin, eye, shoulder_l, shoulder_r, ground) = _need(
        points, *REQUIRED_POINTS)

    head_h = chin[1] - head_top[1]
    total_h = ground[1] - head_top[1]
    if head_h <= 0:
        raise ProportionError("chin must sit below head_top in image coordinates")
    if total_h <= 0:
        raise ProportionError("ground must sit below head_top in image coordinates")

    shoulder_span = abs(shoulder_l[0] - shoulder_r[0])
    shoulder_y = (shoulder_l[1] + shoulder_r[1]) / 2
    out = {
        # The classic figure-drawing measure. Realistic adults land near 7.5;
        # heroic proportion runs to 8, and much past that reads as elongated.
        "heads_tall": total_h / head_h,
        # Where the eyes sit within the skull. Life is close to 0.5, and a
        # value well under that is the single commonest beginner error.
        "eye_line": (eye[1] - head_top[1]) / head_h,
        "shoulder_span_heads": shoulder_span / head_h,
        # How far the shoulder line sits below the crown, as a fraction of the
        # whole figure. Catches a neck that is too long, which reads as frail.
        "shoulder_height_frac": (shoulder_y - head_top[1]) / total_h,
    }

    hip = points.get("hip")
    if hip is not None:
        out["hip_height_frac"] = (ground[1] - float(hip[1])) / total_h

    ear_l, ear_r = points.get("ear_l"), points.get("ear_r")
    if ear_l is not None and ear_r is not None:
        head_w = abs(float(ear_l[0]) - float(ear_r[0]))
        if head_w > 0:
            out["head_aspect"] = head_h / head_w
            out["shoulder_span_head_widths"] = shoulder_span / head_w

    hands = [points.get("hand_l"), points.get("hand_r")]
    hands = [h for h in hands if h is not None]
    if hands:
        hand_y = sum(float(h[1]) for h in hands) / len(hands)
        shoulder_y = (shoulder_l[1] + shoulder_r[1]) / 2
        out["arm_drop_frac"] = (hand_y - shoulder_y) / total_h

    knee = points.get("knee")
    if knee is not None:
        out["knee_height_frac"] = (ground[1] - float(knee[1])) / total_h
    return out


# How far a ratio may drift before it is worth the agent's attention. These are
# relative to the reference value. Anything under `notable` is inside the noise
# of marking points on stylized artwork by eye, and chasing it wastes turns.
NOTABLE = 0.05
STRONG = 0.12


def compare(reference: Points, model: Points) -> list[dict]:
    """Row per shared measure, worst disagreement first.

    Only measures both sides can produce are compared; a reference that does
    not show the hands simply yields no arm row rather than a fabricated one.
    """
    ref, mod = measure(reference), measure(model)
    rows = []
    for key in sorted(set(ref) & set(mod)):
        r, m = ref[key], mod[key]
        delta = m - r
        relative = abs(delta) / abs(r) if r else float("inf")
        rows.append({
            "measure": key,
            "reference": round(r, 4),
            "model": round(m, 4),
            "delta": round(delta, 4),
            "relative": round(relative, 4),
            "verdict": "strong" if relative >= STRONG
                       else "notable" if relative >= NOTABLE else "ok",
        })
    rows.sort(key=lambda row: -row["relative"])
    return rows


def summarize(rows: Iterable[Mapping]) -> str:
    """A table the agent reads back after a build. Plain text on purpose: it
    goes into a tool result, and a rendered table survives that better than
    nested JSON the model has to reassemble."""
    rows = list(rows)
    if not rows:
        return "no comparable measures: the reference is missing required points"
    width = max(len(str(r["measure"])) for r in rows)
    lines = [f"{'measure'.ljust(width)}  reference    model    delta   verdict",
             f"{'-' * width}  ---------  -------  -------   -------"]
    for r in rows:
        lines.append(
            f"{str(r['measure']).ljust(width)}  {r['reference']:9.3f}  "
            f"{r['model']:7.3f}  {r['delta']:+7.3f}   {r['verdict']}")
    worst = rows[0]
    if worst["verdict"] == "ok":
        lines.append("\nEvery measure is within tolerance of the reference.")
    else:
        lines.append(
            f"\nWorst: {worst['measure']} is {'high' if worst['delta'] > 0 else 'low'} "
            f"by {abs(worst['relative']) * 100:.0f}%. Fix that before anything else.")
    return "\n".join(lines)


def validate_points(points: Points) -> list[str]:
    """Problems with a set of marked points, as messages. Empty means usable.

    Marked points are normalized image coordinates, so anything outside 0..1 is
    a marking error rather than an unusual pose.
    """
    problems = []
    for name in REQUIRED_POINTS:
        if name not in points:
            problems.append(f"missing required point '{name}'")
    for name, value in points.items():
        if name not in REFERENCE_POINTS:
            problems.append(f"unknown point '{name}'; expected one of "
                            f"{', '.join(REFERENCE_POINTS)}")
            continue
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, IndexError, ValueError):
            problems.append(f"point '{name}' must be [x, y]")
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            problems.append(f"point '{name}' at ({x:.3f}, {y:.3f}) is outside the "
                            "image; coordinates are normalized 0..1")
    if problems:
        return problems
    try:
        measure(points)
    except ProportionError as exc:
        problems.append(str(exc))
    return problems
