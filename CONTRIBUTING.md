# Contributing

The architecture here is opinionated, and most of the rules exist because the
obvious alternative was tried and failed. Read this before opening a pull
request; it will save you writing code that gets rejected on principle.

## The rules that are not negotiable

**Agents never write raw `bpy`.** There is no code-execution tool and there will
not be one. An agent's only route into Blender is editing a declarative document
and recompiling. If an agent needs a capability that does not exist, that is a
builder to add, not an escape hatch to open.

**The compiler is deterministic and total.** The same document produces the same
scene, every time. Randomness requires an explicit seed field. A build either
completes or fails with a validation error naming the offending entity; there is
no partial state.

**Validation runs outside Blender.** `compiler/validate*.py` must never import
`bpy`. It runs in CI and in the server process, and it is the compiler's first
step so that a bad document fails before the scene is touched.

**Semantics are resolved once.** Where a character's eyes are, how tall it is,
which way a socket faces: computed at ingest or model build, validated, stored
in a profile. Scene building is lookup only.

## Adding a builder

Builders are the vocabulary of shapes that can exist. Adding one is the normal
way to extend the system.

1. Write the function in `compiler/modeling/`. Signature is
   `(name, params, smooth, objects=None) -> bpy.types.Object`. Build with
   `bmesh` where you can, so no operator context is needed.
2. Register it in `BUILDERS` in `compiler/modeling/builders.py`.
3. Add a `<name>_params` schema to `spec/model.schema.json` with
   `additionalProperties: false`, and map it in `_PARAM_SCHEMAS` in
   `compiler/validate_model.py`.
4. If it publishes named points, list them in `POINT_NAMES` so `point:` anchors
   validate against real names.
5. Write a headless test in `tests/blender/test_model.py` that asserts the
   geometry, not just that it built.

Parameters should be quantities a person can reason about: metres, radians,
counts. A builder whose parameters only make sense by trial and error is a
builder an agent cannot use.

## Tests

```sh
tests/run_all.sh              # unit tests, no Blender
tests/run_all.sh --blender    # plus the compiler and model suites
```

Never mock `bpy`. A mocked test passes while the compiler is broken, which is
worse than no test. Anything touching Blender runs headless against the real
thing.

Every validation rule needs a test that breaks exactly one thing and asserts the
specific code fires against the right entity. Error messages are a user
interface here: when a landmark is missing, the error lists the ones that exist,
because that is how an agent learns an asset's vocabulary.

## Documents are the source of truth

Specs, model recipes, profiles, skills and the bible are files, because you read,
diff and hand-edit them. They are committed. Anything the compiler can rebuild —
`.blend` outputs, renders, previews, the asset cache — is a build artifact and
belongs in `.gitignore`.

## Style

Python 3.13, four spaces, type hints on public functions. Keep files under about
500 lines; when one grows past that, it is usually two concerns.

Comments explain why, not what. `# increment i` is noise; `# a leaf joint extends
past the joint by its radius unless marked loose` is the thing that will save the
next person an hour.

## Reference images

Reference images are the author's own input and are not redistributed. Point a
model recipe's `reference` field at your own file under `assets/references/`,
which is gitignored.
