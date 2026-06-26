# FatBench Build Status

## Where we are

Building the MVP per `LLD.md`: one fat-context task reconstructed from a real Zulip PR.

### Done
- **Step 1 (repo):** Cloned Zulip (blobless) → `repos/zulip`. Verified scale: **416K LOC Python total, 345K in `zerver/`**. Far exceeds the 150K threshold → full context load is infeasible → real retrieval pressure. ✅
- **Step 2 (PR selection):** Mined git history (88 migration-bearing commits post-cutoff) and selected:
  - **Task `zulip-001`: "Add `topics_policy` realm setting"**
  - Real PR: **#34897** (tracking issue #33549), merged **2025-06-17** (post May-2025 cutoff → contamination-controlled)
  - Source commit `62c67eae8e`, parent/base `8fb1eeeb09`
  - 38 files: 12 backend impl + 4 migrations + 5 backend test files + frontend/api-docs (stripped — backend-only task)
  - Classic T1 distributed-invariant: model → migration ×4 → actions (send/edit enforcement) → event system (schema/types/events) → view/API → tests. Also deprecates `mandatory_topics` (convention-inference dimension).
- **Step 3 (task def):** Written → `tasks/zulip-001.yaml`. Gold diffs split + saved:
  - `tasks/zulip-001.gold-backend.diff` (405 lines — the reference solution)
  - `tasks/zulip-001.gold-tests.diff` (167 lines — becomes the gates)
  - `tasks/zulip-001.frontend.diff` (465 lines — excluded from task)

### Key methodology decision (locked)
Gold tests are partly *modifications* of existing tests that switch from the old
`mandatory_topics` bool API to `topics_policy` + `RealmTopicsPolicyEnum`. Since gold
tests pin the gold implementation's public names, **the task prompt specifies the
setting name and enum members**. The skill measured is *propagation across layers*,
not *API invention*. Missing the event-system registration fails `test_events.py`
even with a correct model+view → the partial-knowledge penalty we want.

### Done (cont.)
- **Step 5 (harness):** BUILT. `harness/` is a stdlib+PyYAML package (venv at `.venv/`),
  spec at `docs/superpowers/specs/2026-06-25-fatbench-harness-design.md`. Modules:
  `task.py`, `config.py`, `diffutil.py`, `workspace.py`, `agent.py`, `evaluator.py`,
  `scorer.py`, `run.py`. Configs: `configs/baseline.yaml`, `configs/full-harness.yaml`
  (+ `full-harness.CLAUDE.md`, a Zulip onboarding map that does NOT leak the task's
  setting name). **25 unit tests pass.** Verified end-to-end on this host:
  - `--dry-run --no-tests` scores the gold backend diff at completeness=1.0/precision=1.0,
    migration-aware matching works, results written to `results/{run_id}/{task_id}/`.
  - Real integration: gold-backend diff AND gold-tests overlay both `git apply` cleanly
    onto a fresh parent-commit checkout (the exact sequence the evaluator will run).
  - Vendored `repos/zulip` stays untouched (clone-to-tempdir isolation confirmed).

  Run it:
  ```bash
  .venv/bin/python -m pytest harness/tests/ -q
  .venv/bin/python -m harness.run --task tasks/zulip-001.yaml --config configs/baseline.yaml --no-tests
  ```

## Next steps

- **Step 4 (validate oracle) — STILL BLOCKED on container runtime.** When a Linux/container
  env exists, run `harness.run` WITHOUT `--no-tests` so `ContainerEvaluator` provisions
  Zulip and runs the gates. Confirm:
  1. Gold backend diff + gold test diff applied → gate tests PASS (use `--dry-run` for this).
  2. Gold tests applied to UNpatched parent → gate tests FAIL (proves they gate the feature).
  Finalize exact `gate_tests` node ids (some are still module-level, not single tests).
- **UNVALIDATED CODE:** `ContainerEvaluator` + `parse_test_backend_output` are written to
  documented test-backend behavior but never run against a real container. Validate before
  trusting correctness/regression scores.
- **Step 6 (calibration run):** baseline (no CLAUDE.md) — measure token consumption,
  where it gets stuck. (`claude -p` invocation path is built but not yet run on a full task.)
- **Step 7 (compare):** baseline vs full-harness; results JSON + analysis.

## Risks live right now
- **Zulip test env is heavy** (Postgres, Redis, RabbitMQ, provisioning). Step 4 is the
  real gate on this whole MVP. If provisioning fights us → fallback repo `dbt-core`
  (simpler deps) noted in LLD §10. Decide within Step 4, don't sink >1 day.

## BLOCKER (2026-06-23, Step 4)
- **No container runtime on this Mac.** `docker` 29.5.2 is just the Homebrew CLI —
  no Docker Desktop (`/Applications/Docker.app` absent), no daemon, no colima/podman.
  Earlier "pull succeeded" was a misread; daemon was never reachable.
- Zulip backend tests need Linux (PG+Redis+RabbitMQ+memcached+venv); no native macOS support.
- Host is **arm64**; `zulip/ci:bookworm` is **amd64-only** → will run emulated (slow but works).
- **Action in progress:** `brew install colima docker` (bg `bxoa8y2rq`). Then:
  `colima start --arch x86_64 --memory 8 --cpu 4 --disk 60` (x86 emulation for the amd64 image),
  then run CI image → `tools/ci/setup-backend` → `test-backend`.
- **Decision point:** if colima + emulated provision is too slow/flaky, options are
  (a) move this whole build to a Linux/cloud-desktop host, or (b) keep authoring on
  macOS and run the actual eval on Linux later. Containerization is an environment
  problem, not a design problem — the task selection + oracle design stand regardless.

## Key paths
- Repo: `/Users/divkov/workplace/fatbench/repos/zulip` (HEAD at clone; checkout `8fb1eeeb09` for task)
- Task: `/Users/divkov/workplace/fatbench/tasks/zulip-001.yaml`
- Design: `HLD.md`, `LLD.md`
