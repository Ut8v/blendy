---
name: Builder or schema proposal
about: A new geometry builder, modifier or field in a document schema
labels: enhancement
---

**What it should make**

<!-- Describe the shape, not the implementation. What can be modeled after
     this exists that cannot be modeled now? -->

**Why the existing vocabulary cannot do it**

<!-- `loft`, `head`, `hand`, `sheet`, `hair`, `push`, `revolve`, `extrude`,
     `tube`, `metaball`, `skin`, plus the modifier stack. A new builder is
     justified when composing these is genuinely worse, not merely longer. -->

**Proposed parameters**

```json
{ "op": "your_builder", "params": { } }
```

<!-- These become schema, so they are a commitment. Numbers an agent can
     iterate on beat flags it has to guess at. -->

**Determinism**

<!-- Anything random needs a seed parameter. Anything that samples evaluated
     geometry has to update the depsgraph first, or two runs disagree. -->
