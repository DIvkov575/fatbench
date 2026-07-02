# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FatBench is a **benchmark for context-demanding coding tasks** — problems where a correct
solution requires reading and coordinating edits across 15-40 files in a large repo, so that
partial context produces confident-but-wrong answers. It deliberately targets the large
multi-file feature PRs that SWE-bench filters out.

The methodology (do not re-derive it — it is the core design): **don't synthesize repos or plant
bugs. Reconstruct tasks from real merged PRs.** For each task: pick a real PR on a 150K+ line OSS
repo, check out its **parent commit** (the "before" state), turn the PR body into the task prompt
(stripped of solution hints), use the PR's non-test diff as the gold patch and its test diff as
the correctness gates. A solution is correct iff the PR's own tests pass — SWE-bench's
self-validating oracle, applied to fat tasks. See `LLD.md` §1.

## Repository layout — two distinct layers

This repo mixes the **instrument** with the **system under test**. Keep them separate:

- **Design + harness (the instrument we are building):** `HLD.md`, `LLD.md`, `STATUS.md`, `tasks/`,
  and the not-yet-written `harness/`, `configs/`, `analysis/`, `results/`. This is the actual
  FatBench project.
- **`repos/zulip/` (the system under test):** a vendored clone of Zulip, its **own git repo**,
  pinned at parent commit `8fb1eeeb09`. ~416K LOC Python (345K in `zerver/`). This is the
  codebase a benchmarked agent solves the task against — **not** FatBench's own source. Treat it
  as a fixture.

Read `STATUS.md` first in any session — it is the live build log (current step, blockers, key
paths) and is updated as work progresses. `HLD.md` is the full design vision; `LLD.md` is the MVP
spec actually being built.

## Critical invariants — do not break these

- **The FatBench root IS a git repository** (the instrument's source — design docs, `harness/`,
  `tasks/`, `configs/`). `repos/zulip/` is **excluded via `.gitignore`** because it is its own git
  repo (the vendored Zulip fixture) — never embed or commit it here. `.venv/`, `results/`, and
  `.remember/` are also gitignored (regenerable / private). Commit design + harness changes here.
- **`repos/zulip` must stay at the task's parent commit** (`8fb1eeeb09` for `zulip-001`). The whole
  oracle depends on the agent working from the *before* state. Do not pull, rebase, or advance it.
  Do not commit experimental changes into it — apply/discard diffs instead.
- **Never leak the solution into a task prompt.** Task `description:` blocks describe desired
  *behavior/outcome* only — no file names, function names, or implementation approach. The agent
  must discover the "how" by exploring the repo. (Exception, documented per task: when gold tests
  pin public API names, the prompt may specify those names — see the methodology note in
  `tasks/zulip-001.yaml`. The skill measured is propagation across layers, not API invention.)
- **Gates come from the PR, not the agent.** When scoring, apply the agent's *implementation* diff,
  then overlay the *real* PR test files (`*.gold-tests.diff`), then run those tests. This prevents
  an agent from "passing" by writing weak tests.
- **`minimum_reading_set` and `red_herrings` in task YAML are NOT shown to the agent** — they are
  selection-time documentation for validating the task is genuinely fat-context.

## Tasks

`tasks/` holds the benchmark task definitions. For `zulip-001` (the MVP task — add a `topics_policy`
realm setting, reconstructed from Zulip PR #34897):

- `zulip-001.yaml` — task definition (see schema in `LLD.md` §4).
- `zulip-001.gold-backend.diff` — the reference solution (non-test backend files). Used for
  completeness/precision scoring.
- `zulip-001.gold-tests.diff` — the PR's test changes. These become the gates; overlaid onto the
  agent's solution at scoring time.
- `zulip-001.frontend.diff` — the PR's `web/` changes, **excluded** (this is a backend-only task).

Migration filenames are gold-specific; completeness matches on non-migration files + "added ≥1
migration" rather than exact migration names.

## Scoring (multi-axis, from `LLD.md` §5)

```
completeness = |agent_files ∩ gold_patch_files| / |gold_patch_files|
precision    = |agent_files ∩ gold_patch_files| / |agent_files|
correctness  = all gate_tests pass ? (fraction passing) : 0   # gates are hard pass/fail
regression   = pre-existing test suite still passes ? 1 : 0
efficiency   = composite / tokens_consumed
```

File metrics matter even when gates fail: they distinguish "touched 18/22 right files (close)" from
"touched 3 (lost)" — the diagnostic signal SWE-bench discards.

## Running Zulip's tests (the oracle)

Zulip's backend tests are the gates. They need a full Linux service stack (Postgres, Redis,
RabbitMQ, memcached, provisioned venv) and **do not run natively on macOS** — they run inside
Zulip's dev/CI container.

```bash
# Inside the Zulip container, from repos/zulip (venv must be active):
./tools/test-backend                                  # full backend suite
./tools/test-backend zerver.tests.test_realm          # one test MODULE (dotted path)
./tools/test-backend zerver.tests.test_realm.RealmAPITest  # one test CLASS (dotted path)
```

Note: `test-backend` accepts **only MODULE or CLASS** dotted labels — **NOT single methods**
(validated on a real container, 2026-07-01). A method-level label like
`zerver.tests.test_realm.RealmAPITest.test_invalid_topics_policy` is fed raw to `__import__`
and dies with `... is not a package`. Pytest-style node ids
(`zerver/tests/test_realm.py::RealmAPITest::test_x`) appear in task YAML for readability; the
harness's `nodeid_to_dotted` translates them and **collapses any method component to its class**
(the finest granularity test-backend can run — a strict superset of the intended test, still a
valid gate). Pin `gate_tests` at class or module level.

**Oracle validation (LLD Step 4): DONE ✅ (2026-07-01, see STATUS.md).** Confirmed on a real
provisioned Zulip stack on the remote Cloud Desktop: gold-backend + gold-tests → gate classes
PASS (98 tests OK); gold-tests alone on the unpatched parent → FAIL (ImportError on
`RealmTopicsPolicyEnum`). The methodology holds. A reusable provisioned image
`fatbench/zulip-provisioned:zulip-001` is snapshotted on the remote so future runs skip the
~15-min provision.

## Test execution environment (RESOLVED — 2026-07-01)

Tests run on the **remote Amazon Cloud Desktop** in `~/.rbg.conf`
(`dev-dsk-divkov-1b-029561b7.us-east-1.amazon.com`, x86_64, docker daemon up). Authoring stays on
macOS; the eval runs on the remote. The old "no container runtime on this Mac" blocker was purely
a host artifact — the remote runs the amd64 `zulip/ci:bookworm` natively (no emulation). See
STATUS.md for the validated operational recipe and the `fatbench/zulip-provisioned:zulip-001`
snapshot. Two container gotchas: (1) the bind-mount approach fails (host UID ≠ container `github`
uid 1001) — copy the repo *into* the image and `chown -R github`; (2) the bare container has no
init system, so start `postgresql/redis-server/rabbitmq-server/memcached` by hand before tests.
Fallback repo if Zulip's env ever fights us: `dbt-core` (simpler deps), per `LLD.md` §10.

## Harness (to be built — `LLD.md` §6)

`harness/run.py` (single-file MVP) will: check out the parent commit into a fresh workspace,
optionally write a config's `CLAUDE.md`, invoke `claude -p --dangerously-skip-permissions` with the
task prompt, collect the resulting diff + token usage, apply it, overlay the gold test files, run
gates + regression, and emit scores. Configs under `configs/` are A/B variants — at minimum
`baseline` (no CLAUDE.md) vs `full-harness` (a Zulip onboarding CLAUDE.md). The MVP comparison is
the first measurement, not the hypothesis being tested: the MVP validates that the *instrument*
produces discriminating, reproducible scores.

**Adapter constraint:** the agent must get no information it wouldn't have in real use. The task
description is the only input; everything else is discovered by exploring the repo. Hooks/plugins/
CLAUDE.md must load exactly as they would in normal operation.
