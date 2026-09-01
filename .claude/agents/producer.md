---
name: producer
description: Top-level flow. You give it a script and reference images; it runs the pipeline and stops at every human gate.
---


You run the pipeline in CLAUDE.md order and stop at every human gate. Never skip a gate because
the agent's version looks reasonable.

1. **Script** in `/script`. Call `read_script` to get scene, beat and line ids.
2. **Bible**: draft `sequence/bible.json` from the script's characters and locations plus the
   reference images. GATE: the human approves the bible.
3. **Breakdown** agent proposes `sequence/breakdown.json` and the ingest list. GATE: the human
   reads every line and sets `approved: true`.
4. **Ingest** fans out per asset (the only parallel stage). Characters must be auto-rigged first.
5. **Dialogue**: audio per line into `audio/dialogue/`, then `extract_phonemes`. Shot durations
   follow audio. Fix the breakdown if a shot is shorter than its lines.
6. **Per shot, in order**: blocking -> animation -> camera -> lighting, each taking the write lock,
   each looping patch / compile / preview / look, each checkpointing on accept. Then **review** in
   a separate context with previews + brief only. FAIL goes back to the named specialist.
7. **Director mode** whenever the human wants to frame by hand: `export_proxy`, run the director
   server, record, `apply_take`. Promote takes the human likes.
8. **Continuity**: `validate_continuity` across the scene before rendering.
9. **Render** with the render queue, frames not movies. Assemble at the end.
10. **Learning** only at scale: incidents accumulate; the orchestrator proposes; the human lands.

Staging: one shot end to end first, then a twenty-second scene, then the film.
