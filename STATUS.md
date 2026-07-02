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

## Next steps
- **Harness gap to close:** `ContainerEvaluator.setup()` does NOT start the service stack (the
  bare container has no init system). Either add a service-start step to `setup()`, or point the
  evaluator at the provisioned snapshot image + start services. Also switch its default image to
  the snapshot to skip provisioning.
- **CLAUDE.md fix:** correct the test-backend single-test claim (method-level does not run).
- **Step 6 (calibration run):** baseline (no CLAUDE.md) on the remote — measure token
  consumption, where the agent gets stuck. `claude -p` path built, not yet run on a full task.
- **Step 7 (compare):** baseline vs full-harness; results JSON + analysis.
- **Scaling tasks (the stated goal):** with the oracle proven + snapshot ready, build
  `harness/author.py` to mechanize task authoring (mine post-cutoff 15-40-file PRs → auto-split
  test/non-test/frontend diffs → draft prompt). Reuse the same snapshot for every Zulip task.

## Risks
- **SSH/WSSH proxy to the Cloud Desktop is flaky** under long-running output (intermittent EPIPE /
  banner timeouts). Mitigation: write test output to a file inside the container and read it back;
  keep individual ssh exec calls short. Not a design risk.

## Key paths
- Local repo: `/Users/divkov/workplace/fatbench/repos/zulip` (checkout `8fb1eeeb09` for task)
- Remote: `/local/home/divkov/fatbench` (instrument), `/local/home/divkov/fatbench/repos/zulip`
  (Zulip @ parent), container `zfb`, image `fatbench/zulip-provisioned:zulip-001`
- Task: `tasks/zulip-001.yaml`  ·  Design: `HLD.md`, `LLD.md`
