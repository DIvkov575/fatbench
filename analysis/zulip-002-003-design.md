# zulip-002 & zulip-003 — Task Design

Architect output. Grounds two new FatBench tasks in verified real merged Zulip PRs, chosen to be
maximally distinct from `zulip-001` and from each other. This doc + the companion writing plan
(`analysis/zulip-002-003-writing-plan.md`) are the single source of truth the Builders follow.

## Verification summary (done by the Architect, not estimated)

All counts below were confirmed against the GitHub API (`gh api .../pulls/<N>` + `/files`) and every
parent commit was confirmed reachable in the local full clone at `repos/zulip`:

| PR | merge SHA | parent SHA (verified `rev-parse <merge>^`) | changed | merged |
|----|-----------|--------------------------------------------|---------|--------|
| #28617 | `0ee71c0a6ad067571e8a93ff3e09241b65f67926` | `c15f426dc6fce3751ab8f131030d51f29affa0f1` | 19 | 2025-11-05 |
| #34505 | `733817cb510fa3a5540161ee9a58365445c3cab4` | `ad9cb501831949241e7e85416f0ae731bc2f7759` | 19 | 2025-05-02 |

Both parents resolve locally (spot-checked). Note: `repos/zulip` is currently pinned at zulip-001's
parent `8fb1eeeb09`; the harness checks out the task's parent into a *fresh* workspace, so no change
to the vendored fixture is needed — but each Builder MUST record the correct parent SHA in the YAML.

### PRs rejected

- **#34836** (register push device / E2EE) — REJECTED. Touches `event_schema`/`event_types`/`events`,
  which is exactly zulip-001's propagation mechanism. Least archetype-diverse. Per instructions.
- **#19748** (asyncio SMTP server) — REJECTED. 34 files but the substance is Puppet manifests,
  shell scripts, and ops docs; only ~9 files are `zerver/*.py`. Heavy infra/ops noise pollutes the
  file-based completeness/precision metrics, and the mail-server tests risk depending on
  SMTP/socket machinery that fights the container oracle. Poor fit for a clean backend oracle.
- **#32811** (message reporting endpoint) — NOT SELECTED (viable backup). Solid, but it adds a realm
  field + `populate_db` seeding, which rhymes with zulip-001's "add a field to the realm" flavor,
  and at 8 backend files it is the thinnest of the shortlist. Kept as fallback if either pick fails
  container validation.

---

## The diversity thesis

Three tasks, three orthogonal cross-file reasoning archetypes. Each stresses a *different* failure
mode of partial context.

| Dimension | zulip-001 (topics_policy) | zulip-002 (#28617 parallelism) | zulip-003 (#34505 reminders) |
|-----------|---------------------------|--------------------------------|------------------------------|
| **Subsystem** | Realm settings + real-time event system | Data import/export + background worker | Scheduled-message delivery + compose |
| **Archetype** | Distributed invariant **propagation** (add a concept, thread it through every layer) | Cross-callsite **abstraction consolidation** (a refactor: route N duplicated callsites through one new primitive) | Subsystem **extension / feature threading** (reuse existing delivery machinery for a new endpoint) |
| **Change nature** | Feature: new field + enum + migrations + events | Refactor: behavior-preserving concurrency rework, one new lib module | Feature: new API endpoint on top of an existing scheduler |
| **Coupling type** | Vertical: model→action→event schema→event types→events→view, all must agree on one setting | Horizontal fan-in: 8 disparate callsites (slack/mattermost/rocketchat importers, export, transfer, mgmt commands, deferred-work worker) must all adopt one shared `run_parallel` abstraction identically | Diagonal reuse: a *new* path (actions/lib/views/urls) must hook into the *pre-existing* `ScheduledMessage` model + `try_deliver_one_scheduled_message` delivery loop instead of building a parallel system |
| **What makes it fat** | ~12 relevant files scattered across 6+ dirs, drowned in hundreds of similar-looking `*_policy` settings; event-layer registration is easy to miss | Duplication is spread across 4 dirs and 3 different third-party importers; you must *find every callsite* of the old pattern, and the new primitive's semantics (process pool + DB reconnect + queue variant) must fit all of them | The delivery infrastructure lives in a *different* feature (scheduled messages); you must read that subsystem to learn the ScheduledMessage row shape, the delivery trigger, and rendering, then extend it — not reinvent it |
| **Primary layer an agent is likely to DROP** | Event-system registration (`event_schema`/`event_types`/`events.py`) — model+view look "done" but `test_events` fails | The callsites: an agent writes the new `lib/parallel.py` and its unit test passes, but leaves the 8 importer/export/worker callsites on the old pattern — file metrics + importer regressions expose it | The shared plumbing: an agent builds a standalone reminders table/loop and never wires into `ScheduledMessage` + `try_deliver_one_scheduled_message`, so the delivery test fails and `scheduled_messages` regressions break |

The three "primary dropped layers" are deliberately different: an **event registration**, a **set of
callsites**, and a **shared delivery mechanism**. An agent's context-management weakness will show up
differently on each, which is the discrimination signal FatBench exists to produce.

---

## zulip-002 — Parallelize export/import (PR #28617)

- **Title:** "Factor out ProcessPoolExecutor callsites, download files from S3 in parallel"
- **Merge SHA:** `0ee71c0a6ad067571e8a93ff3e09241b65f67926`
- **Parent SHA:** `c15f426dc6fce3751ab8f131030d51f29affa0f1`
- **Merged:** 2025-11-05 (well post knowledge-cutoff)

**What the change does.** Zulip's data import and export code contained several independent,
copy-pasted uses of `concurrent.futures.ProcessPoolExecutor` (in the Slack/Mattermost/RocketChat
importers, the realm exporter, the file-transfer layer, management commands, and the deferred-work
worker). This PR factors that duplication into a single new primitive module that offers a
`run_parallel` / `run_parallel_queue` API (with correct child-process DB-connection handling —
`_disconnect`), then rewrites every callsite to use it, and additionally parallelizes S3 file
downloads. It is a behavior-preserving concurrency **refactor** plus one new capability (parallel S3
download). No models, no migrations, no settings, no events, no API — maximally distant from
zulip-001.

**Backend files touched (12 non-test), grouped by dir:**

- `zerver/lib/` — `parallel.py` (**new module**, the shared primitive), `export.py`, `import_realm.py`, `transfer.py`
- `zerver/data_import/` — `import_util.py`, `slack.py`, `mattermost.py`, `rocketchat.py`
- `zerver/management/commands/` — `export.py`, `export_usermessage_batch.py`, `convert_slack_data.py`
- `zerver/worker/` — `deferred_work.py`

4 directories, one new module, 11 existing files rewritten to consume it.

**Test files (7):** `zerver/tests/test_parallel.py` (**new, 306 lines** — the hard oracle),
`test_import_export.py`, `test_realm_export.py`, `test_slack_importer.py`,
`test_rocketchat_importer.py`, `test_mattermost_importer.py`, `test_management_commands.py`
(the last six are the pre-existing behavioral suites the refactor must not break; edits are small,
1–9 lines each).

**Cross-file reasoning burden.** To get this right an agent must: (1) recognize that the several
ProcessPoolExecutor blocks are the *same* pattern and can share one abstraction — this requires
reading all four directories, not one; (2) design the primitive so its signature fits every
consumer (a plain parallel map for importers *and* a queue-draining variant for the worker) — the
semantics are constrained by the union of callsites; (3) handle the classic fork-safety gotcha
(each child process must drop the inherited DB connection — `_disconnect`), which the new unit test
pins; (4) update all callsites consistently. The failure mode is writing the primitive, passing its
unit test, and leaving callsites unconverted — caught by file metrics and by importer/export
regressions.

**Oracle validity note (important, honest).** `zerver/tests/test_parallel.py` imports from the new
`zerver/lib/parallel.py`, so it hard-fails on the unpatched parent (ImportError) — a clean
SWE-bench-style gate. The six *existing* importer/export test files, however, pass on the parent too
(it's a refactor), so they do **not** discriminate "did nothing" from "did it right"; their value is
as **regression-as-gate** — they fail if the refactor breaks a converted callsite. This is why the
task keeps completeness/precision as first-class signals: for a consolidation refactor, "touched all
12 files" is much of the skill under test. Call this out to the Validator: the primary correctness
gate is `test_parallel`; the importer/export classes are gates against a *broken* refactor, not
against an *absent* one.

**Leakage to scrub.** The PR title itself names the mechanism ("ProcessPoolExecutor", "S3",
"parallel"). The auto-generated draft's `description:` will be the PR body — which here is just the
boilerplate self-review checklist (no real prose), so the Builder must **write the description from
scratch** describing the desired *outcome*: "export and import of realm data, and S3 file transfer,
should run concurrently across worker processes rather than serially; concurrency logic that is
currently duplicated across the import/export code paths should be consolidated." Do **not** name
`ProcessPoolExecutor`, `run_parallel`, `lib/parallel.py`, or the specific files.

**Methodology concerns.**
- **API-name pinning — YES, needs the exception.** `test_parallel.py` imports the exact names
  `run_parallel`, `run_parallel_queue`, `_disconnect` from `zerver.lib.parallel`. Because the gold
  test pins these, the prompt MUST be allowed to specify that a shared helper exposing
  `run_parallel`/`run_parallel_queue` (in a `zerver/lib/parallel.py` module) is expected — same
  documented exception as zulip-001. The measured skill is consolidation + fork-safety, not
  API invention. Document this in the YAML methodology note.
- Parent commit is a clean pre-refactor state (verified reachable). Refactor tasks are inherently
  more forgiving on "exact file set" — flag `precision` may be noisy if the agent's consolidation
  factors differently than gold; that is acceptable and expected.

---

## zulip-003 — Add API endpoint to schedule reminders (PR #34505)

- **Title:** "reminders: Add API endpoint to schedule reminders."
- **Merge SHA:** `733817cb510fa3a5540161ee9a58365445c3cab4`
- **Parent SHA:** `ad9cb501831949241e7e85416f0ae731bc2f7759`
- **Merged:** 2025-05-02 (post-cutoff)

**What the change does.** Adds a new backend API endpoint (`POST /json/reminders`) that lets a user
schedule a reminder about a specific message at a future time. Rather than building a new delivery
system, it **reuses Zulip's existing scheduled-message infrastructure**: it adds a
`reminder_target_message_id` column to the `ScheduledMessage` model (one migration), introduces a
reminders action + lib layer that create a `ScheduledMessage` of a reminder type, and hooks into the
existing `try_deliver_one_scheduled_message` delivery loop and rendering. A new exception
(`DeliveryTimeNotInFutureError`) validates the requested time. Frontend (`web/src/compose_reply.ts`)
is excluded — backend-only task.

**Backend files touched (~10 substantive non-test + 1 migration), grouped by dir:**

- `zerver/actions/` — `reminders.py` (**new**), `scheduled_messages.py` (extend delivery)
- `zerver/lib/` — `reminders.py` (**new**), `message.py`, `exceptions.py` (new error), `markdown/fenced_code.py`
- `zerver/models/` — `scheduled_jobs.py` (add `reminder_target_message_id` field)
- `zerver/migrations/` — `0699_scheduledmessage_reminder_target_message_id.py` (**new**)
- `zerver/views/` — `reminders.py` (**new** endpoint), `scheduled_messages.py`
- `zerver/openapi/` — `zulip.yaml` (endpoint spec)
- `zproject/` — `urls.py` (route registration)

7 directories. (Excluded from gold: `web/src/compose_reply.ts` frontend; `version.py` +
`api_docs/*.md` are version-bump/doc noise — `author.py` classifies `.md` as doc and `.ts` as
frontend automatically.)

**Test files (3):** `zerver/tests/test_reminders.py` (**new, 289 lines** — the hard oracle: posts to
`/json/reminders`, asserts `ScheduledMessage` rows and the rendered reminder content),
`test_scheduled_messages.py` (modified 9+/5- — exercises the shared delivery path),
`test_openapi.py` (1 line — doc/spec consistency).

**Cross-file reasoning burden.** The endpoint is small; the fat part is that its correct
implementation lives in *another feature's* code. To get it right an agent must read the scheduled
messages subsystem to learn: how a `ScheduledMessage` row is shaped and typed, how
`try_deliver_one_scheduled_message` picks up and delivers due rows, and where delivery content is
rendered — then extend all of that (new type discriminator, new target-message column + migration,
delivery + rendering branch) rather than standing up a separate reminders table and loop. The
failure mode is a self-contained reminders implementation that never integrates with
`ScheduledMessage`, so `test_reminders`'s delivery assertions fail and `test_scheduled_messages`
regresses. This is "understand and safely extend an existing subsystem you didn't write" — distinct
from both other tasks.

**Leakage to scrub.** The auto-draft `description:` = PR body = *"Extracted from #27169. This just
adds the API endpoint and tests which ensure that the reminders are scheduled correctly."* Minimal
prose but the Builder must still write an outcome-only spec: "Users can schedule a reminder about a
specific message to be delivered to themselves at a chosen future time, via a JSON API endpoint.
Scheduling a reminder with a delivery time that is not in the future must be rejected. Reminders are
delivered by the same mechanism that delivers other future-dated messages." Do **not** name
`ScheduledMessage`, `try_deliver_one_scheduled_message`, `scheduled_jobs.py`, or the file layout.

**Methodology concerns.**
- **API-name pinning — YES, needs the exception (limited).** `test_reminders.py` posts to the literal
  URL `/json/reminders` and reads `reminder_id` from the response. The gold test pins the endpoint
  path and response field, so the prompt MUST be allowed to state the endpoint path
  (`POST /json/reminders`), the two request parameters (a message id and a future delivery timestamp),
  and that the response returns the created reminder's id. It need NOT (and must not) name internal
  functions/models. Same documented exception as zulip-001, scoped to the public HTTP surface the
  test pins.
- Parent commit verified reachable and is a clean "before reminders exist" state.
- The migration filename (`0699_...`) is gold-specific; use the standard completeness rule
  ("non-migration files + added ≥1 migration"), as zulip-001 does.
