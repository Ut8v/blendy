"""Model recipe validation. Plain Python, no bpy.

Schema per part (so one bad part does not mask the rest), op-specific params,
then semantics: part graph, references, skin graphs, profiles, landmarks, and
the core character vocabulary.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .issues import Issue, ValidationResult, pointer
from .refs import CHARACTER_CORE_LANDMARKS
from .validate import SPEC_DIR, _fastjsonschema, _subvalidator, load_schema, schema_issues

MODEL_SCHEMA = SPEC_DIR / "model.schema.json"
MODEL_VERSION = "1.0"
_PARAM_SCHEMAS = {"primitive": "primitive_params", "skin": "skin_params", "revolve": "revolve_params",
                  "extrude": "extrude_params", "tube": "tube_params", "metaball": "metaball_params",
                  "hair": "hair_params", "loft": "loft_params", "head": "head_params",
                  "hand": "hand_params"}
# Named points each builder publishes, for `point:` landmark anchors.
POINT_NAMES = {
    "head": {"eye_l", "eye_r", "eye_midpoint", "ear_l", "ear_r", "chin", "head_top",
             "nose_tip", "mouth", "jaw_l", "jaw_r", "neck", "brow"},
    "hand": {"wrist", "palm", "knuckles", "fingertip", "thumb_tip"},
    "loft": {"start", "end"},
}
_ANCHORS = {"center", "origin", "top", "bottom", "front", "back", "left", "right"}
_HEAVY_OPS = {"metaball", "hair"}


def _params_issues(schema, part: dict[str, Any], idx: int) -> list[Issue]:
    exc = _fastjsonschema().JsonSchemaValueException
    if part.get("mirror_of"):
        return []
    key = _PARAM_SCHEMAS.get(part.get("op"))
    if key is None:
        return []
    try:
        _subvalidator(schema, schema["$defs"][key], key)(part.get("params", {}))
    except exc as e:
        return [Issue("error", "schema", "invalid_params", f"{part['op']} params: {e.message}",
                      pointer(["parts", idx, "params"] + list(e.path[1:])), part.get("id"))]
    return []


def validate_model(model: Any, strict: bool = False) -> ValidationResult:
    schema = load_schema(MODEL_SCHEMA)
    issues = schema_issues(model, schema, MODEL_VERSION)
    if not issues:
        for idx, part in enumerate(model["parts"]):
            issues.extend(_params_issues(schema, part, idx))
    if not issues:
        issues = _semantic(model)
    result = ValidationResult(issues)
    return result.strict() if strict else result


def _semantic(m: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append
    parts = m["parts"]
    by_id: dict[str, dict[str, Any]] = {}
    for i, p in enumerate(parts):
        if p["id"] in by_id:
            add(Issue("error", "semantic", "duplicate_id", f"part id '{p['id']}' repeated",
                      pointer(["parts", i, "id"]), p["id"]))
        by_id[p["id"]] = p

    # parent graph: refs exist, no cycles, parent-before-child is not required (compiler sorts)
    for i, p in enumerate(parts):
        par = p["parent"]
        if par is not None and par not in by_id:
            add(Issue("error", "semantic", "unknown_ref", f"parent '{par}' is not a part "
                      f"(have: {', '.join(sorted(by_id))})", pointer(["parts", i, "parent"]), p["id"]))
    for p in parts:
        seen, cur = set(), p["parent"]
        while cur is not None and cur in by_id:
            if cur == p["id"] or cur in seen:
                add(Issue("error", "semantic", "parent_cycle", f"part '{p['id']}' is its own ancestor",
                          "/parts", p["id"]))
                break
            seen.add(cur)
            cur = by_id[cur]["parent"]

    for i, p in enumerate(parts):
        pid, base = p["id"], ["parts", i]
        if p["material"] is not None and p["material"] not in m["materials"]:
            add(Issue("error", "semantic", "unknown_material",
                      f"material '{p['material']}' is not defined (have: {', '.join(sorted(m['materials'])) or 'none'})",
                      pointer(base + ["material"]), pid))
        at = p.get("at")
        if at:
            if at["part"] not in by_id:
                add(Issue("error", "semantic", "unknown_ref", f"at.part '{at['part']}' is not a part",
                          pointer(base + ["at"]), pid))
            elif at["part"] == pid:
                add(Issue("error", "semantic", "self_reference", "a part cannot be placed at itself",
                          pointer(base + ["at"]), pid))
            else:
                src = by_id.get(by_id[at["part"]].get("mirror_of") or "", by_id[at["part"]])
                have = POINT_NAMES.get(src["op"], set())
                if at["point"] not in have:
                    add(Issue("error", "semantic", "unknown_point",
                              f"a {src['op']} part publishes no point '{at['point']}' "
                              f"(have: {', '.join(sorted(have)) or 'none'})", pointer(base + ["at"]), pid))
        if p.get("mirror_of"):
            src = p["mirror_of"]
            if src not in by_id:
                add(Issue("error", "semantic", "unknown_ref", f"mirror_of '{src}' is not a part", pointer(base + ["mirror_of"]), pid))
            elif src == pid or by_id[src].get("mirror_of"):
                add(Issue("error", "semantic", "bad_mirror", "mirror_of must name a part built from params", pointer(base + ["mirror_of"]), pid))
        for j, mod in enumerate(p["modifiers"]):
            _modifier_issues(mod, pointer(base + ["modifiers", j]), pid, by_id, add)
        if p["op"] == "skin" and not p.get("mirror_of"):
            _skin_issues(p["params"], pointer(base + ["params"]), pid, add)
        if p["op"] == "revolve" and not p.get("mirror_of"):
            prof = p["params"]["profile"]
            if any(r < 0 for r, _ in prof):
                add(Issue("error", "semantic", "bad_profile", "revolve radii must be >= 0", pointer(base + ["params", "profile"]), pid))
            if any(b[1] < a[1] for a, b in zip(prof, prof[1:])):
                add(Issue("warning", "semantic", "profile_not_monotonic", "revolve profile z goes back down; the surface will self-intersect", pointer(base + ["params", "profile"]), pid))
        if p["op"] == "tube" and not p.get("mirror_of"):
            r, pts = p["params"]["radius"], p["params"]["points"]
            if isinstance(r, list) and len(r) != len(pts):
                add(Issue("error", "semantic", "radius_count", f"tube has {len(pts)} points but {len(r)} radii", pointer(base + ["params", "radius"]), pid))
        for axis, s in zip("xyz", p["transform"]["scale"]):
            if s == 0:
                add(Issue("warning", "semantic", "zero_scale", f"scale.{axis} is 0", pointer(base + ["transform"]), pid))

    for name, lm in m["landmarks"].items():
        if "part" in lm:
            if lm["part"] not in by_id:
                add(Issue("error", "semantic", "unknown_ref", f"landmark '{name}' names part '{lm['part']}' which does not exist", f"/landmarks/{name}"))
            elif lm["anchor"].startswith("point:"):
                part = by_id[lm["part"]]
                src = by_id.get(part.get("mirror_of") or "", part)
                have = POINT_NAMES.get(src["op"], set())
                pname = lm["anchor"][6:]
                if pname not in have:
                    add(Issue("error", "semantic", "unknown_point",
                              f"landmark '{name}': a {src['op']} part publishes no point '{pname}' "
                              f"(have: {', '.join(sorted(have)) or 'none; only head, hand and loft publish points'})",
                              f"/landmarks/{name}"))
            elif lm["anchor"].startswith("joint:"):
                part = by_id[lm["part"]]
                src = by_id.get(part.get("mirror_of") or "", part)
                joints = src.get("params", {}).get("joints", {}) if src["op"] == "skin" else {}
                jname = lm["anchor"][6:]
                if jname not in joints:
                    add(Issue("error", "semantic", "unknown_joint", f"landmark '{name}': part '{lm['part']}' has no joint '{jname}' "
                              f"(have: {', '.join(sorted(joints)) or 'none; only skin parts have joints'})", f"/landmarks/{name}"))
    if m["kind"] == "character":
        missing = [n for n in CHARACTER_CORE_LANDMARKS if n not in m["landmarks"]]
        if missing:
            level = "error" if m.get("skeleton") else "warning"
            add(Issue(level, "semantic", "incomplete_character_landmarks",
                      "character lacks core landmarks (needed before rigging): " + ", ".join(missing), "/landmarks"))
    heavy = sum(1 for p in parts if p["op"] in _HEAVY_OPS)
    if heavy > 8:
        add(Issue("warning", "semantic", "heavy_model", f"{heavy} metaball/hair parts; builds will be slow", "/parts"))
    return issues


def _modifier_issues(mod, path, pid, by_id, add) -> None:
    t = mod["type"]
    need = {"subdivision": ["levels"], "bevel": ["width"], "mirror": ["axis"], "solidify": ["thickness"],
            "boolean": ["operation", "part"], "displace": ["strength"], "array": ["count", "offset"],
            "push": ["center", "radius", "strength"],
            "shrinkwrap": ["part"], "smooth": ["factor"], "cloth": ["pin", "frame"], "decimate": ["ratio"]}
    for key in need.get(t, []):
        if key not in mod:
            add(Issue("error", "semantic", "modifier_param", f"{t} modifier needs '{key}'", path, pid))
    if "part" in mod:
        if mod["part"] not in by_id:
            add(Issue("error", "semantic", "unknown_ref", f"{t} modifier targets part '{mod['part']}' which does not exist", path, pid))
        elif mod["part"] == pid:
            add(Issue("error", "semantic", "self_reference", f"{t} modifier targets its own part", path, pid))
    if t == "array" and not isinstance(mod.get("offset"), list):
        add(Issue("error", "semantic", "modifier_param", "array offset must be a vec3", path, pid))


def _skin_issues(params, path, pid, add) -> None:
    joints, edges = params["joints"], params["edges"]
    adj: dict[str, set] = {j: set() for j in joints}
    for a, b in edges:
        for j in (a, b):
            if j not in joints:
                add(Issue("error", "semantic", "unknown_joint", f"edge references joint '{j}' which is not defined "
                          f"(have: {', '.join(sorted(joints))})", path + "/edges", pid))
                return
        if a == b:
            add(Issue("error", "semantic", "bad_edge", f"edge from '{a}' to itself", path + "/edges", pid))
            return
        adj[a].add(b); adj[b].add(a)
    root = params.get("root")
    if root is not None and root not in joints:
        add(Issue("error", "semantic", "unknown_joint", f"root '{root}' is not a joint", path + "/root", pid))
    start = root if root in joints else next(iter(joints))
    seen, q = {start}, deque([start])
    while q:
        for n in adj[q.popleft()]:
            if n not in seen:
                seen.add(n); q.append(n)
    if len(seen) != len(joints):
        add(Issue("error", "semantic", "skin_disconnected", f"skin joints not connected: {', '.join(sorted(set(joints) - seen))} "
                  "are unreachable; split into separate parts or add edges", path + "/edges", pid))
