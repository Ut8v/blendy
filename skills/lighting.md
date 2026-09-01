---
name: lighting
description: Key, fill, rim, world, mood.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- Start from the look's lighting_preset (apply_preset lighting <name>). The look owns world and key
  direction; you adjust within it, you do not replace it.
- Key direction follows the bible look's key_direction, consistent across every shot in the scene.
- Use lookdev previews (EEVEE) for lighting; fast previews are unlit and tell you nothing here.
- Interior shots need a visible practical source or the space reads as a void.
- Energy is in watts; a 1000W area light in a kitchen is not subtle. Check exposure on skin first.
- Never light for Cycles unless the breakdown marks the shot render_engine CYCLES.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

