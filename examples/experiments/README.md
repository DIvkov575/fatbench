# Example experiments

FatBench is a benchmarking **platform**. It ships tasks, the oracle, the scorer, and one built-in
control arm — the vanilla `baseline` (Claude Code with only the task prompt + the raw repo). The
thing you actually want to measure — a `CLAUDE.md`, a set of plugins/MCP servers, a hook config —
is an **experiment you bring**, not something the platform defines.

Run the vanilla baseline (built-in control):

```bash
python -m harness.run --task tasks/zulip-001.yaml --remote-host <host>
```

Run your experiment against the same task and score it head-to-head:

```bash
python -m harness.run --task tasks/zulip-001.yaml \
    --claude-md examples/experiments/zulip-backend-onboarding.CLAUDE.md \
    --experiment-name onboarding-v1 --remote-host <host>
```

Compare the two result dirs under `results/` — same task, same oracle, only the injected context
differs.

## What's here

- `zulip-backend-onboarding.CLAUDE.md` — a sample experiment: a hand-written Zulip backend
  onboarding doc (how the code is layered; the cross-cutting-setting invariant). It's the arm
  used in the Step 7 write-up (`analysis/step7-comparison.md`), kept as a worked example of a
  legitimate, non-leaking experiment doc. **Not** part of the platform — copy it, edit it, or
  bring your own.

## Rules for a valid experiment doc

- Describe general codebase/workflow knowledge, **not** a specific task's solution. Naming the
  files or symbols a given task must touch is leakage and invalidates that task's measurement.
- Anything a real user would legitimately have in their environment is fair game (onboarding
  conventions, build/test commands, architectural maps, tool configs).
