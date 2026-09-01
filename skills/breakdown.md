---
name: breakdown
description: Script to shot list. Proposes; a human approves every line.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- Input: script (parsed, with line ids), bible, reference images. Output: breakdown.json plus the
  ingest list.
- Resolve every character and location against the bible by name or alias. Anything unresolved is a
  hard failure to report, never a new entry to invent.
- Durations in frames at the bible fps. Dialogue shots: duration >= the line's audio; ask for audio
  before guessing.
- Emit the ingest list first so acquisition fans out while the human reviews the shot list.
- Continuity: enters_with of shot N equals exits_with of shot N-1 within a scene. Write both.
- Beats are one line, plain language, what the audience should understand from the shot.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

