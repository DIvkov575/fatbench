# FatBench: Low-Level Design (MVP)

## Scope

One genuinely fat-context problem, sourced from a **real merged PR** in a **real large OSS repo**. Two runs (baseline vs harness). Does the benchmark produce discriminating scores, and is the task large enough to create real retrieval pressure?

- **1 repo** (real OSS, 150K+ lines Python)
- **1 task** (reconstructed from a real multi-file feature PR, 15-30 files touched)
- **2 runs** (baseline, full-harness)
- **Gold patch = the real merged diff. Gate assertions = the tests added in that PR.**
- **Total cost:** ~$40-80

---

## 1. Core Methodology Shift

**Do not generate a repo. Do not plant bugs. Use reality.**

SWE-bench's insight is that real GitHub issues + their test suites are a self-validating oracle: a patch is correct iff the issue's tests pass. FatBench applies the same insight, but deliberately selects the **large multi-file feature PRs that SWE-bench filters out**, on much larger repos.

### Why this is strictly better than the synthetic approach

| Dimension | Synthetic 25K repo | Real large OSS + real PR |
|-----------|-------------------|--------------------------|
| Retrieval pressure | None (fits in context 3x) | Real (can't read it all) |
| Gold patch | Hand-authored, arguable | The actual merged code |
| Gate assertions | Hand-written, may miss cases | Tests the real engineers wrote |
| Realism | Reads as LLM-generated | Is real production code |
| Construction effort | Weeks | Hours (find PR, checkout parent) |
| Invariant density | Artificially planted | Organically present |
| Contamination | Zero, but unrealistic | Manageable (pick post-cutoff PRs) |

### The reconstruction recipe

1. Pick a large OSS repo with strong CI/test coverage.
2. Find a merged PR that:
   - Touched 15-30 files (the fat-context signal)
   - Added or modified tests (these become the gates)
   - Implements a coherent feature (describable as a task prompt)
   - Merged **after the model's training cutoff** (contamination control)
3. Checkout the PR's **parent commit** (the "before" state).
4. The PR description (cleaned of solution hints) → the task prompt.
5. The PR's non-test diff → the gold patch (for completeness/precision scoring).
6. The PR's test diff → applied to the before-state as the gate assertions.
7. Agent works on the before-state; we run the PR's tests against its solution.

---

## 2. Repo Selection

### Requirements

- **Size:** 150K+ lines so the codebase cannot be fully loaded (forces triage).
- **Language:** Python for MVP (high model fluency — isolates context capability from language capability).
- **Architecture:** Monolithic enough that features ripple across modules. **Avoid plugin-per-folder architectures** (e.g., Home Assistant integrations) — those are low-scatter; a feature lives in one isolated folder, defeating the purpose.
- **Test culture:** PRs routinely include tests, and tests are runnable in a container.
- **Active:** Recent large PRs available post-cutoff.

### Shortlist

| Repo | ~Lines | Architecture | Test culture | Fit |
|------|--------|-------------|-------------|-----|
| **Zulip** | ~400K Python | Monolithic Django, deeply interconnected | Excellent — PRs require tests | **Top pick** — high scatter, real cross-cutting features |
| **Apache Airflow** | ~200K Python | Core + providers; core changes ripple widely | Strong | Strong — use core-touching PRs, not provider-only |
| **Sentry** | ~400K (mixed) | Monolithic backend | Strong | Good but Python/TS mixed — defer to Phase 2 |
| **Superset** | ~300K (mixed) | Flask backend + React | Good | Mixed-language — defer |
| **dbt-core** | ~120K Python | Modular but coupled compiler/runtime | Strong | Borderline size; good coupling |

**MVP choice: Zulip.** Reasons:
- Large, monolithic Django — features genuinely scatter across models, views, actions, event system, frontend bridge, and tests.
- Famously rigorous test requirements — almost every feature PR adds backend tests.
- Cross-cutting concerns everywhere (real-time events, multi-realm tenancy, permissions) — exactly the invariant-propagation profile we want for a T1 task.
- Pure-Python backend testable in isolation.

**Step 1 of the build is to verify actual line counts and pick the exact PR** — numbers above are approximate.

---

## 3. Task Selection Criteria

We're looking for a PR that is a **T1 (distributed invariant propagation)** at hard tier. Concretely, find a merged Zulip feature PR where:

- Files touched: 15-30 (excluding pure docs/translations)
- Spans: model + migration + action/business-logic + view/endpoint + event system + tests (the classic Zulip "feature touches everything" shape)
- Has a clear feature description that can be reworded into a task without naming the solution
- The added tests are specific enough to serve as correctness gates
- Merged after the eval model's training cutoff

### Example shape (illustrative — actual PR TBD in build step)

A feature like "add a per-realm setting to restrict who can create public streams" in Zulip typically touches:
- `zerver/models.py` (or `models/realm.py`) — the setting field
- `zerver/migrations/NNNN_*.py` — migration
- `zerver/actions/*.py` — the action that mutates state + sends events
- `zerver/lib/events.py` — register the setting in the event/state system
- `zerver/views/*.py` — the endpoint
- `zerver/lib/streams.py` — the permission check enforcement
- `zerver/tests/test_*.py` — multiple test files
- API documentation files
- Frontend bridge (`web/src/...`) — for MVP we scope to backend-only PRs or strip the frontend portion

That's 15-25 files for one "small-sounding" setting — because the invariant (this permission) must be enforced consistently across mutation, event propagation, API, and tests. **That is the fat-context problem.** An agent that edits the model and the view but misses the event-system registration produces a patch that passes a naive test but fails the real event-consistency test.

---

## 4. Task Definition Format

```yaml
id: zulip-001
source_pr: "https://github.com/zulip/zulip/pull/NNNNN"
parent_commit: "<sha of PR base>"
repo: zulip
language: python
type: T1
tier: hard

description: |
  <PR title + body, rewritten to describe the desired behavior WITHOUT
   naming files, functions, or the implementation approach. The agent must
   discover the "how" by reading the codebase.>

# Derived automatically from the real PR:
gold_patch_files: [...]          # non-test files in the PR diff
gold_test_files: [...]           # test files in the PR diff (become gates)
parent_commit: <sha>

# Scoring
gate_tests:                      # the actual tests the PR added
  - "zerver/tests/test_X.py::TestClass::test_the_feature"
  - "zerver/tests/test_X.py::TestClass::test_event_propagation"
  - "zerver/tests/test_Y.py::TestClass::test_permission_enforced"
regression_command: |
  ./tools/test-backend zerver/tests/  # or scoped subset for speed

token_budget: 1_200_000
wall_clock_cap_seconds: 2700

# Documented at selection time, not shown to agent:
minimum_reading_set: [...]       # files a human needed to solve it
red_herrings: [...]              # similarly-named files that mislead
```

The key elegance: `gold_patch_files`, `gold_test_files`, and `gate_tests` are all **extracted from the real PR diff** — no hand-authoring of correctness criteria.

---

## 5. Scoring

Same multi-axis model, but assertions come from the real PR's tests:

```
completeness = |agent_files ∩ gold_patch_files| / |gold_patch_files|
precision    = |agent_files ∩ gold_patch_files| / |agent_files|
correctness  = (all gate_tests pass) ? (fraction of gate_tests passing) : 0
                # gates = the tests the PR added; ALL must pass for nonzero
regression   = (pre-existing test suite still passes) ? 1 : 0
efficiency   = composite / tokens_consumed
```

**Gate logic:** The tests added in the PR are the gates. If the agent's solution fails any of the feature's own tests, it didn't implement the feature correctly → correctness = 0. This is exactly SWE-bench's pass criterion, applied to a fat task.

**Why completeness/precision still matter beyond pass/fail:** Two agents can both fail the gates, but one touched 18/22 right files (close) and the other touched 3 (lost). The file metrics show *how* the agent failed — partial understanding vs. total miss. This is the diagnostic signal SWE-bench throws away.

---

## 6. Harness

### Containerization (now required)

Unlike the synthetic-repo plan, real OSS repos need their full dependency stack. Zulip has a documented dev-container setup. The harness:

1. Builds (or pulls) a Docker image with Zulip's dependencies at the parent commit.
2. Mounts a fresh checkout of the parent commit.
3. Optionally writes CLAUDE.md (config-dependent).
4. Runs the agent inside (or against) the container.
5. Applies the agent's diff, runs gate tests + regression subset.

```python
# harness/run.py (MVP, single file)

def run(config_name: str) -> Result:
    workspace = checkout_parent_commit(TASK.repo, TASK.parent_commit)
    git_init_snapshot(workspace)              # baseline commit for diffing

    if config := load_config(config_name):
        if config.claude_md:
            write_file(workspace / "CLAUDE.md", config.claude_md)

    diff, files, usage, wall = invoke_claude_code(
        workspace, TASK.description,
        timeout=TASK.wall_clock_cap_seconds,
    )

    # Score inside the container
    apply_diff(workspace, diff)
    overlay_pr_tests(workspace, TASK.gold_test_files)   # ensure gate tests present
    gates = run_tests(workspace, TASK.gate_tests)
    regression = run_tests(workspace, TASK.regression_command)

    return Result(
        config=config_name,
        completeness=overlap(files, TASK.gold_patch_files),
        precision=precision(files, TASK.gold_patch_files),
        correctness=score_gates(gates),
        regression=1.0 if regression.ok else 0.0,
        input_tokens=usage.input, output_tokens=usage.output,
        wall_clock=wall, cost=cost(usage),
    )
```

Note `overlay_pr_tests`: the gate tests come from the PR, not from the agent. We apply the agent's *implementation* diff, then drop in the *real* test files, then run them. This prevents the agent from "passing" by writing weak tests — it's judged against the engineers' tests.

---

## 7. Configs

### `baseline`
No CLAUDE.md. Agent gets the task prompt + raw repo checkout. Pure model + repo.

### `full-harness`
A CLAUDE.md authored as a real Zulip contributor's onboarding map would be:
- Build/test commands (`./tools/test-backend`, how to run a single test)
- Architecture: realms (tenancy), the actions→events→state-sync pattern, where permissions are enforced
- The "a feature touches everything" checklist Zulip itself documents for contributors
- Pointers: "new realm setting → see how `<existing setting>` flows through models, actions, events, views, tests"
- Gotchas: event-system registration is mandatory and easy to miss

This mirrors what a senior engineer would tell a new hire. The question: does encoding that institutional knowledge measurably improve the agent's solution?

---

## 8. What the MVP Proves

The MVP validates the **instrument**, not a hypothesis:

1. **Discrimination:** Do the scores spread out, or does everything land at 0.5? A useful benchmark separates good from bad solutions.
2. **Retrieval pressure is real:** On a 150K-line repo, does the agent actually have to triage? (Measure: does it read a small fraction of files, and does missing the right ones cause gate failures?)
3. **Mechanical soundness:** Runs end-to-end in a container, reproducible scores, real PR tests execute correctly.
4. **Gate validity:** Does the gold patch (real merged code) pass all gates, and does an obviously-incomplete patch fail them?

Once the instrument is calibrated, it's used to measure real questions: which plugins matter, what parts of CLAUDE.md earn their tokens, Opus vs Sonnet on fat problems, whether memory/hooks/MCP move the needle.

The baseline-vs-harness comparison in the MVP is the **first measurement taken with the calibrated instrument** — not the thing being proven.

---

## 9. Build Sequence

| Step | Work | Time | Output |
|------|------|------|--------|
| 1 | Clone Zulip, verify size, set up dev container, get test suite running | 1-2 days | Working containerized Zulip at a recent commit |
| 2 | Find the PR: search merged feature PRs touching 15-30 files w/ tests, post-cutoff | 1 day | Selected PR + parent commit SHA |
| 3 | Reconstruct task: checkout parent, write task prompt (strip solution hints), extract gold files + gate tests from diff | 0.5 day | `tasks/zulip-001.yaml` |
| 4 | Validate: apply real PR diff to parent, confirm gate tests pass; confirm they fail on unpatched parent | 0.5 day | Verified oracle |
| 5 | Write harness (`run.py`, container glue, configs) | 1-2 days | Working CLI |
| 6 | Calibration run (baseline, no CLAUDE.md): measure token consumption, where it gets stuck | 0.5 day | Baseline result + token profile |
| 7 | Harness run + compare | 0.5 day | Results JSON + analysis |

**Total: ~5-7 days.** The bottleneck moved from "build a repo" (weeks) to "get a big OSS repo's test suite running in a container" (1-2 days) — a known, solvable problem.

---

## 10. Risks Specific to This Approach

| Risk | Mitigation |
|------|-----------|
| Zulip test suite is hard to containerize | It has documented dev-container + CI; fall back to dbt-core (simpler deps) if it fights us |
| Selected PR's frontend portion muddies a backend task | Scope to backend-only PRs, or strip the `web/` diff and only gate on backend tests |
| Contamination (model saw the PR) | Pick PRs merged after the model's training cutoff; record merge date in task meta |
| Task prompt leaks the solution | Rewrite PR body to describe behavior/outcome only; have a second pass strip any file/function names |
| Gate tests depend on frontend or external services | Select PRs whose tests are pure backend; verify in Step 4 |
| One PR isn't representative | MVP is one task by design — it proves the instrument; Phase 1 adds breadth |

---

## 11. Exit Criteria

MVP is done when:
1. Zulip (or fallback) runs in a container with its backend test suite passing at the parent commit.
2. The selected PR's tests pass with the real diff applied, fail without it.
3. Harness runs both configs end-to-end without manual intervention.
4. Scores are computed, compared, and reproducible (≤10% variance on a repeat run).

MVP succeeds if scores discriminate (the instrument works) — regardless of which config wins. A null result (harness doesn't help) is a valid, informative measurement, not a failure of the benchmark.
