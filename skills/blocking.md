---
name: blocking
description: Layout, scale, staging. Primitives allowed here and only here.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- You hold the write lock first. Place the location assets, then cast, at bible blocking marks.
- Primitives are for stand-ins only. They must be replaced before the shot is accepted.
- Characters stand on ground_plane: their @<id>.ground_contact must sit at the location's ground_plane.
- Check scale against the bible's scale_reference. A character 8% too tall reads as a different person.
- After every patch: validate, compile, render_preview (three angles), look. The top view catches
  intersections and floating objects that look fine head-on. Never batch edits before looking.
- Respect enters_with: every cast member starts where the previous shot left them.
- Do not touch cameras or lights. Those specialists come after you.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

