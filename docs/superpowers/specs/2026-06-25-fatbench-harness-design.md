# FatBench Harness — Design Spec

Date: 2026-06-25
Status: approved (verbal), building

## Goal

Build the FatBench measurement harness (`LLD.md` §6): run one coding agent on one
reconstructed-PR task under one config, and emit a multi-axis scorecard. One run =
one (task, config) → scores.

## Pipeline (5 steps)

1. **Set up** — fresh checkout of `repos/zulip` at the task's `parent_commit` into a temp
   workspace. Never mutate the vendored `repos/zulip`.
2. **Ask** — run `claude -p` in the workspace with `task.description` as the only input.
3. **Collect** — agent's diff (vs the checkout baseline) + token usage from the JSON envelope.
4. **Grade** — five axes (below), graded against the real PR as answer key.
5. **Record** — write `results/{run_id}/{task_id}/{patch.diff,transcript.json,scores.json,test_output.log}` + `meta.json`, `summary.json`.

## Oracle (the answer key)

Task comes from a real merged PR. Gold backend diff (non-test files) → which files *should*
change. Gold test diff → the gates. Anti-cheat: apply the agent's **impl** diff, overlay the
**PR's** test files, run those. Agent-written tests are discarded.

## Scoring (`LLD.md` §5)

```
completeness = |agent_files ∩ gold_patch_files| / |gold_patch_files|
precision    = |agent_files ∩ gold_patch_files| / |agent_files|
correctness  = all gate_tests pass ? (fraction passing) : 0    # hard gate
regression   = pre-existing suite still passes ? 1 : 0
efficiency   = composite / tokens_consumed
composite    = 0.35*correctness + 0.25*completeness + 0.15*precision + 0.15*regression + 0.10*efficiency
```

- **Migration-aware matching**: gold has migrations `0710`–`0713` (gold-specific names). Collapse
  all `zerver/migrations/*` gold entries into one synthetic slot, satisfied iff the agent added
  ≥1 file under `zerver/migrations/`. Non-migration gold files match by exact path.
- `agent_files` = agent's **impl** files (test files the agent touched are excluded from the
  file metrics — only the PR's tests gate).

## Test execution seam

The grading step needs Zulip's full backend (Postgres/Redis/RabbitMQ) → Linux container only.
No container runtime exists on this host now. So test execution sits behind:

```python
class Evaluator(ABC):
    def setup(self, workspace) -> None: ...
    def run_tests(self, workspace, test_ids: list[str]) -> TestRunResult: ...   # passed/failed/raw_log/ran
    def teardown(self) -> None: ...
```

- `ContainerEvaluator` — docker/colima: pull Zulip CI image, mount workspace, run
  `./tools/test-backend <dotted.module.path>` (translate pytest node-ids → dotted paths), parse
  summary. Raises a clear, actionable error when no runtime is present. **Built but unvalidated
  until a Linux env exists.**
- `NullEvaluator` — skips tests, returns `ran=False`. Lets the full pipeline + file-metric scores
  run on this host today. Auto-selected when no runtime detected (or `--no-tests`).

## Modules

```
harness/
  requirements.txt   # PyYAML
  task.py            # Task dataclass + YAML loader
  config.py          # configs/*.yaml → {name, claude_md|None}
  diffutil.py        # parse changed paths from git-log/-diff text; split test vs impl; migration detect
  workspace.py       # fresh checkout @ parent_commit; baseline; collect agent diff (excl injected CLAUDE.md)
  agent.py           # invoke claude -p --output-format json; capture diff, tokens, cost, wall-clock
  evaluator.py       # Evaluator ABC + ContainerEvaluator + NullEvaluator
  scorer.py          # 5 axes + composite; migration collapsing; gate gating
  run.py             # orchestrator CLI
  tests/             # pytest for pure-Python stages, fixtures = real *.diff files
configs/
  baseline.yaml      # claude_md: null
  full-harness.yaml  # Zulip onboarding CLAUDE.md
```

## claude -p JSON envelope (probed)

Keys used: `result` (text), `is_error`, `num_turns`, `duration_ms`, `total_cost_usd`,
`usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}`.
`tokens_consumed` for efficiency = input + output (+ cache_creation, as it's billed input).

## Error handling

Agent timeout → kill, score partial (file metrics still computed). Empty/unapplicable diff →
correctness 0, file metrics from whatever applied. No container → EnvironmentError or auto-fallback
to NullEvaluator. test-backend parse failure → surface raw log, don't crash.

## Testing

Unit tests (no container, run today): `diffutil` (path extraction, test/impl split, migration
detect — fixtures are the real `tasks/*.diff`), `scorer` (all axes, migration collapse, gate gating),
`task`/`config` loaders. Container path: test that **skips** when no runtime present.

## Out of scope (YAGNI)

Multi-task batching, statistical comparison across runs (`analysis/`), non-Claude adapters,
parallel runs. MVP is one task, two configs, sequential.
