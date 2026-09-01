# Evals

The only external ground truth in the system. Frozen: the orchestrator cannot add,
edit or remove anything here.

- `shots/` frozen shot specs. Ten is enough to start. Add by hand, deliberately.
- `references/<shot>/<angle>.png` accepted renders. Create with `python -m evals.run --accept`
  once you have looked at the previews and are happy with them.
- `results/<run>/` every run, with its previews, scores and the skill diff that produced it.

A proposed skill edit lands only if a full run shows no regression on any shot, any metric.
