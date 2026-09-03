"""Proportion measurement, for comparing a built model against its reference.

The agent can see both images, but two pictures at different framings cannot be
measured against each other by eye, and "looks about right" does not converge.
This turns the comparison into scale-invariant ratios that get better or worse.

Both sides reduce to the same thing: a handful of named 2D points. The reference
side is marked on the image by the agent, in normalized coordinates. The model
side is its own landmarks projected to the front view. One measurement function
then runs over either, so the two are guaranteed to be computed the same way.

Two things this gets right that are easy to get wrong:

Aspect. Normalized image coordinates divide x by width and y by height, so on
any non-square image a horizontal distance and a vertical one are in different
units. Comparing shoulder span to head height without correcting for that is
wrong by the aspect ratio, which on a portrait crop is 25% — larger than the
threshold at which this module tells an agent to go and fix something. Marked
points are therefore converted to isotropic units at the boundary.

Partial references. A reference is often a portrait crop with no feet in frame.
Only head_top and chin are required, because they define the measuring unit;
every other measure is emitted only when its inputs are actually present. A
missing ground plane costs you the height ratios and nothing else.

Image convention throughout: x increases right, y increases DOWN, because that
is how marked points arrive. Model landmarks are Z-up world space and get
flipped on the way in.

No bpy here: this runs in CI, in the server, and inside Blender alike.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

Point = Sequence[float]
Points = Mapping[str, Point]

# Head height is the unit every ratio is expressed in, so these two are the
# only points without which nothing can be measured at all.
REQUIRED_POINTS = ("head_top", "chin")
OPTIONAL_POINTS = ("eye", "shoulder_l", "shoulder_r", "ground", "hip",
                   "ear_l", "ear_r", "hand_l", "hand_r", "knee")
REFERENCE_POINTS = REQUIRED_POINTS + OPTIONAL_POINTS

# How each landmark in a model profile maps onto the marked-point vocabulary.
_FROM_LANDMARK = {
    "head_top": "head_top", "chin": "chin", "eye_midpoint": "eye",
    "shoulder_L": "shoulder_l", "shoulder_R": "shoulder_r",
    "hip": "hip", "ear_L": "ear_l", "ear_R": "ear_r",
    "hand_L": "hand_l", "hand_R": "hand_r", "ground_contact": "ground",
}


class ProportionError(ValueError):
    """Raised when points are missing or degenerate, rather than returning a
    number that looks like a measurement and is not."""


def to_isotropic(points: Points, width: float, height: float) -> dict[str, tuple[float, float]]:
    """Put marked points into units where x and y are the same length.

    Normalized coordinates are x/width and y/height. Scaling x by width/height
    expresses both in units of image height, which is what makes a horizontal
    span comparable to a vertical one.
    """
    if width <= 0 or height <= 0:
        raise ProportionError(f"image size must be positive, got {width}x{height}")
    aspect = float(width) / float(height)
    return {k: (float(p[0]) * aspect, float(p[1])) for k, p in points.items()}


def model_points(profile: Mapping) -> dict[str, tuple[float, float]]:
    """Project a model profile's landmarks into front-view coordinates.

    Front view looks down -Y, so world x is image x and world z is image y with
    the sign flipped. World space is already isotropic — both axes are meters —
    so no aspect correction applies here.
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


def _get(points: Points, *names: str):
    """Every named point, or None if any is absent. Optional measures use this
    so that a partial reference yields fewer rows rather than invented ones."""
    out = []
    for name in names:
        p = points.get(name)
        if p is None:
            return None
        out.append((float(p[0]), float(p[1])))
    return out


def measure(points: Points) -> dict[str, float]:
    """Scale-invariant proportions from points already in isotropic units.

    Only head_top and chin are needed. Everything else is emitted when it can
    be, so a portrait crop and a full figure both produce usable output.
    """
    required = _get(points, *REQUIRED_POINTS)
    if required is None:
        missing = [n for n in REQUIRED_POINTS if n not in points]
        raise ProportionError(f"missing required point(s): {', '.join(missing)}")
    head_top, chin = required

    head_h = chin[1] - head_top[1]
    if head_h <= 0:
        raise ProportionError(
            "chin must sit below head_top in image coordinates; these look marked "
            "upside down, which would make every ratio below plausible and wrong")

    out: dict[str, float] = {}

    if (p := _get(points, "eye")) is not None:
        # Where the eyes sit within the skull. Life is close to 0.5, and well
        # under that is the single commonest beginner error.
        out["eye_line"] = (p[0][1] - head_top[1]) / head_h

    if (p := _get(points, "ear_l", "ear_r")) is not None:
        head_w = abs(p[0][0] - p[1][0])
        if head_w > 0:
            out["head_aspect"] = head_h / head_w

    shoulders = _get(points, "shoulder_l", "shoulder_r")
    if shoulders is not None:
        span = abs(shoulders[0][0] - shoulders[1][0])
        shoulder_y = (shoulders[0][1] + shoulders[1][1]) / 2
        out["shoulder_span_heads"] = span / head_h
        # How far the shoulder line sits below the crown, in head heights.
        # Expressed against the head rather than total height so it survives a
        # reference that is cropped above the feet.
        out["shoulder_drop_heads"] = (shoulder_y - head_top[1]) / head_h
        if "head_aspect" in out:
            head_w = head_h / out["head_aspect"]
            out["shoulder_span_head_widths"] = span / head_w

    ground = _get(points, "ground")
    if ground is not None:
        total_h = ground[0][1] - head_top[1]
        if total_h <= 0:
            raise ProportionError("ground must sit below head_top in image coordinates")
        # The classic figure-drawing measure. Realistic adults land near 7.5;
        # heroic runs to 8, and much past that reads as elongated.
        out["heads_tall"] = total_h / head_h
        if (p := _get(points, "hip")) is not None:
            out["hip_height_frac"] = (ground[0][1] - p[0][1]) / total_h
        if (p := _get(points, "knee")) is not None:
            out["knee_height_frac"] = (ground[0][1] - p[0][1]) / total_h

    hands = [points.get("hand_l"), points.get("hand_r")]
    hands = [h for h in hands if h is not None]
    if hands and shoulders is not None:
        hand_y = sum(float(h[1]) for h in hands) / len(hands)
        shoulder_y = (shoulders[0][1] + shoulders[1][1]) / 2
        out["arm_drop_heads"] = (hand_y - shoulder_y) / head_h
    return out


# How far a ratio may drift before it is worth the agent's attention. Relative
# to the reference value. Anything under `notable` is inside the noise of
# marking points by eye on stylized artwork, and chasing it wastes turns.
NOTABLE = 0.05
STRONG = 0.12


def compare(reference: Points, model: Points) -> list[dict]:
    """Row per shared measure, worst disagreement first.

    Only measures both sides can produce are compared; a reference cropped
    above the knees yields no height row rather than a fabricated one.
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


def summarize(rows: Iterable[Mapping], skipped: Iterable[str] = ()) -> str:
    """A table the agent reads back after a build. Plain text on purpose: it
    goes into a tool result, and a rendered table survives that better than
    nested JSON the model has to reassemble."""
    rows, skipped = list(rows), list(skipped)
    if not rows:
        return ("no comparable measures: the reference needs at least head_top "
                "and chin, plus one other point")
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
            f"by {worst['relative'] * 100:.0f}%. Fix that before anything else.")
    if skipped:
        lines.append(f"Not measurable from this reference: {', '.join(sorted(skipped))}.")
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
    # Aspect is irrelevant to the checks measure() makes here, so any positive
    # square stands in: this is asking whether the points are coherent at all.
    try:
        measure(to_isotropic(points, 1.0, 1.0))
    except ProportionError as exc:
        problems.append(str(exc))
    return problems


def png_size(path: str) -> tuple[int, int]:
    """Width and height from a PNG header, so marking does not need Pillow.

    Raises for anything that is not a PNG rather than guessing, since a wrong
    aspect silently biases every width measure.
    """
    with open(path, "rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ProportionError(
            f"{path} is not a PNG; pass image_size=[width, height] explicitly")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
