# zulip-002 & zulip-003 — Builder Writing Plan

A step-by-step checklist. Each Builder produces **exactly one** task. Follow it top to bottom; every
design decision is already made in `analysis/zulip-002-003-design.md` (read that first). Where this
plan says "see design doc," the value is there — do not re-derive it.

**Per-task constants (fill in for your task):**

| | zulip-002 | zulip-003 |
|--|-----------|-----------|
| `--pr` | `28617` | `34505` |
| `--id` | `zulip-002` | `zulip-003` |
| merge SHA | `0ee71c0a6ad067571e8a93ff3e09241b65f67926` | `733817cb510fa3a5540161ee9a58365445c3cab4` |
| parent SHA | `c15f426dc6fce3751ab8f131030d51f29affa0f1` | `ad9cb501831949241e7e85416f0ae731bc2f7759` |
| tier | `hard` | `medium` |
| type | `T2` (cross-callsite consolidation refactor) | `T3` (subsystem extension / reuse) |
| API-name exception needed? | YES — `run_parallel`/`run_parallel_queue` in a `zerver/lib/parallel.py` | YES — endpoint `POST /json/reminders`, params, `reminder_id` response |

Working dir: `/Users/divkov/workplace/fatbench/.claude/worktrees/oracle-validated`
Python: `/Users/divkov/workplace/fatbench/.venv/bin/python`

---

## Step 0 — Verify the parent commit (before anything else)

```bash
cd /Users/divkov/workplace/fatbench/repos/zulip
git rev-parse <merge SHA>^        # MUST print the parent SHA from the table above
git cat-file -t <parent SHA>      # MUST print: commit
```
If either fails, STOP and report — the oracle depends on the exact "before" state. Do **not** move
`repos/zulip`'s pin; the harness checks the parent out into a fresh workspace.

## Step 1 — Run the authoring scaler

```bash
cd /Users/divkov/workplace/fatbench/.claude/worktrees/oracle-validated
/Users/divkov/workplace/fatbench/.venv/bin/python -m harness.author build \
    --repo zulip/zulip --pr <N> --id <id> --out tasks/
```

This writes: `tasks/<id>.yaml` (DRAFT), `tasks/<id>.gold-backend.diff`, `tasks/<id>.gold-tests.diff`,
and (if any) `tasks/<id>.frontend.diff`. It prints a JSON blob with `parent_commit`, `fatness`, and
`leakage_flags`. Confirm the printed `parent_commit` equals the table's parent SHA.

## Step 2 — Verify the auto-split diffs are partitioned correctly

The splitter is heuristic (`classify_path`); verify it did the right thing.

```bash
# List what landed in each bucket:
grep -E '^diff --git' tasks/<id>.gold-backend.diff | sed 's#.* b/##'
grep -E '^diff --git' tasks/<id>.gold-tests.diff   | sed 's#.* b/##'
[ -f tasks/<id>.frontend.diff ] && grep -E '^diff --git' tasks/<id>.frontend.diff | sed 's#.* b/##'
```

Check against the expected file lists in the design doc. Specifically:

- **zulip-002:** backend diff must contain exactly the 12 `zerver/{lib,data_import,management/commands,worker}`
  files (including new `zerver/lib/parallel.py`); tests diff must contain the 7 `zerver/tests/test_*.py`
  files (including new `test_parallel.py`); there should be **no** frontend diff. There are no
  migrations and no `.md`/`api_docs` files in this PR.
- **zulip-003:** backend diff must contain the ~10 substantive files + the migration
  `zerver/migrations/0699_scheduledmessage_reminder_target_message_id.py`; tests diff must contain
  `test_reminders.py`, `test_scheduled_messages.py`, `test_openapi.py`; frontend diff must contain
  `web/src/compose_reply.ts`. **Watch for noise:** `version.py` will be classified `backend` and
  `api_docs/*.md` classified `doc` (kept in backend diff but excluded from `gold_patch_files`).
  Confirm `version.py` and the `api_docs/*.md`/`tools/merge-api-changelogs` entries are **not** in
  the final `gold_patch_files` list (they are version-bump/doc noise, not implementation).

If a file is miscategorized, hand-edit the three `.diff` files to move the offending `diff --git`
block to the correct bucket, and adjust the YAML file lists to match.

## Step 3 — Rewrite `description:` into an OUTCOME-ONLY prompt

The draft `description:` is the raw PR body — **discard it** and write fresh. Rules (from CLAUDE.md /
LLD §4): describe desired **behavior/outcome only**. NO file names, NO function names, NO
implementation approach, NO mention of the mechanism.

Use the ready-made outcome spec in the design doc ("Leakage to scrub" section for your task) as the
basis. Then apply the **API-name exception** (both tasks need it — see table + design doc):

- **zulip-002:** you MAY state that the concurrency logic currently duplicated across the
  import/export code paths should be consolidated into a single shared helper exposing
  `run_parallel` and `run_parallel_queue` (the gold test imports these names). You may name the
  module `zerver/lib/parallel.py` since the test imports from it. You may NOT name the individual
  callsites, `ProcessPoolExecutor`, or `_disconnect`. Add a methodology note (mirroring zulip-001's)
  explaining the exception: the skill is consolidation across callsites + child-process DB safety,
  not API invention.
- **zulip-003:** you MAY state the public HTTP surface the test pins: endpoint `POST /json/reminders`,
  taking a target message id and a future delivery timestamp, returning the new reminder's id; and
  that a non-future delivery time must be rejected. You may state that delivery must reuse the
  same mechanism that delivers other future-scheduled messages (this is the *outcome*, and the whole
  point of the task — but do NOT name `ScheduledMessage`/`try_deliver_one_scheduled_message`/files).
  Add the methodology note scoping the exception to the public endpoint.

End the description with the standard scope line: `Scope: BACKEND ONLY. Do not modify web/ frontend
code.` Mirror zulip-001's `description:` block-scalar style and its `# --- Methodology note (NOT
shown to agent) ---` comment block.

## Step 4 — Fill the schema fields

Mirror `tasks/zulip-001.yaml` exactly. Required top-level keys and how to fill each:

```yaml
id: <id>
title: "<human title, may paraphrase the PR title WITHOUT leaking mechanism into the prompt;
         title is metadata, not shown as the prompt — PR-accurate title is fine here>"
source_pr: "https://github.com/zulip/zulip/pull/<N>"
source_commit: "<merge SHA>"
parent_commit: "<parent SHA>"      # from the table; MUST match Step 0
merged_date: "<YYYY-MM-DD>"        # 2025-11-05 (002) / 2025-05-02 (003)
repo: zulip
language: python
type: <T2 or T3 per table>
tier: <hard or medium per table>

# Repo scale comment (copy zulip-001's two-line note verbatim — same repo).

description: |
  <the outcome-only prompt from Step 3>

# --- Methodology note (NOT shown to agent) ---
# <the API-name-exception justification from Step 3>

gold_patch_files:
  <non-migration, non-doc backend files from Step 2, one per line>
# (zulip-003 only) migration handled by the "added >=1 migration" rule — list the migration
# under a short comment as zulip-001 does, or omit and rely on the rule; match zulip-001's style.

gold_test_files:
  <the test files from the tests diff>

gate_tests:
  <provisional, CLASS granularity — see Step 5>

regression_command: |
  <see Step 6>

token_budget: 1200000
wall_clock_cap_seconds: 2700

minimum_reading_set:
  <see Step 7>

red_herrings:
  <see Step 7>

attributes:
  <see Step 7>
```

## Step 5 — Set `gate_tests` PROVISIONALLY at CLASS granularity

`test-backend` runs **only** module- or class-level dotted labels — never a single method (a
method-level label is fed raw to `__import__` and dies "is not a package"; see CLAUDE.md). Pin at
CLASS level. Write them as pytest-style nodeids `path::ClassName` (the harness's `nodeid_to_dotted`
translates and collapses to class). Mark them provisional with a comment; the **Validator finalizes
them on the container**.

Get the class names from the gold test diff:
```bash
grep -E '^\+class .*Test' tasks/<id>.gold-tests.diff
# also inspect existing classes in modified (non-new) test files if the new tests were added into them:
grep -nE '^class .*Test' /Users/divkov/workplace/fatbench/repos/zulip/zerver/tests/<file>.py
```

- **zulip-002 provisional gates** (primary = the new module's suite; others are regression-as-gate,
  see design doc oracle note):
  - `zerver/tests/test_parallel.py::RunParallelTest`   # primary — imports the new primitive
  - `zerver/tests/test_parallel.py::RunNotParallelTest`
  - plus the class(es) in `test_import_export.py` / `test_realm_export.py` / `test_slack_importer.py`
    that the diff modifies — the Validator confirms which classes and that they pass with gold applied.
- **zulip-003 provisional gates:**
  - `zerver/tests/test_reminders.py::RemindersTest`    # primary — posts /json/reminders, checks delivery
  - the modified class in `zerver/tests/test_scheduled_messages.py` (find via the grep above — the
    delivery-path class the diff touches)

Add the same comment block zulip-001 uses noting gates are pinned at class level and finalized by a
container run.

## Step 6 — `regression_command`

Scope to the modules most likely affected (fast subset), like zulip-001.

- **zulip-002:**
  ```
  ./tools/test-backend zerver.tests.test_parallel zerver.tests.test_import_export \
    zerver.tests.test_realm_export zerver.tests.test_slack_importer \
    zerver.tests.test_rocketchat_importer zerver.tests.test_mattermost_importer \
    zerver.tests.test_management_commands
  ```
- **zulip-003:**
  ```
  ./tools/test-backend zerver.tests.test_reminders zerver.tests.test_scheduled_messages
  ```

## Step 7 — Fill selection-time docs (NOT shown to agent)

`minimum_reading_set`, `red_herrings`, `attributes`. These are validation documentation. Use the
design doc's "cross-file reasoning burden" section to pick these honestly.

- **`minimum_reading_set`** — the files a human needed to read to solve it. Include the new-file
  targets AND the context files that define the pattern to mirror:
  - *zulip-002:* one existing callsite per importer family (`data_import/slack.py`), the exporter
    (`lib/export.py`), the deferred-work worker (`worker/deferred_work.py`), `lib/transfer.py`, and
    "an existing ProcessPoolExecutor usage" as the pattern to consolidate. Note child-process DB
    reconnection as the subtle requirement.
  - *zulip-003:* `zerver/models/scheduled_jobs.py` (ScheduledMessage shape),
    `zerver/actions/scheduled_messages.py` (the delivery loop `try_deliver_one_scheduled_message`),
    `zerver/views/scheduled_messages.py` (endpoint pattern to mirror), `zerver/lib/exceptions.py`
    (error pattern), `zproject/urls.py` (route registration), and "an existing scheduled-message
    delivery test" for the expected behavior.
- **`red_herrings`** — similarly-named files that mislead:
  - *zulip-002:* `web/src/` export/import UI; other `concurrent.futures`/threading usages unrelated
    to import/export (e.g. queue processors that should NOT be converted).
  - *zulip-003:* `web/src/compose_reply.ts` (frontend, out of scope); the reminders *frontend* UI;
    `zerver/actions/message_send.py` (looks message-adjacent but is not the reminder path).
- **`attributes`** — same keys as zulip-001 (`scatter`, `snr`, `red_herrings`, `inferential_distance`,
  `ambiguity`, `coupling`, `spec`). Suggested values:
  - *zulip-002:* scatter high, snr low, red_herrings medium, inferential_distance medium (must infer
    a single primitive fits all callsites), ambiguity medium, coupling **wide** (fan-in across
    callsites), spec under.
  - *zulip-003:* scatter medium, snr low, red_herrings medium, inferential_distance medium (must
    discover the reuse target), ambiguity low (endpoint pinned), coupling **deep** (new path depends
    on existing delivery subsystem), spec under.

## Step 8 — Self-check before handing to the Validator

```bash
/Users/divkov/workplace/fatbench/.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('tasks/<id>.yaml')); print('YAML OK')"
# Leakage self-audit — the description must NOT contain implementation file/function names:
grep -nE '\.py|ProcessPoolExecutor|try_deliver|ScheduledMessage|_disconnect|deferred_work' \
  <(sed -n '/^description:/,/^# ---/p' tasks/<id>.yaml)  # expect ONLY the allowed API-exception terms
```
Allowed terms in the description per the exception: zulip-002 → `run_parallel`, `run_parallel_queue`,
`zerver/lib/parallel.py`; zulip-003 → `/json/reminders`, `reminder_id`. Anything else flagged =
scrub it.

Confirm: parent SHA matches, all gold files exist in the diffs, gate_tests are class-level and
marked provisional, scope line present, methodology note present.

## Handoff to the Validator (do NOT do these — the Validator does, on the container)

- Finalize `gate_tests` node ids by running gold-backend + gold-tests on the provisioned Zulip
  container and confirming PASS, and running gold-tests alone on the unpatched parent and confirming
  FAIL (for zulip-002 the FAIL is the ImportError from `test_parallel`; the importer/export classes
  are regression-as-gate and are validated to PASS-with-gold, see design doc oracle note).
- Confirm the migration applies (zulip-003).
- Reuse/extend the `fatbench/zulip-provisioned:zulip-001` image approach; a fresh provision may be
  needed per parent commit.
