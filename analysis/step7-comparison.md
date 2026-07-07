# Step 7 — Baseline vs Full-Harness on zulip-001

**Date:** 2026-07-07. Task: `zulip-001` (add `topics_policy` realm setting, deprecate
`mandatory_topics`). Agent: `claude -p` 2.1.201, `--dangerously-skip-permissions`.
Gates + regression run on the provisioned Zulip container on the remote Cloud Desktop.
All correctness values use the FIXED grader (impl-only staged, gold tests overlaid).

## Results

| Run | config | complete | precision | correctness | regression | composite | tokens | turns | min |
|-----|--------|---------:|----------:|------------:|-----------:|----------:|-------:|------:|----:|
| Step 6 | baseline | 0.80 | 0.80 | 0 | 1.0 | — | 297K | 122 | 21.7 |
| Step 7 | baseline | 0.80 | 0.80 | 0 | 1.0 | 0.57 | 291K | 126 | 32.2 |
| Step 7 | full-harness | 0.60 | 0.75 | 0 | 1.0 | 0.51 | 192K | 115 | 26.4 |

(Step 6's on-disk `scores.json` shows correctness=1.0 — the pre-fix grading bug; the true
value is 0, recorded in `scores.corrected.json`. Composite omitted for Step 6 as it was
computed from the buggy correctness.)

## What each run missed (vs the 9 gold non-migration files)

- **Baseline (both runs, identical):** matched 7/9. Missed `zerver/lib/event_schema.py` and
  `zerver/lib/event_types.py` — the **event typing/validation** layer. Got the model, both
  enforcement actions (`message_send`, `message_edit`), `realm_settings`, `events.py`, the
  view, and openapi. Added 2 extras (`api_docs/changelog.md`, `version.py`).
- **Full-harness:** matched 5/9. Missed the same two event-typing files **plus**
  `message_send.py` and `message_edit.py` (the **enforcement** layer). Same 2 extras.

## Findings

1. **Correctness = 0 for both.** Neither solution passes the PR's gates; both miss
   `event_schema.py` + `event_types.py`, so `test_events` / `test_home` fail. The onboarding
   CLAUDE.md — which *explicitly* warns "the classic failure mode is forgetting the
   event-system registration" — did **not** move the agent to those files. Institutional
   knowledge stated in prose was not sufficient to fix the specific omission on this task.

2. **Full-harness scored LOWER on file metrics (0.60 vs 0.80) with ~34% fewer tokens
   (192K vs 291K) and fewer turns (115 vs 126), running to completion (not truncated).**
   The onboarding steered the agent to a *narrower, earlier-terminating* solution: it built
   the settings/event-register path the CLAUDE.md emphasized but dropped the enforcement
   actions the baseline found by exploration. Plausible read: the map made the agent
   confident it had covered the layers and stop exploring sooner. N=1 — do not over-read.

3. **The instrument is reproducible.** Two independent baseline runs landed on the *same*
   7 matched files, same 2 missed, 291K vs 297K tokens. The file metrics are stable signal.

4. **File metrics discriminate where correctness can't.** All three runs have correctness=0,
   but completeness cleanly separates them (0.8 vs 0.6) and pinpoints *which layer* each
   dropped — the diagnostic signal a pass/fail-only oracle (SWE-bench) discards. This is the
   core value proposition, demonstrated.

## Caveats

- **N=1 task, 1 run per config.** The full-harness-is-worse result is a single observation;
  it could be run-to-run variance. A real conclusion needs multiple seeds and multiple tasks.
- **CLAUDE.md is one specific onboarding doc.** A different phrasing (or one that pointed at
  an analogous existing setting to mirror) might help. This measures *this* doc, not
  "onboarding" in general.
- The full-harness CLAUDE.md's build-commands section repeats the known-wrong method-level
  `test-backend` example (line 15); harmless to solving the task, left stable mid-experiment.

## Bottom line

The MVP goal is met: the instrument produces **discriminating, reproducible, multi-axis
scores** on a real fat-context task, and it already yielded a non-obvious finding (a plausible
onboarding doc made the solution narrower, not better). Whether that finding generalizes is a
question for more tasks + more runs — which is exactly what `harness/author.py` and the
provisioned snapshot now make cheap.
