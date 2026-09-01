---
name: animation
description: Rig binding, clip selection, sequencing, timing.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- Motion is retargeted, never keyframed. Clips come from list_clips(skeleton). If the motion you need
  does not exist, say so; do not fake it with transforms.
- Blends live in the NLA: set blend_in / blend_out in frames on the spec entry. No hand f-curves.
- Loop idles; never loop a transition (walk-in, sit-down, turn).
- Dialogue shots: audio timing is fixed. Body clips fit around the line, not the reverse.
- Every clip change: compile, preview at three frames (start, middle, end), look. Feet sliding and
  clipping through furniture show in the three-quarter view.
- exits_with must be true at the last frame: the character ends where the next shot expects them.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

