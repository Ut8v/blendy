"""Semantic validation of a shot spec: everything JSON Schema cannot express.

Runs after the schema layer passed, so it may assume the shape is sound. No bpy.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .issues import Issue, pointer
from .refs import ProfileIndex, is_ref, parse_ref

_SCALAR, _VEC3 = "scalar", "vec3"
_CHANNEL_ARITY = {
    "location": _VEC3, "rotation_euler": _VEC3, "scale": _VEC3, "color": _VEC3,
    "focal": _SCALAR, "dof.focus_distance": _SCALAR, "dof.f_stop": _SCALAR, "energy": _SCALAR,
}
_CAMERA_CHANNELS = {"location", "rotation_euler", "scale", "focal",
                    "dof.focus_distance", "dof.f_stop"}
_LIGHT_CHANNELS = {"location", "rotation_euler", "scale", "energy", "color"}
_GENERATED_SOURCES: set[str] = set()
_SECTIONS = ("assets", "rigs", "animation", "cameras", "lights", "audio")

Add = Callable[[Issue], None]


class _Ctx:
    """Everything the checks need: the spec, an id table, profiles, and the sink."""

    def __init__(self, spec: dict[str, Any], profiles: ProfileIndex, add: Add):
        self.spec, self.profiles, self.add = spec, profiles, add
        self.kinds: dict[str, str] = {}
        self.assets = {a["id"]: a for a in spec["assets"]}
        f = spec["meta"]
        self.f_start, self.f_end = f["frame_start"], f["frame_end"]

    # --- references ---------------------------------------------------------

    def ref(self, value, want, path, owner, label) -> bool:
        """Resolve an id reference, requiring it to live in section *want*."""
        if value is None:
            return False
        if value not in self.kinds:
            self.add(Issue("error", "semantic", "unknown_ref",
                           f"{label} '{value}' does not match any entity id", path, owner))
            return False
        if want and self.kinds[value] != want:
            self.add(Issue("error", "semantic", "wrong_ref_kind",
                           f"{label} '{value}' is a {self.kinds[value][:-1]}, "
                           f"expected a {want[:-1]}", path, owner))
            return False
        return True

    def landmark(self, value: str, path: str, owner: str | None, label: str) -> bool:
        """Validate "@asset.landmark". The error lists the landmarks that do exist:
        this is the message that teaches the agent an asset's vocabulary."""
        try:
            lref = parse_ref(value)
        except ValueError as e:
            self.add(Issue("error", "semantic", "malformed_landmark_ref", str(e), path, owner))
            return False
        asset = self.assets.get(lref.asset_id)
        if asset is None:
            self.add(Issue("error", "semantic", "unknown_ref",
                           f"{label} '{value}' names asset '{lref.asset_id}', which does not exist",
                           path, owner))
            return False
        table = self.profiles.landmarks_for(asset)
        if table is None and asset["source"] == "model":
            have = ", ".join(self.profiles.model_ids()) or "none"
            self.add(Issue("error", "semantic", "model_not_built",
                           f"{label} '{value}' needs model '{asset['ref']}', which has no profile yet. "
                           f"Build it with preview_model first (built models: {have}).", path, owner))
            return False
        if table is None:
            self.add(Issue("error", "semantic", "asset_not_ingested",
                           f"{label} '{value}' needs the profile of '{lref.asset_id}' "
                           f"({asset['source']}:{asset['ref']}), which has not been ingested. "
                           "Run resolve_asset first; landmarks are derived at ingest, never at scene time.",
                           path, owner))
            return False
        if lref.landmark not in table:
            have = ", ".join(sorted(table)) or "<none>"
            self.add(Issue("error", "semantic", "unknown_landmark",
                           f"{label}: asset '{lref.asset_id}' has no landmark "
                           f"'{lref.landmark}'. Available: {have}", path, owner))
            return False
        return True

    def target(self, value, path, owner, label, want=None) -> bool:
        """A target_ref: entity id or landmark ref."""
        if value is None:
            return False
        if is_ref(value):
            return self.landmark(value, path, owner, label)
        if value == owner:
            self.add(Issue("error", "semantic", "self_reference",
                           f"{label} points at the entity itself", path, owner))
            return False
        return self.ref(value, want, path, owner, label)


def semantic_issues(spec: dict[str, Any], profiles: ProfileIndex) -> list[Issue]:
    issues: list[Issue] = []
    ctx = _Ctx(spec, profiles, issues.append)
    add = issues.append

    if ctx.f_end < ctx.f_start:
        add(Issue("error", "semantic", "bad_frame_range",
                  f"frame_end {ctx.f_end} is before frame_start {ctx.f_start}", "/meta"))

    _collect_ids(ctx)
    for idx, asset in enumerate(spec["assets"]):
        _check_asset(ctx, idx, asset)
    for idx, rig in enumerate(spec["rigs"]):
        _check_rig(ctx, idx, rig)
    _check_animation(ctx)
    for idx, cam in enumerate(spec["cameras"]):
        _check_camera(ctx, idx, cam)
    ctx.ref(spec["render"]["camera"], "cameras", "/render/camera", None, "render.camera")
    for idx, light in enumerate(spec["lights"]):
        _check_light(ctx, idx, light)
    if "sequence" in spec:
        _check_sequence_block(ctx, spec["sequence"])
    _check_render(ctx)
    return issues


def _collect_ids(ctx: _Ctx) -> None:
    owners: dict[str, str] = {}
    for section in _SECTIONS:
        for idx, item in enumerate(ctx.spec.get(section, [])):
            eid = item["id"]
            if eid in owners:
                ctx.add(Issue("error", "semantic", "duplicate_id",
                              f"id '{eid}' is already used by {owners[eid]}; "
                              "ids must be unique across the whole spec",
                              pointer([section, idx, "id"]), eid))
            else:
                owners[eid] = f"{section}[{idx}]"
                ctx.kinds[eid] = section


def _check_transform(ctx: _Ctx, t: dict[str, Any], path: str, eid: str) -> None:
    loc = t["location"]
    if is_ref(loc):
        ctx.landmark(loc, path + "/location", eid, "location")
    anchor = t.get("anchor")
    if anchor is not None:
        ctx.landmark(anchor, path + "/anchor", eid, "anchor")
        if is_ref(loc):
            ctx.add(Issue("error", "semantic", "anchor_and_landmark_location",
                          "location is a landmark ref and anchor is set; use one or the other",
                          path, eid))
    elif "anchor_offset" in t:
        ctx.add(Issue("error", "semantic", "offset_without_anchor",
                      "anchor_offset given but anchor is null", path + "/anchor_offset", eid))
    for axis, s in zip("xyz", t["scale"]):
        if s == 0:
            ctx.add(Issue("warning", "semantic", "zero_scale",
                          f"scale.{axis} is 0, which flattens the object to nothing",
                          path + "/scale", eid))
    for axis, r in zip("xyz", t["rotation_euler"]):
        if abs(r) > 4 * math.pi:
            ctx.add(Issue("warning", "semantic", "suspicious_rotation",
                          f"rotation_euler.{axis} is {r}; rotations are radians, not degrees",
                          path + "/rotation_euler", eid))


def _check_asset(ctx: _Ctx, idx: int, asset: dict[str, Any]) -> None:
    if asset["source"] == "model" and ctx.profiles.profile_for(asset) is None:
        have = ", ".join(ctx.profiles.model_ids()) or "none"
        ctx.add(Issue("error", "semantic", "model_not_built",
                      f"model '{asset['ref']}' has no profile; build it with preview_model "
                      f"(built models: {have})", pointer(["assets", idx, "ref"]), asset["id"]))
    if asset["source"] == "primitive":
        ctx.add(Issue("warning", "semantic", "primitive_asset",
                      "primitive is a blocking placeholder and should not survive "
                      "into a final spec", pointer(["assets", idx]), asset["id"]))
    _check_transform(ctx, asset["transform"], pointer(["assets", idx, "transform"]), asset["id"])


def _check_rig(ctx: _Ctx, idx: int, rig: dict[str, Any]) -> None:
    p = pointer(["rigs", idx, "asset_id"])
    if not ctx.ref(rig["asset_id"], "assets", p, rig["id"], "asset_id"):
        return
    src = ctx.assets[rig["asset_id"]]["source"]
    if src == "primitive":
        ctx.add(Issue("error", "semantic", "primitive_rigged",
                      f"asset '{rig['asset_id']}' is a primitive and has no skeleton to bind",
                      p, rig["id"]))
        return
    profile = ctx.profiles.profile_for(ctx.assets[rig["asset_id"]])
    if src in _GENERATED_SOURCES or (profile and profile.get("flags", {}).get("generated")):
        ok = bool(profile and profile.get("flags", {}).get("rig_ok"))
        ctx.add(Issue("warning" if ok else "error", "semantic", "generated_mesh_rigged",
                      f"asset '{rig['asset_id']}' is a generated mesh with dense irregular "
                      "topology" + ("; profile marks it rig_ok after cleanup" if ok else
                                    "; it needs a cleanup pass (profile flag rig_ok) before it deforms"),
                      p, rig["id"]))
    if profile and profile.get("class") == "character":
        from .refs import CHARACTER_CORE_LANDMARKS
        missing = [n for n in CHARACTER_CORE_LANDMARKS if n not in profile.get("landmarks", {})]
        if missing:
            ctx.add(Issue("error", "semantic", "incomplete_character_profile",
                          f"asset '{rig['asset_id']}' profile lacks core landmarks: "
                          f"{', '.join(missing)}; re-ingest", p, rig["id"]))


def _check_animation(ctx: _Ctx) -> None:
    seen: dict[tuple[str, int], str] = {}
    for idx, clip in enumerate(ctx.spec["animation"]):
        ctx.ref(clip["rig_id"], "rigs", pointer(["animation", idx, "rig_id"]), clip["id"], "rig_id")
        cs = clip["frame_start"]
        if cs > ctx.f_end:
            ctx.add(Issue("warning", "semantic", "strip_outside_range",
                          f"starts at frame {cs}, after the shot ends at {ctx.f_end}",
                          pointer(["animation", idx]), clip["id"]))
        key = (clip["rig_id"], cs)
        if key in seen:
            ctx.add(Issue("warning", "semantic", "coincident_strips",
                          f"starts on frame {cs} on rig '{clip['rig_id']}', same as "
                          f"'{seen[key]}'; NLA blend order between them is arbitrary",
                          pointer(["animation", idx]), clip["id"]))
        else:
            seen[key] = clip["id"]


def _check_keyframes(ctx: _Ctx, entity, allowed, kind, base) -> None:
    eid = entity["id"]
    seen: dict[tuple[str, int], int] = {}
    for k_idx, kf in enumerate(entity.get("keyframes", [])):
        p = pointer(base + ["keyframes", k_idx])
        ch, frame, value = kf["channel"], kf["frame"], kf["value"]
        if ch not in allowed:
            ctx.add(Issue("error", "semantic", "bad_channel",
                          f"channel '{ch}' is not animatable on a {kind}", p, eid))
            continue
        arity, is_vec = _CHANNEL_ARITY[ch], isinstance(value, list)
        if arity == _VEC3 and not is_vec:
            ctx.add(Issue("error", "semantic", "keyframe_arity",
                          f"channel '{ch}' takes a 3-component value, got a number", p, eid))
        elif arity == _SCALAR and is_vec:
            ctx.add(Issue("error", "semantic", "keyframe_arity",
                          f"channel '{ch}' takes a single number, got a 3-component value", p, eid))
        if (ch, frame) in seen:
            ctx.add(Issue("error", "semantic", "duplicate_keyframe",
                          f"channel '{ch}' already has a keyframe on frame {frame} "
                          f"(index {seen[(ch, frame)]})", p, eid))
        else:
            seen[(ch, frame)] = k_idx
        if not (ctx.f_start <= frame <= ctx.f_end):
            ctx.add(Issue("warning", "semantic", "keyframe_outside_range",
                          f"frame {frame} is outside the shot range "
                          f"{ctx.f_start}..{ctx.f_end}", p, eid))


def _check_move(ctx: _Ctx, cam: dict[str, Any], base: list) -> None:
    move, eid = cam["move"], cam["id"]
    p = pointer(base + ["move"])
    if cam.get("track_target") is not None:
        ctx.add(Issue("error", "semantic", "move_and_track_target",
                      "a landmark move already aims the camera; track_target must be null", p, eid))
    if any(k["channel"] in ("location", "rotation_euler") for k in cam.get("keyframes", [])):
        ctx.add(Issue("error", "semantic", "move_and_transform_keyframes",
                      "a landmark move owns location/rotation; remove those keyframes", p, eid))
    last = None
    for k_idx, key in enumerate(move["keys"]):
        kp = pointer(base + ["move", "keys", k_idx])
        if last is not None and key["frame"] <= last:
            ctx.add(Issue("error", "semantic", "unsorted_move_keys",
                          f"frame {key['frame']} is not after the previous key ({last})", kp, eid))
        last = key["frame"]
        if isinstance(key["target"], list):
            ctx.add(Issue("warning", "semantic", "unsnapped_key",
                          f"key at frame {key['frame']} targets world space, not a landmark; "
                          "it will break when the character is repositioned", kp, eid))
        else:
            ctx.target(key["target"], kp + "/target", eid, "move target")


def _check_camera(ctx: _Ctx, idx: int, cam: dict[str, Any]) -> None:
    base = ["cameras", idx]
    _check_transform(ctx, cam["transform"], pointer(base + ["transform"]), cam["id"])
    ctx.target(cam.get("track_target"), pointer(base + ["track_target"]), cam["id"], "track_target")
    dof = cam.get("dof")
    if dof:
        ctx.target(dof.get("focus_target"), pointer(base + ["dof", "focus_target"]),
                   cam["id"], "dof.focus_target")
        if cam["type"] == "ORTHO":
            ctx.add(Issue("warning", "semantic", "ortho_dof",
                          "depth of field on an orthographic camera has no useful effect",
                          pointer(base + ["dof"]), cam["id"]))
    _check_keyframes(ctx, cam, _CAMERA_CHANNELS, "camera", base)
    if any(k["channel"].startswith("dof.") for k in cam.get("keyframes", [])) and not dof:
        ctx.add(Issue("error", "semantic", "keyframe_without_dof",
                      "keyframes a dof channel but dof is null", pointer(base + ["keyframes"]), cam["id"]))
    if cam.get("move"):
        _check_move(ctx, cam, base)


def _check_light(ctx: _Ctx, idx: int, light: dict[str, Any]) -> None:
    base = ["lights", idx]
    _check_transform(ctx, light["transform"], pointer(base + ["transform"]), light["id"])
    if light["type"] == "AREA" and "size" not in light:
        ctx.add(Issue("error", "semantic", "missing_size",
                      "AREA lights require an explicit size", pointer(base), light["id"]))
    if light["type"] != "SPOT":
        for key in ("spot_size", "spot_blend"):
            if key in light:
                ctx.add(Issue("error", "semantic", "irrelevant_field",
                              f"'{key}' only applies to SPOT lights, this is a {light['type']}",
                              pointer(base + [key]), light["id"]))
    ctx.target(light.get("track_target"), pointer(base + ["track_target"]), light["id"], "track_target")
    _check_keyframes(ctx, light, _LIGHT_CHANNELS, "light", base)


def _check_sequence_block(ctx: _Ctx, seq: dict[str, Any]) -> None:
    cast_ids = set()
    for i, member in enumerate(seq["cast"]):
        cast_ids.add(member["cast_id"])
        ctx.ref(member["rig_id"], "rigs", pointer(["sequence", "cast", i, "rig_id"]),
                None, f"cast '{member['cast_id']}' rig_id")
    for i, line in enumerate(seq.get("dialogue", [])):
        if line["cast_id"] not in cast_ids:
            ctx.add(Issue("error", "semantic", "dialogue_cast_absent",
                          f"line '{line['line_id']}' is spoken by '{line['cast_id']}', "
                          "who is not in this shot's cast", pointer(["sequence", "dialogue", i])))


def _check_render(ctx: _Ctx) -> None:
    spec, render = ctx.spec, ctx.spec["render"]
    w = spec["world"]
    lit = bool(spec["lights"]) or w["hdri"] is not None or (
        w["strength"] > 0 and any(c > 0 for c in w["background_color"]))
    if render["engine"] != "BLENDER_WORKBENCH" and not lit:
        ctx.add(Issue("warning", "semantic", "unlit_scene",
                      f"{render['engine']} render with no lights, no HDRI and a black world "
                      "will come out black", "/render"))
    if render["engine"] == "BLENDER_WORKBENCH" and render["samples"] > 1:
        ctx.add(Issue("warning", "semantic", "unused_samples",
                      "Workbench ignores samples; this is a fast preview render", "/render/samples"))
    if render["engine"] == "CYCLES":
        ctx.add(Issue("warning", "semantic", "cycles_engine",
                      "Cycles is for selected final shots only, never the preview loop",
                      "/render/engine"))
