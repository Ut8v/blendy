---
name: ingest
description: Acquiring and profiling assets. Runs per asset, never touches a scene spec.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- One asset in, one profile out. resolve_asset acquires, caches by content hash, ingests once.
- Characters must arrive rigged (Mixamo / Auto-Rig Pro). Never extract character landmarks visually.
- Props: call get_asset_profile, look at the six views under preview/ingest/<hash>/, then add_sockets
  with (view, u, v, name). Two views agreeing on a point is the confidence check. A ray that misses
  is a failed landmark; do not retry the same (u, v).
- Every socket carries a normal. "Where the seat is" is useless without "which way it faces".
- Generated meshes (meshy, tripo) are flagged; they are fine for set dressing and must not be rigged
  without a cleanup pass that sets rig_ok.
- Record license and origin honestly. This matters the moment output is published.
- Never derive anything at scene time that belongs in the profile. If you are computing anatomy in a
  shot, stop and re-ingest.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

