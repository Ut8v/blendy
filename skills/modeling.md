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
- `skin` is the workhorse for anything organic: a joint graph with radii. Bodies are one
  skin part from hips to head with limbs branching; fingers are their own small skins.
  Use `[rx, ry]` radii for flattened sections (chest, hands). Subdivision 2 is the default.
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

