## What this changes

<!-- One or two sentences. What is different afterwards, and why. -->

## How it was checked

<!-- Which suites you ran. If the change touches the compiler or a builder,
     say whether `tests/run_all.sh --blender` passed, and against which
     Blender version. Renders are welcome. -->

- [ ] `tests/run_all.sh` passes
- [ ] `tests/run_all.sh --blender` passes, or this change cannot affect the compiler

## The rules this repository enforces

CI checks these, so an unticked box will fail rather than start an argument.
They are listed here so the reasoning is visible, not to make you promise.

- [ ] No raw `bpy` outside `compiler/` — the only mutation path is editing a
      document and recompiling. No tool that executes arbitrary code.
- [ ] `compiler/validate*.py` still imports without Blender.
- [ ] No reference images, `.blend` files, caches, database or local settings.
      Reference images belong to whoever made them and stay out of the tree.
- [ ] No credentials, tokens or absolute home paths.
- [ ] Nothing above a skill file's `<!-- LEARNED -->` marker is modified.
- [ ] No source file over 500 lines. Split it instead.
- [ ] Determinism holds: same document in, same scene out. Randomness needs a
      seed field in the document.

## Anything unresolved

<!-- Known gaps, things you were unsure about, decisions worth a second
     opinion. This section being non-empty is normal and useful. -->
