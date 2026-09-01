---
name: camera
description: Framing, lens, movement, focus.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- Lens is chosen from the bible's lens_set. No other focal length exists. No zoom mid-move.
- Aim at landmarks (@hero.eye_midpoint), never at world coordinates. A move is a spherical offset from
  a landmark; use camera presets (list_presets camera) before inventing a move.
- One camera per shot. Depth of field focus_target on the subject's eyes for close and medium shots.
- Preview from the scene camera at first, middle and last frame. A move that works at frame 1 and
  frame 96 can still cross an obstacle at frame 40.
- Director takes (list_takes / apply_take) outrank anything you would derive. If a take exists, use it.
- Headroom, look room, and eyeline continuity across a cut are your job; check the previous shot.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

