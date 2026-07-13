# SWE-bench two-arm comparison: raw vs. your Claude config

Measures what your Claude Code setup (plugins, skills, MCP, hooks, CLAUDE.md) buys you on a
standard benchmark, by running the **same** `claude -p` on the **same** SWE-bench instances two
ways and grading both with the **official** SWE-bench oracle.

This reuses FatBench's platform philosophy — *a run is just (task) → score; the harness measures
what you bring, not what it injects* — but points it at `princeton-nlp/SWE-bench_Lite` and the
official Docker grader instead of the hand-authored Zulip tasks.

## The two arms (the whole experiment is `arms.py`)

| Arm | What loads |
|-----|-----------|
| `raw` | **vanilla claude** — isolated empty `CLAUDE_CONFIG_DIR`, `--strict-mcp-config` with no `--mcp-config`, minimal `--settings` carrying only Bedrock auth. No plugins / skills / MCP / CLAUDE.md. |
| `mine` | `claude -p` under your **real** environment — plugins, skills, MCP servers, hooks, CLAUDE.md all load exactly as in normal use. |

Everything else is identical: dataset, `repo@base_commit` checkout, prompt (= `problem_statement`),
permission mode (`bypassPermissions`), model, and grader.

Auth note: we don't use `claude --bare` (it forces `ANTHROPIC_API_KEY`/apiKeyHelper and disables
Bedrock, which this machine uses). Isolation is via an empty config dir + minimal settings instead.

## Pipeline

1. `dataset.py` — load SWE-bench Lite via the HF datasets-server rows API (pure stdlib, cached).
2. `predict.py` — per (instance × arm): clone `repo@base_commit`, run the arm's agent, collect the
   git diff → SWE-bench predictions JSONL (`instance_id`, `model_name_or_path`=arm, `model_patch`)
   + per-run telemetry (tokens/cost/turns).
3. `evaluate.py` — scp predictions to the remote Cloud Desktop, run the **official**
   `swebench.harness.run_evaluation` in Docker there (x86_64-native), pull back the report.
4. `compare.py` — raw vs. mine: resolved rate, head-to-head wins/regressions, token & cost ratios.
5. `run.py` — orchestrator CLI.

## Usage

```bash
V=../.venv/bin/python  # or your venv

# Predictions only (no grading) — sanity-check patches first:
$V -m swebench.run --arms raw,mine --limit 3 --no-eval

# Full pilot: predict + grade on the remote + compare (~20-30 instances):
$V -m swebench.run --arms raw,mine --limit 25 \
    --remote-host <cloud-desktop-host> --remote-python /path/to/venv/bin/python

# A specific instance set:
$V -m swebench.run --arms raw,mine --instance-ids astropy__astropy-12907,django__django-11039
```

## Remote grading prerequisites (one-time)

The `--remote-host` must have a working docker daemon and `swebench` installed in the
`--remote-python` env. `evaluate.check_remote_ready()` probes both before running. The grader is
x86_64-native on the Cloud Desktop (same host used for the Zulip gates) — no emulation.

## Cost

~25 instances × 2 arms = 50 agent runs + 50 gradings. Start with `--limit 2 --no-eval` to smoke
the prediction path, then a small graded run, before the full pilot. Telemetry per run captures
tokens/cost so the comparison reports spend, not just resolved rate.
