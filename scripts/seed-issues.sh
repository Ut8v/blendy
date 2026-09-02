#!/usr/bin/env bash
# Create the backlog as GitHub issues. Run once, after the repo has a remote:
#   gh auth login && scripts/seed-issues.sh
set -euo pipefail
command -v gh >/dev/null || { echo "needs the GitHub CLI: https://cli.github.com"; exit 1; }

label() { gh label create "$1" --color "$2" --description "$3" 2>/dev/null || true; }
label builder    0B5394 "A new shape or capability in the compiler"
label agent      1F883D "Agent behavior, skills or prompting"
label quality    8C4A2F "Output does not read the way it should"
label unverified 7A6E5D "Written but never run against Blender"
label studio     6F42C1 "The local UI"

new() { gh issue create --title "$1" --label "$2" --body "$3" >/dev/null && echo "  + $1"; }

echo "Creating issues…"

new "Rigging: generate a Rigify rig from model landmarks" "builder" \
'Characters cannot be posed. The model builder publishes a full landmark vocabulary; the rigging stage should place a Rigify metarig from it, generate, and bind with automatic weights.

- Place the metarig from `hip`, `chin`, `head_top`, `shoulder_L/R`, `hand_L/R`, `foot_L/R`.
- Generate and bind; store the bone mapping in the model profile.
- A rig that deforms badly is a modeling defect, fixed in the recipe, never by weight painting.
- Headless test: the rig exists, every core landmark resolves to a bone, and a bent limb does not collapse.'

new "Posing: a pose library and procedural locomotion" "builder" \
'Performance is authored, not retargeted from downloaded clips.

- Named poses in `/poses` as Rigify control transforms, placed on frames with ease.
- Procedures generated from parameters: walk, idle, turn, look_at, reach. Deterministic given a seed.
- Additive layers on the NLA for lip sync and small adjustments.'

new "Review agent scores turntables against the reference" "agent" \
'The review agent sees previews and a brief but has no notion of a reference image, so nothing catches likeness drift automatically.

- Score model turntables against the reference and file structured incidents.
- Score rigs against deform tests.
- This is what starts filling the learned section of the modeling skill.'

new "Hair reads as ribbons rather than strands" "quality" \
'Clumping at 0.72 pulls roughly fourteen strands into each cluster, which weld into flat sheets at close range. Seven sides made each strand round but did not fix the clumping.

Wants convergence by the modeling agent: more strands, finer, less clumped, judged on the head close-up rather than the front view. Parameter work, not builder work.'

new "Face detail is too subtle to read" "quality" \
'The head builder now carries forehead lines, glabella furrows, nasolabial folds and crow'"'"'s feet under `age`, but they barely register at body distance and read as smooth skin.

Either the strengths are too low or the falloff radii are too wide. Compare a bare head at `age` 0 and 1.5 to calibrate.'

new "Armor detail: rivets, straps and lamellar rows" "builder" \
'Surface detail is procedural noise only, so plate rows and rivets are implied rather than modeled. A row builder that instances a small form along a path would cover most cases and is cheap.'

new "Ingest pipeline has never been run" "unverified" \
'`compiler/ingest.py` implements normalize, measure, classify, six orthographic views, socket raycasting and validation, and `server/ingest_driver.py` drives it. None of it has executed against a real asset.

Needs one end-to-end run with a local mesh, then a headless test.'

new "Render queue and final frame rendering unverified at scale" "unverified" \
'Per-shot state tracking, parallel dispatch and resume are implemented and unit tested, but only ever exercised on two frames. Needs a real multi-shot run to confirm resume and stale-frame handling.'

new "Director mode has not been driven end to end" "unverified" \
'Proxy export, the browser viewport, take recording, decimation and promotion all have tests, but nobody has recorded a take by hand and applied it to a shot.'

new "Dialogue and lip sync need a Rhubarb run" "unverified" \
'Audio staging, phoneme extraction and viseme keying are written against Rhubarb'"'"'s JSON output but have never run with Rhubarb installed.'

new "Establish the eval baseline" "agent" \
'`/evals` has a scoring harness and a runner but only one frozen shot and no accepted references. The orchestrator must not propose skill edits before this exists, since the eval gate is the only external ground truth.

- Ten frozen shots.
- Accepted reference renders via `python -m evals.run --accept`.'

new "Studio: warn when a model exceeds its poly budget" "studio" \
'A hair parameter change took one model from 60k to 175k polygons with no warning until the render crawled. The models tab should show the poly count against a budget and flag a large jump between builds.'

echo "Done."
