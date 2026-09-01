# Blendy

An agent harness for producing 3D animation in Blender, end to end, in-house. Agents
model, texture, rig, animate, light and shoot; a human directs: camera moves, notes on
the characters, approval at the gates. The only outside software is Blender and the
add-ons that ship with it. The only outside input is a reference image.

The scene is a document. Agents edit declarative JSON specs (a shot, a model recipe, a
pose); a deterministic compiler turns them into Blender scenes; every mutation is
followed by a preview render the agent reads back. Nothing else writes to Blender.

Agents run as Claude Code subagents (`.claude/agents/`) against the MCP server in
`.mcp.json`. The server exposes typed tools only; no agent can execute arbitrary code
inside Blender.

## Status

| Layer | Where | State |
|---|---|---|
| Shot / bible / breakdown schemas and validators | `spec/`, `compiler/validate*.py` | done, tested |
| Landmark references `@asset.landmark`, profiles | `compiler/refs.py` | done, tested |
| Compiler: fixed step order, resolvers, NLA, lip sync | `compiler/build.py`, `compiler/resolvers/` | done, headless tests pass |
| Previews (3 angles), final frames, proxy export, ingest | `compiler/{preview,render,proxy,ingest}.py` | done, headless tests pass |
| MCP server, 53 typed tools | `server/mcp_server.py`, `server/tools/` | done, tested |
| JSON Patch by id, write lock, checkpoints, SQLite | `server/{patch,session,db}.py` | done, tested |
| Sequence layer: bible, breakdown, continuity, inheritance | `compiler/validate_sequence.py`, `sequence/` | done, tested |
| Script parser (Fountain-lite) | `server/script.py` | done, tested |
| Dialogue + phonemes (Rhubarb) | `server/lipsync.py` | driver done; needs Rhubarb installed |
| Presets, director mode, takes, decimation | `server/{presets,director}.py`, `director/` | done, tested |
| Studio: chat + previews + director + shot panel in one UI | `server/studio.py`, `director/web/` | done, tested |
| Render queue, evals, incidents, skills, orchestrator, corrections | `server/`, `evals/` | done, tested |
| Modeling layer (recipes, builders, turntables, model profiles) | `spec/model.schema.json`, `compiler/modeling/` | in progress |
| Rigging (Rigify from landmarks), posing, procedures | | not started |

First proof: `spec/scenes/haldin_blockout.json` was blocked out, lit and shot by the
agents from `assets/references/haldin.png` through the Studio, and rendered to
`renders/haldin_blockout.mp4`. It is a greybox; the modeling layer is what turns that
into a character.

## Environment

- Blender 5.1.x at `/Applications/Blender.app` (override with `BLENDER=`). Bundled Python
  is 3.13; `.python-version` pins it.
- Engine identifiers on 5.x are `BLENDER_WORKBENCH`, `BLENDER_EEVEE`, `CYCLES`.
  `BLENDER_EEVEE_NEXT` was 4.2-only and raises `TypeError`.
- Blender bundles `fastjsonschema` and `numpy`; the venv installs the same plus `mcp`.
- Rhubarb Lip Sync is optional; set `RHUBARB=/path/to/rhubarb` when installed.

## Setup

```sh
python3 -m venv .venv && ./.venv/bin/pip install fastjsonschema mcp numpy
tests/run_all.sh                 # unit tests, no Blender
tests/run_all.sh --blender       # plus the compiler suite, headless against real Blender
```

Claude Code picks up `.mcp.json` and `.claude/agents/*` when the directory is opened.

## Studio

```sh
./.venv/bin/python -m server.studio          # http://127.0.0.1:8765
```

One browser window: chat on the left (every tool call and preview inline, text streams
live), previews / director / renders tabs in the middle, and the bible, breakdown
approval, validation, checkpoints and takes on the right. Quick buttons run the
specialist passes.

The chat runs the `claude` CLI headlessly with this repo's `.mcp.json` and agents, on
the account that is logged in; `ANTHROPIC_API_KEY` is stripped from the child
environment. Agents get MCP tools and read-only file tools, nothing else, and
conversations resume across turns via the CLI session id (`director/studio_state.json`).

## Running things by hand

```sh
./.venv/bin/python compiler/validate.py sequence/shots/s01_002.json
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python compiler/build.py -- --spec spec/scenes/blockout_example.json \
  --out checkpoints/_build/blockout.blend --mode preview --quality fast
./.venv/bin/python -m server.render_queue s01_001 s01_002
./.venv/bin/python -m evals.run --accept
ffmpeg -framerate 24 -i renders/<shot>/frame_%04d.png -c:v libx264 -pix_fmt yuv420p <shot>.mp4
```

## The pipeline and its human gates

`agents/producer.md` is the top-level flow. Gates that are never skipped:

1. Bible: drafted from the script and the reference images; approved by a human.
2. Breakdown: proposed with `approved: false`; a human reads every line and flips it.
   `new_shot_from_breakdown` refuses an unapproved breakdown.
3. Review: a separate-context agent that sees previews and the brief only, and writes
   structured incidents.
4. Skill edits: the orchestrator proposes, the frozen eval set gates, a human lands.

Per shot the specialists run in sequence, each holding the single write lock:
blocking, animation, camera, lighting, review. Each loop is patch, compile, preview,
look, accept or revise, checkpointing on accept.

A hand-written twenty-second scene is the staging target: `script/kitchen.fountain`,
`sequence/bible.json`, `sequence/breakdown.json`, `sequence/shots/`.

## Design notes

- Shot spec v1.1 adds a `sequence` block (shot and scene ids, location, look, cast to rig
  map, dialogue, enters/exits state), `audio[]`, camera `move` (landmark-anchored
  spherical keys), `transform.anchor` for parts that follow a landmark, and
  `render.camera`. `version` is a top-level constant.
- Ids are lowercase snake_case and unique across the whole spec. JSON-Patch paths may
  address array items as `/assets/id=hero_block/...`.
- Inheritance is enforced: fps, resolution, engine, focal lengths in the lens set, the
  look's hdri and lighting preset, cast asset and skeleton, location assets, duration
  and continuity state all come from the bible and breakdown. A shot that disagrees
  fails validation.
- Camera moves compile to two helper empties (aim and rig with a track-to constraint)
  with the camera parented for roll. Position is derived at compile time from the
  landmark, so re-blocking a shot moves the camera with it.
- Primitives carry a built-in landmark vocabulary (`top`, `front`, `left`, ...), so a
  blockout can anchor to `@hero_block.top` before any model exists. Landmark empties
  cancel the parent's scale, so anchored children keep their own dimensions and
  offsets are in metres.
- An asset referenced by a landmark but never profiled is an error, not a guess.
- Take decimation is Ramer-Douglas-Peucker over the multi-channel track in
  tolerance-normalised units, plus an 8-frame minimum spacing.
- Eval scoring is three deterministic metrics against human-accepted references
  (pixel, luminance, structure). It detects drift; it does not judge quality.
