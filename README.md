# Blendy

**An experiment: can an agent make 3D animation, if you build a strict enough
harness around it?**

A language model cannot see what it makes, cannot sculpt, and writes 3D code that
fails quietly. This is an attempt to find out how far it gets anyway when
everything it does has to go through a document, a validator and a render it is
made to look at. Agents model, rig, texture, animate, light and shoot in Blender;
a person directs. The only software involved is Blender and the add-ons that ship
with it, and the only outside input is a reference image.

> **Work in progress, and nowhere near a conclusion.** Characters come out
> stylized rather than convincing. Rigging and posing are not built. Several
> layers have only ever run in their own tests. Nothing here claims the question
> is answered.

**[Read the overview →](https://ut8v.github.io/blendy/)**

The scene is a document, not a sequence of commands. Agents edit declarative JSON
— a shot, a model recipe, a pose — and a deterministic compiler turns it into a
Blender scene. Nothing else writes to Blender. There is no code-execution tool,
because unconstrained `bpy` generation fails: the model cannot see what it made,
errors compound silently, and there is no way back to the last good state.

Agents run as Claude Code subagents against the MCP server in `.mcp.json`.

## Requirements

- Blender 5.1 or later. Bundled Python is 3.13; `.python-version` pins it.
- Python 3.13 for the server side.
- A Claude Code CLI signed in, for the agent side. No API key is used, and
  `ANTHROPIC_API_KEY` is stripped from the child environment.
- Optional: Rhubarb Lip Sync for dialogue, via `RHUBARB=/path/to/rhubarb`.

## Setup

```sh
python3 -m venv .venv
./.venv/bin/pip install fastjsonschema mcp numpy

tests/run_all.sh              # unit tests, no Blender
tests/run_all.sh --blender    # plus the compiler and model suites
```

Claude Code picks up `.mcp.json` and `.claude/agents/*` when the directory is
opened.

## The studio

```sh
./.venv/bin/python -m server.studio     # http://127.0.0.1:8765
```

One browser window: a conversation on the left with every tool call and preview
inline, previews / models / director / renders / activity tabs in the middle, and
the shot or model being inspected on the right. Long runs survive a page reload,
and anything that shells out to Blender runs as a watched background job.

## What is built

| Layer | Where |
|---|---|
| Shot, model, bible and breakdown schemas with validators | `spec/`, `compiler/validate*.py` |
| Compiler: fixed step order, resolvers, NLA, lip sync | `compiler/build.py`, `compiler/resolvers/` |
| Modeling: recipes, builders, procedural materials, turntables | `spec/model.schema.json`, `compiler/modeling/` |
| Previews, final frames, proxy export, ingest | `compiler/{preview,render,proxy,ingest}.py` |
| MCP server, 62 typed tools | `server/mcp_server.py`, `server/tools/` |
| JSON Patch by id, write lock, checkpoints, SQLite | `server/{patch,session,db}.py` |
| Sequence layer: continuity boundaries, shot inheritance | `compiler/validate_sequence.py` |
| Director mode: takes, decimation, camera presets | `server/director.py`, `director/web/` |
| Render queue, evals, incidents, skills, orchestrator | `server/`, `evals/` |

Rigging, posing and the reference-aware reviewer are not built yet. Open issues
track the rest.

## Builders

Agents compose from a fixed vocabulary; they cannot invent geometry.

`loft` sweeps cross-sections along a path into quad topology, for torsos, limbs
and boots. `head` and `hand` are parametric anatomy that publish named points, so
a recipe places an eye at `point:eye_l` rather than guessing a coordinate.
`sheet` makes cloth that starts wrapped around the body and falls under
simulation. `hair` grows tapered strands off an emitter surface. `push` is the
one sculpting primitive. `revolve`, `extrude`, `tube`, `metaball` and `skin`
cover turned forms, plates, ropes and rough volumes.

Adding a builder is the normal way to extend the system. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Design notes

- Ids are lowercase snake_case, unique across a document. JSON-Patch paths address
  array items as `/assets/id=hero/...`, because the agent edits by id and an index
  is not a stable handle.
- Anywhere a position is accepted, `@hero.eye_midpoint` is too, resolved at compile
  time after rigs bind. A camera move written against landmarks survives
  re-blocking and recasting.
- Landmark empties cancel their parent's scale, so an anchored child keeps its own
  dimensions and offsets stay in meters.
- Inheritance is enforced: frame rate, resolution, engine, lens set, the look's
  hdri and lighting preset, casting and continuity all come from the bible and
  breakdown. A shot that disagrees fails validation.
- A reference to an asset that was never profiled is an error, not a guess.
- Take decimation is Ramer–Douglas–Peucker over the multi-channel track in
  tolerance-normalized units, with a minimum key spacing.
- Eval scoring is three deterministic metrics against human-accepted references.
  It detects drift; it does not judge quality.

## Reference images

Reference images are the author's own input and are not redistributed. Point a
model recipe's `reference` field at your own file under `assets/references/`,
which is gitignored.

## License

MIT. See [LICENSE](LICENSE).
