# Step 7 — Baseline vs Full-Harness on zulip-001

**Date:** 2026-07-07. Task: `zulip-001` (add `topics_policy` realm setting, deprecate
`mandatory_topics`). Agent: `claude -p` 2.1.201, `--dangerously-skip-permissions`.
Gates + regression run on the provisioned Zulip container on the remote Cloud Desktop.
All correctness values use the FIXED grader (impl-only staged, gold tests overlaid).

## Results

| Run | config | MCP | complete | precision | correctness | regression | composite | tokens | turns | min |
|-----|--------|-----|---------:|----------:|------------:|-----------:|----------:|-------:|------:|----:|
| Step 6 | baseline | on | 0.80 | 0.80 | 0 | 1.0 | — | 297K | 122 | 21.7 |
| Step 7 | baseline | on | 0.80 | 0.80 | 0 | 1.0 | 0.57 | 291K | 126 | 32.2 |
| Step 7 | full-harness | on | 0.60 | 0.75 | 0 | 1.0 | 0.51 | 192K | 115 | 26.4 |
| Step 8 | baseline (vanilla) | **off** | 0.80 | 0.80 | 0 | **0.0** | 0.42 | 165K | 124 | 16.4 |

("MCP on" = the run inherited this machine's user-level MCP servers — not vanilla. "MCP off" =
`--strict-mcp-config`, no MCP servers: clean vanilla Claude Code. Step 6's on-disk `scores.json`
shows correctness=1.0 — the pre-fix grading bug; true value 0, in `scores.corrected.json`.)

### Vanilla baseline (Step 8) — the clean "just the model on the task" control
Re-ran baseline with MCP servers disabled (`--strict-mcp-config`), so it's genuinely vanilla
Claude Code, not "as this machine is configured". Findings:
- **File metrics identical to the MCP-on baselines** (0.80/0.80, same 7 matched, same 2 missed:
  `event_schema.py` + `event_types.py`). Disabling MCP did NOT change *what* the agent found —
  the fat-context failure is about the model's exploration, not tooling. Cheaper: 165K tokens.
- **regression = 0.0 (NEW):** this run's impl references `Realm.REALM_TOPICS_POLICY_TYPES`, an
  attribute it never defined; the regression suite hits that path → 34 pre-existing tests ERROR.
  The earlier baseline's (different, also-wrong) code happened not to break regression.
- **KEY CALIBRATION INSIGHT — run-to-run variance is real and axis-dependent.** Two baseline
  runs agree *exactly* on file metrics but *diverge* on correctness/regression, because the agent
  writes different (each wrong) code each attempt. The retrieval signal (which files) is stable;
  the execution signal (does the code work) has variance. => N=1 per config is insufficient for
  correctness/regression claims; file metrics are already stable at N=1.

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

3. **The retrieval signal is reproducible; the execution signal is not (at N=1).** THREE
   independent baseline runs (2 MCP-on, 1 vanilla) landed on the *same* 7 matched / 2 missed
   files — file metrics are rock-stable. But correctness/regression varied: the vanilla run
   broke 34 regression tests (regression=0.0) where the others didn't, because the agent writes
   different (each wrong) code each attempt. **Implication: file metrics are trustworthy at N=1;
   correctness/regression need multiple runs per config.**

4. **Vanilla vs machine-configured (MCP) baseline: no file-metric difference.** Disabling MCP
   servers (`--strict-mcp-config`) left completeness/precision unchanged (0.80/0.80) and just
   made the run cheaper (165K vs ~291K tokens). On this task the model's MCP tooling didn't help
   it find the missing layer — the gap is exploration/reasoning, not tools.

5. **File metrics discriminate where correctness can't.** All runs have correctness=0, but
   completeness cleanly separates configs (0.8 baseline vs 0.6 full-harness) and pinpoints
   *which layer* each dropped — the diagnostic signal a pass/fail-only oracle (SWE-bench)
   discards. This is the core value proposition, demonstrated.

## Caveats

- **N=1 task, 1 run per config (except baseline, N=3).** The full-harness-is-worse result is a
  single observation and could be variance — and we now have DIRECT evidence that correctness/
  regression vary run-to-run (finding 3). A real conclusion needs multiple seeds and more tasks.
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
