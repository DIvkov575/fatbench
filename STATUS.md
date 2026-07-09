# FatBench Build Status

## Where we are

MVP complete; now GROWING THE TASK SET. As of 2026-07-08 the benchmark has **3 validated tasks**.

### Platform reframe (2026-07-09)
FatBench is a benchmarking **platform**, not one experiment. It ships tasks + oracle + scorer + a
single built-in control arm: the vanilla `baseline` (no injected context). The thing under test —
a CLAUDE.md / plugins / MCP / hooks — is an **experiment the user brings**, via `--claude-md PATH`
(or a config YAML). Run the baseline by omitting both flags. The old shipped `full-harness` config
was removed; its onboarding CLAUDE.md now lives as a *sample experiment* in
`examples/experiments/zulip-backend-onboarding.CLAUDE.md` (+ README with the "valid experiment doc"
rules). `configs/` now holds only `baseline.yaml`. 46 unit tests pass.

### Task set (all oracle-validated two-sided on the container)
- **zulip-001** — add `topics_policy` realm setting. Archetype: vertical **invariant propagation**
  (model→migrations→actions→events→views). Parent `8fb1eeeb09`.
- **zulip-002** — parallelize export/import (PR #28617). Archetype: horizontal **callsite
  consolidation** refactor (new `lib/parallel.py` threaded through data_import/export/worker/mgmt).
  Parent `454905f988`. 8 gate classes; FAIL-side = `ModuleNotFoundError: zerver.lib.parallel`.
- **zulip-003** — schedule-reminders API (PR #34505). Archetype: diagonal **subsystem reuse**
  (reuse `ScheduledMessage` delivery machinery). Parent `1af039d8` (+1 migration). 2 gate classes;
  FAIL-side = `/json/reminders` 404. `ScheduledMessageTest` is regression-as-gate (passes on parent).
Diversity thesis + full design: `analysis/zulip-002-003-design.md`; writing plan:
`analysis/zulip-002-003-writing-plan.md`. Built by a Scout→Architect→2×Builder→Validator agent team.

### Two harness follow-ups surfaced while building 002/003
1. **FIXED — `author.py` parent-commit bug.** `merge_commit_sha^` is wrong for REBASE-merged PRs
   (returns the PR's own Nth commit). New `_parent_commit` walks N first-parents back from the head
   (`merge_commit_sha ~ commits`); verified against #28617 (N=11) and #34505 (N=2). NOTE: no API-only
   heuristic is safe across all merge modes — the authoritative check stays "does the gold diff apply
   on this parent in a local clone?" (Builders/Validator do this).
2. **FIXED — `ContainerEvaluator` now runs newer-than-snapshot parents end-to-end.** Verified
   2026-07-08 by dry-running zulip-002 through `harness.run --remote-host`: the gold diff scores
   completeness/precision/correctness/regression = 1.0 (composite 0.9). Changes:
   - `stage()` re-syncs the venv to the parent's lockfile (`uv sync --frozen --group dev --inexact`,
     `VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT=/srv/zulip/.venv`); toggle via `sync_venv`.
   - `run_tests`/`run_command` pass/inject `--skip-provision-check`.
   - **Staging transport rewritten (this was the hard bug).** The old `_put` streamed the diff over
     ssh-stdin into `docker exec -i`; the Amazon **WSSH proxy silently truncated** large payloads
     (an 18KB diff cut to ~7.5KB, exit 0), so `git apply` hit a partial/**stale** diff. Worse, the
     snapshot image had zulip-001's `/tmp/gold-tests.diff` baked in (from June-23 validation), which
     got applied instead → the mysterious `test_events.py:4259` error on a zulip-002 run. Fixes:
       • `_put` now `scp`s the diff to the host + `docker cp`s into the container (no stdin stream),
         then **`chmod 644`** (docker cp lands it as host uid 34727450/0600 → the container `github`
         user couldn't read it) and **byte-count verifies**; raises loudly instead of applying junk.
       • `stage()` `rm -f`s `/tmp/*.diff` before writing; the baked-in stale diffs were scrubbed
         from the image and it was re-committed clean.
       • `RemoteContainerEvaluator._host_sh` retries transient WSSH failures (never real errors).
   - Staging files live in `/tmp` (outside `/srv/zulip`), already safe from `git clean`.
   45 unit tests pass.

---

## MVP history (below): one fat-context task reconstructed from a real Zulip PR.

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

### Done (cont.)
- **Step 4 (validate oracle): DONE ✅ (2026-07-01).** Ran on the remote Amazon Cloud Desktop
  `dev-dsk-divkov-1b-029561b7.us-east-1.amazon.com` (x86_64, docker daemon up, 8c/15G) — the
  macOS container blocker was purely a host artifact. Two-sided oracle confirmed against a real,
  fully-provisioned Zulip stack:
  1. **PASS side:** gold-backend + gold-tests applied to parent → gate classes run migrations and
     pass. `Ran 98 tests ... OK` across the 5 gate classes (`RealmAPITest` alone: 20 tests OK).
  2. **FAIL side:** gold-tests alone on the unpatched parent → `ImportError: cannot import name
     'RealmTopicsPolicyEnum'` (collection crash, non-zero exit); class-level run reports
     `FAILED (failures=1, errors=34)`. The tests genuinely gate the feature.
- **`gate_tests` finalized** at CLASS granularity in `tasks/zulip-001.yaml`.
- **ContainerEvaluator / parser validated + bug-fixed:**
  - **Bug found & fixed:** `nodeid_to_dotted` emitted `module.Class.method`, which test-backend
    CANNOT run — its loader `__import__`s the raw string → "is not a package". Only MODULE and
    CLASS labels work. Fixed to collapse any method component to its class. `parse_test_backend_output`
    verified correct against all three real outputs (OK / import-crash / failures+errors).
  - **CLAUDE.md correction needed:** the "Running Zulip's tests" section claims test-backend takes
    `zerver.tests.test_realm.RealmAPITest.test_x` (method dotted). It does NOT — method-level fails.
  - **27 unit tests pass** (was 25; +2 for the real FAIL-side outputs and method-collapse).
- **Phase 2 speedup — provisioned image snapshotted:** `fatbench/zulip-provisioned:zulip-001`
  (5.03GB) on the remote. Future runs skip the ~15-min provision: just `git reset --hard parent →
  apply diff → start services → test-backend`. This is the key throughput lever for scaling tasks.

### Operational recipe (remote, validated)
```bash
HOST=dev-dsk-divkov-1b-029561b7.us-east-1.amazon.com   # from ~/.rbg.conf
# One-time: docker run from the SNAPSHOT (skips provisioning entirely)
docker run -d --name zfb fatbench/zulip-provisioned:zulip-001 sleep infinity
# services have no init system in the bare container — start them by hand each container:
docker exec -u root zfb bash -lc 'for s in postgresql redis-server rabbitmq-server memcached; do service $s start; done'
# per run: reset -> apply agent/gold diff -> overlay gold tests -> run gate CLASSES
docker exec zfb bash -lc 'cd /srv/zulip && git reset --hard 8fb1eeeb09 && git clean -fdq \
  && git apply /tmp/impl.diff && git apply /tmp/gold-tests.diff \
  && source .venv/bin/activate && ./tools/test-backend zerver.tests.test_realm.RealmAPITest ...'
```

### Done (cont.) — 2026-07-06 session
- **Harness wired to the snapshot + validated E2E through the harness code (not manual):**
  - `ContainerEvaluator` now targets `fatbench/zulip-provisioned:zulip-001` (no in-run provision);
    `setup()` starts the 4 services; new `stage()` applies impl diff + overlays gold tests INSIDE
    the container via diffs (tree never overwritten → provisioned `.venv`/`var/` survive);
    `run_tests`/`run_command` activate the venv and read output back from a file.
  - **`RemoteContainerEvaluator`** drives docker over SSH; file writes stream through
    `docker exec -i 'cat > dest'` (no temp files on the remote). `run.py` gained `--remote-host`.
  - **E2E validated** (`python -m harness.run --dry-run --remote-host <host>`):
    PASS side → `correctness=1.0`, **98 gate + 243 regression tests OK**; FAIL side (empty impl +
    gold tests) → `ok=False`. The full setup→stage→run path works over SSH.
- **CLAUDE.md test-backend claim corrected** (method-level labels don't run) — done last session.
- **Task-authoring scaler built: `harness/author.py`** (the stated breadth goal):
  - `discover` — query merged post-cutoff PRs touching N..M files via `gh api`, ranked by fatness.
  - `build` — fetch one PR's metadata + `.diff`, split into backend/test/frontend (+migrations),
    score fatness (file count, dir scatter, has-migration), scan the PR body for solution leakage,
    and emit a review-ready `tasks/<id>.yaml` + split `.diff` files mirroring zulip-001's layout.
  - Emitted YAML carries inline reviewer TODOs (scrub leakage, pin gate_tests at class/module,
    confirm parent). Validated against zulip-001's own PR #34897: correctly reported the FULL PR
    (27 backend files / 7 dirs / migrations → verdict "fat"; zulip-001 hand-narrowed to 13 via a
    later parent) and flagged the `topics_policy`/`mandatory_topics` leakage. Emitted YAML parses.
  - **35 unit tests pass** (was 30; +5 author tests for classify/split/fatness/leakage).

### Done (cont.) — 2026-07-07 session: Step 6 calibration run DONE ✅
First LIVE `claude -p` agent on zulip-001, baseline config (no CLAUDE.md), gates on the remote.
Run dir `results/20260707-121626_zulip-001_baseline/`. ~30 min agent, **297K tokens** (of 1.2M).

- **Result (corrected — see harness bug below): completeness=0.8, precision=0.8, correctness=0.**
  - The agent touched 7/9 gold files but **MISSED `zerver/lib/event_schema.py` +
    `zerver/lib/event_types.py`** (the event-system registration layer) and added 2 extras
    (`api_docs/changelog.md`, `version.py`).
  - Consequently **3 gold gate tests fail** (95/98 pass): `test_events.RealmPropertyActionTest.
    test_change_realm_property` and `test_home.HomeTest.{test_home,test_home_demo_organization}`
    — i.e. the setting doesn't propagate through events / initial client state.
  - This is the textbook fat-context failure: correct model+actions+views, **dropped a layer**,
    confident-but-wrong. File metrics (0.8) still show it was *close* — the signal SWE-bench discards.
- **Instrument validated on all four LLD §8 criteria:** discrimination ✅ (0.8, not 0/1),
  retrieval pressure real ✅ (missing files → gate failures), mechanically sound ✅ (live agent
  end-to-end), gate validity ✅ (gold passes, incomplete fails).

- **HARNESS BUG found & fixed (only a LIVE run could surface it):** the grader staged the agent's
  FULL diff — including its edits to the 5 test files — so the gold-tests overlay hit a patch
  conflict, was skipped, and gates ran against the AGENT'S OWN tests (→ bogus correctness=1.0,
  violating "gates come from the PR, not the agent"). Fix: `diffutil.split_diff_by_role()` splits
  the agent diff into impl vs test; `run.py` now stages IMPL-ONLY then overlays gold tests, and
  writes `patch.impl.diff` + `patch.agent-tests.diff`. 37 unit tests pass (+2). The buggy
  `scores.json` is preserved; `scores.corrected.json` holds the true correctness=0.

### Done (cont.) — 2026-07-07 session: Step 7 comparison DONE ✅ (MVP complete)
Full writeup: `analysis/step7-comparison.md` (+ `.json`). Two sequential live runs with the
FIXED harness (they share the `fatbench-eval` container, so cannot overlap).

| config | complete | precision | correctness | regression | composite | tokens | turns |
|--------|---------:|----------:|------------:|-----------:|----------:|-------:|------:|
| baseline (clean re-run) | 0.80 | 0.80 | 0 | 1.0 | 0.57 | 291K | 126 |
| full-harness | 0.60 | 0.75 | 0 | 1.0 | 0.51 | 192K | 115 |

- **Both correctness=0** — both miss `event_schema.py`+`event_types.py`, so `test_events`/
  `test_home` fail. The onboarding CLAUDE.md (which explicitly warns about forgetting the
  event-system registration) did NOT get the agent to those files.
- **Full-harness scored LOWER on files (0.60 vs 0.80) with ~34% fewer tokens**, ran to
  completion (not truncated). It also dropped the enforcement actions (`message_send/edit`)
  the baseline found. Read: the map made it stop exploring sooner. **N=1 — do not over-read.**
- **Reproducibility confirmed:** two baseline runs → identical 7 matched / 2 missed, 291K vs 297K.
- **File metrics discriminate where correctness can't** (all 0, but 0.8 vs 0.6 pinpoints the
  dropped layer) — the core value prop, demonstrated. MVP goal (LLD §8) met.

## Next steps
- **Grow the task set (the real next phase):** `author.py discover` on Zulip, `build` 2-3
  candidates, review/scrub into real tasks. Each reuses the snapshot. Consider dbt-core later.
- **Strengthen the finding:** multiple runs per config (seeds) + more tasks before trusting the
  "full-harness is worse" signal. Consider an onboarding variant that points at an analogous
  existing setting to mirror, rather than prose warnings.
- **Author.py polish (optional):** `discover` does one `pulls/<n>` call per candidate (rate-OK but
  slowish); could batch. Frontend-prefix list is Zulip-specific — parameterize per repo.

## Risks
- **SSH/WSSH proxy to the Cloud Desktop is flaky** under long-running output (intermittent EPIPE /
  banner timeouts). Mitigation: write test output to a file inside the container and read it back;
  keep individual ssh exec calls short. Not a design risk.

## Key paths
- Local repo: `/Users/divkov/workplace/fatbench/repos/zulip` (checkout `8fb1eeeb09` for task)
- Remote: `/local/home/divkov/fatbench` (instrument), `/local/home/divkov/fatbench/repos/zulip`
  (Zulip @ parent), container `zfb`, image `fatbench/zulip-provisioned:zulip-001`
- Task: `tasks/zulip-001.yaml`  ·  Design: `HLD.md`, `LLD.md`
