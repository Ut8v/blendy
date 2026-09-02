---
name: modeling
description: Building characters, props and sets as model recipes, against a reference image.
---

# Core
<!-- Human-authored. IMMUTABLE. Architecture, contracts, hard constraints. -->

- You build by editing the recipe (`patch_model`) and looking at the turntable
  (`preview_model` then `read_image` on every view, especially `compare.png`). Never
  batch more than a few patches before looking. Never touch anything but the recipe.
- Work in the real order: silhouette first (big masses with `skin` and `primitive`), then
  proportions against the reference, then secondary forms (armour, cloak, hair as `tube`
  bundles), then materials, then detail (bevel, displace, grunge). Detail on a wrong
  silhouette is wasted.
- Pick the builder that matches the form. `loft` is the workhorse for bodies, limbs,
  necks, sleeves and boots: a path of cross-sections with a half-width, half-depth and
  a roundness (1.0 ellipse, 1.3 torso, 1.5 rounded box). It gives clean quads that
  subdivide and deform, which `skin` does not. `head` and `hand` are parametric: tune
  their dials, never rebuild them from blobs. `revolve` for anything turned, `extrude`
  for plates and straps, `tube` for ropes and hair, `metaball` only for fused organic
  masses. `skin` remains useful for quick rough volumes.
- `head` publishes a face vocabulary as points: `eye_l`, `eye_r`, `eye_midpoint`,
  `ear_l/r`, `chin`, `head_top`, `nose_tip`, `mouth`, `jaw_l/r`, `neck`, `brow`. Place
  eyeballs, brows, hair and helmets with `at: {part, point, offset}` rather than by
  guessing coordinates, and anchor landmarks with `point:<name>`.
- Its dials are `brow`, `socket`, `cheek`, `jaw`, `chin`, `temple`, `age`, `ears` and a
  `nose` block whose `length` is the forward projection (~0.028 m), not its height.
  Change `height`, `width` and `depth` first; a wrong skull is not fixable with dials.
- `push` sculpts by number: a centre, a radius and either a direction or a radial swell.
  Feature radii must be small. A brow ridge is a 3 cm form; spread it over 8 cm and it
  disappears back into the skull.
- Symmetry: build the left side and add a part with `mirror_of` for the right. Keep the
  character centred on X, facing -Y, feet at z=0, and `height` set to the intended height.
- Every transform is relative to the parent. Parent limbs, armour and hair to the body
  part they attach to, so moving the body moves them.
- Landmarks are named parts + anchors. A character must declare the core set (`eye_L`,
  `eye_R`, `eye_midpoint`, `ear_L/R`, `head_top`, `chin`, `neck`, `shoulder_L/R`,
  `hand_L/R`, `hip`, `foot_L/R`, `ground_contact`); use `joint:` anchors on the skin
  graph for joints and `center`/`top` on parts like eyes.
- Materials are procedural: base colour, roughness, metallic, then `noise` for variation,
  `grunge` for wear, `scratches` on metal, `bump` for skin and cloth. No textures exist.
- Read `model_profile` after each preview: measured height and poly count. A model
  taller than declared by more than 5% is wrong, not "close". Keep characters under
  ~150k polygons at subdivision 2.
- Checkpoint (`checkpoint_model`) whenever a stage reads right. Restore instead of
  digging out of a bad run of patches.
- A likeness is reached by iteration: compare, name the single biggest difference to the
  reference, fix that, look again. Stop when the human says stop, not when you think it
  is done.

<!-- LEARNED -->
<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->

