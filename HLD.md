# FatBench: High-Level Design

## Problem Statement

Existing coding-agent benchmarks (SWE-bench, HumanEval, Aider polyglot) test localized code generation. They reward agents that grep well and edit 1-3 files. They do not test what breaks in practice: reasoning across large codebases where the minimum reading set for a correct solution spans 15-40 files and the agent must make retrieval decisions under uncertainty.

FatBench is a benchmark for **context-demanding** coding problems — tasks where partial context yields confident-but-wrong solutions.

## Competitive Landscape

### What exists and why it's insufficient

| Benchmark | Context size | Output size | Multi-file edit | Reasoning demand | What it actually tests |
|-----------|-------------|-------------|-----------------|------------------|----------------------|
| **SWE-bench** | Repo available, but ~3 files matter | Small patch (1-3 files, 277-17K chars) | Rarely | Low-medium | Retrieval + small local edits |
| **Long Code Arena** (JetBrains) | Full repo | Single output (summary, location, message) | No | Medium | Large-context comprehension (read-only) |
| **RepoBench** | Cross-file | One line | No | Low | Cross-file next-line prediction |
| **SWE-bench+** | Same as SWE-bench | Same | Same | Same | Quality audit; confirmed 32% of SWE-bench has solution leakage |
| **FatBench** | Full repo, 25+ files MUST be read | Large patch (8-15 files, new + modified) | Yes | High | Retrieval + reasoning + multi-file coordinated implementation |

### SWE-bench specifics

- Typical task modifies 1-3 files. Patch size: 277-17,400 characters.
- Difficulty distribution: majority "15 min - 1 hour" human time.
- The repo is large, but the minimum reading set is small. An agent that greps well and reads one file carefully solves most tasks.
- The hardest tasks (multi-file, "1-4 hours") exist but are a tiny fraction and aren't designed to stress context management specifically.

### Long Code Arena specifics

- 6 tasks: library-based code generation, CI repair, project-level completion, commit message generation, bug localization, module summarization.
- Context: "up to a whole code repository."
- Critical gap: all tasks are read-only or single-output. No task requires coordinated multi-file edits. Tests whether you can UNDERSTAND a large codebase, not whether you can CHANGE it coherently.

### The gap FatBench fills

No existing benchmark tests: "given a 25K-line repo, implement a feature that requires reading and reasoning across 25+ files, then producing coordinated edits to 10+ files where each edit depends on understanding the others."

SWE-bench tests retrieval + small patches. Long Code Arena tests comprehension. FatBench tests large-context problem solving — retrieval + reasoning + multi-file coordinated implementation.

## Design Principles

1. **Minimum reading set must be large.** If an agent can solve a task by reading <5 files, it's not a FatBench task regardless of repo size.
2. **Partial knowledge must be penalized.** Reading 4 of 5 relevant files should produce a plausible but incorrect answer — not a "close enough" one.
3. **Token budget is the constraint, not time.** Tasks target ~1M tokens of input+output. The benchmark measures what agents achieve within that budget.
4. **No synthetic padding.** Every file in the repo must be load-bearing (referenced by tests, imported by production code, or containing invariants). No filler.
5. **Multi-axis scoring.** Pass/fail is insufficient. Score completeness, correctness, precision, regression safety, and token efficiency independently.
6. **Reproducible and deterministic.** Same repo state, same task definition, same Docker environment for every run.

## Task Taxonomy

### T1: Distributed Invariant Propagation
Add a feature that requires coordinated changes across N files, where each file has different constraints. Missing any one produces a silent bug that passes naive tests but fails edge-case assertions.

**Example:** "Add per-organization rate limiting" in a full-stack app — requires auth middleware, billing model awareness, cache layer integration, migration, API versioning, test factories.

**Context demand:** Agent must read auth chain, billing model, caching layer, 3-5 existing similar features (to infer the pattern), test helpers, migration history.

### T2: Cross-Module Causal Debugging
A test failure whose root cause is N hops away from the symptom. The agent must trace data flow across module boundaries, understanding transformations at each hop.

**Example:** Wrong value in Service D caused by serialization edge case in Service A, masked by transform in B, cached incorrectly by C.

**Context demand:** Agent must read all modules in the data path, understand each transformation, identify where corruption enters. Red herrings in other modules that look relevant but aren't.

### T3: Convention Inference at Scale
Add a new instance of a pattern that has combinatorial variants. The agent must read enough existing instances to infer the rules and apply the correct combination.

**Example:** "Add subscription resource type (async, cached, audited)" in a repo with 15 existing resource types spanning 3 variant dimensions.

**Context demand:** Agent must read 4-5 existing implementations to discover the combinatorial pattern, classify the new instance, and apply the correct variant across 8-10 files.

### T4: Semantic Migration
Replace API A with API B across 50+ call sites, where A and B have different semantics (error handling, return types, side effects). Each call site requires classification and site-specific handling.

**Example:** Replace `legacyAuth.validate()` (returns null on failure) with `newAuth.check()` (throws on failure). Each call site has different error-handling context.

**Context demand:** Agent must read every call site, classify it (already handles exceptions / relies on null / hot path), and apply the correct transformation per category.

### T5: Spec-Code Gap Implementation
A specification document + partial implementation. Agent must diff spec against code, identify gaps, implement remaining features following existing patterns, without duplicating what exists.

**Example:** 5K-word feature spec, 70% implemented. Agent must find the 30% that's missing and implement it consistently with the existing 70%.

**Context demand:** Reading the full spec + full existing implementation + pattern inference from existing code.

### T6: Adversarial Retrieval
A real task solvable by reading 5-8 specific files — but embedded in a repo with 50+ files containing similar names, similar code structure, and misleading signals. Tests whether the agent discriminates signal from noise.

**Example:** A bug in `OrderProcessor.validate()` — but there are 6 other `validate()` methods, 3 other `*Processor` classes, and 4 test files that exercise adjacent-but-unrelated logic.

**Context demand:** The agent WILL explore wrong paths. The question is whether it converges.

## Architecture

### Repo Strategy: Seeded from OSS + Augmented

Pure synthetic repos are too clean (LLMs find their own patterns easy). Pure OSS repos are uncontrolled (can't guarantee context-demand properties). Hybrid approach:

1. **Fork a real medium-sized OSS project** (5-15K lines) in a mainstream framework.
2. **Augment it** with modules that create cross-cutting concerns, invariant dependencies, and pattern variants. Human-written or heavily human-edited.
3. **Validate:** for each task, document the minimum reading set. If it's <15 files, add complexity until it isn't.
4. **Freeze:** lock the repo state. All tasks are defined against this exact commit.

### Target Repos (3)

| Repo | Language | Domain | Lines | Framework |
|------|----------|--------|-------|-----------|
| `acme-saas` | Python | Multi-tenant SaaS API | ~25K | FastAPI + SQLAlchemy + Celery + Redis |
| `nexus-events` | TypeScript | Event-driven microservices (4 services) | ~20K | Node + Kafka + Postgres |
| `forge-sdk` | Python | Developer SDK with plugin system | ~12K | Pure Python, rich type system |

### Task Definition Schema

```yaml
id: acme-saas-001
title: "Add per-organization rate limiting"
type: T1  # distributed invariant
repo: acme-saas
commit: abc123  # exact repo state
difficulty: hard

description: |
  <The issue as an agent would receive it — realistic, somewhat ambiguous>

minimum_reading_set:
  - src/auth/middleware.py
  - src/billing/models.py
  - src/billing/plans.py
  - src/cache/redis_client.py
  - src/api/v2/decorators.py
  - src/api/v2/routes/usage.py  # existing rate-limit-adjacent feature
  - tests/factories/org.py
  - migrations/0047_add_usage_tracking.py
  # ... (15-25 files)

red_herrings:
  - src/auth/legacy_middleware.py  # deprecated, looks relevant
  - src/api/v1/decorators.py      # old version, different pattern

gold_patch_files:
  - src/auth/middleware.py
  - src/api/v2/decorators.py
  - src/cache/rate_limiter.py  # new file
  - src/billing/models.py
  - tests/test_rate_limiting.py
  - tests/factories/org.py
  - migrations/0052_rate_limits.py

assertions:
  correctness:
    - "Rate limit applies per-org, not per-user"
    - "Enterprise plan is exempt"
    - "Limit resets on billing cycle, not calendar"
    - "429 response includes Retry-After header"
    - "Rate limit state survives Redis restart (persisted)"
  regression:
    - "Existing auth flow unaffected"
    - "Existing usage tracking unaffected"
  convention:
    - "Migration follows sequential numbering"
    - "Test uses existing factory pattern"
    - "Decorator follows same interface as existing decorators"

test_suite:
  setup: "docker compose up -d && alembic upgrade head"
  run: "pytest tests/test_rate_limiting.py -v"
  regression: "pytest tests/ -v --ignore=tests/test_rate_limiting.py"

token_budget: 1_000_000
estimated_minimum_tokens: 400_000  # best-case if agent navigates perfectly
```

### Scoring Engine

```
Score = {
  completeness: |gold_files ∩ agent_files| / |gold_files|,
  correctness:  assertions_passed / assertions_total,
  precision:    |gold_files ∩ agent_files| / |agent_files|,
  regression:   regression_tests_passed / regression_tests_total,
  efficiency:   score_achieved / tokens_consumed,  # normalized
}

# Final composite (configurable weights)
composite = 0.35*correctness + 0.25*completeness + 0.15*precision + 0.15*regression + 0.10*efficiency
```

### Harness Architecture

```
fatbench/
├── repos/                    # Git submodules or tarballs, pinned to exact commits
├── tasks/                    # YAML task definitions
├── harness/
│   ├── runner.py             # Orchestrator: setup env → invoke agent → collect patch → score
│   ├── scorer.py             # Multi-axis scoring from test results + file diff
│   ├── token_tracker.py      # Wraps API calls to measure actual token usage
│   ├── adapters/
│   │   ├── base.py           # Abstract: given (repo_path, task_description) → patch
│   │   ├── claude_code.py    # Invokes `claude` CLI with configurable CLAUDE.md + settings
│   │   ├── claude_api.py     # Direct API with agentic loop (for baseline)
│   │   ├── cursor.py         # Cursor agent mode
│   │   └── aider.py          # Aider
│   └── docker/
│       ├── acme-saas.Dockerfile
│       ├── nexus-events.Dockerfile
│       └── forge-sdk.Dockerfile
├── configs/                  # A/B test configurations
│   ├── baseline.yaml         # No CLAUDE.md, no plugins, no hooks
│   ├── minimal.yaml          # CLAUDE.md with build/test commands only
│   ├── full-harness.yaml     # Full plugin stack
│   └── ablations/            # One component removed at a time
├── analysis/
│   ├── compare.py            # Statistical comparison across configs
│   ├── visualize.py          # Generate charts
│   └── reports/              # Rendered analysis per run
└── results/                  # Raw output per run
    └── {run_id}/
        ├── meta.json         # Config, timestamp, model, cost
        ├── {task_id}/
        │   ├── patch.diff
        │   ├── transcript.jsonl   # Full agent conversation
        │   ├── scores.json
        │   └── test_output.log
        └── summary.json      # Aggregate scores
```

### Adapter Design (Claude Code)

The Claude Code adapter must preserve the agent's normal operating mode — hooks fire, plugins load, CLAUDE.md is read. The adapter:

1. Sets up a fresh workspace with the task repo
2. Writes the configured CLAUDE.md (or none, for baseline)
3. Installs configured plugins/hooks (or none)
4. Invokes `claude --print --dangerously-skip-permissions` with the task prompt
5. Captures the output diff
6. Measures tokens via `--output-format json`

Key constraint: the adapter must NOT give the agent any information it wouldn't have in real use. The task description is the only input. The agent must discover everything else by exploring the repo.

### Validation Protocol

Before a task enters the benchmark:

1. **Human solves it** — document time taken and files read. If a human needs <15 files, redesign the task.
2. **Minimum reading set verified** — remove one file from the set, confirm the solution becomes incorrect or incomplete.
3. **Red herring verification** — confirm that reading only red herring files produces a plausible but wrong solution.
4. **Test suite coverage** — confirm that the gold patch passes all assertions and that a "close but incomplete" patch fails at least 2.
5. **Token budget calibration** — run a capable agent once, measure actual tokens consumed. Set budget at 2-3x the observed amount.

## Phased Delivery

### Phase 0: Proof of Concept (1-2 weeks)
- Fork one real OSS repo (~10K lines)
- Write 3 tasks against it (one T1, one T2, one T6)
- Build minimal runner + scorer
- Run Claude Code with/without CLAUDE.md, compare manually
- **Exit criterion:** the 3 tasks produce measurably different scores with different configs

### Phase 1: First Complete Repo (3-4 weeks)
- Build `acme-saas` to 25K lines (seeded from real OSS + augmented)
- Write 10 tasks across 4 types
- Automated scoring pipeline
- Claude Code adapter working headlessly
- **Exit criterion:** 10 tasks, automated end-to-end, reproducible scores

### Phase 2: Multi-Repo + Multi-Agent (4-6 weeks)
- Add `nexus-events` and `forge-sdk`
- 30 total tasks
- Add adapters for 2+ other agents (Cursor, Aider, raw API)
- Statistical comparison tooling
- **Exit criterion:** publishable results comparing 3+ agents across 30 tasks

### Phase 3: Community + Publication (ongoing)
- Open-source the benchmark
- Leaderboard
- Task contribution protocol
- Annual refresh (tasks leak into training data)

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Repos are too "clean" / LLM-friendly | Benchmark is easy, doesn't discriminate | Seed from real OSS; add organic complexity; validate with humans |
| Gold patches are too rigid | Valid alternative solutions score 0 | Test-based scoring (multiple valid patches can pass); manual review for edge cases |
| Tasks leak into training data | Benchmark saturates over time | Version the benchmark; refresh tasks annually; keep some tasks private |
| Cost per run is prohibitive | Can't iterate | Start with 3 tasks in Phase 0; full suite is for final comparison |
| Claude Code adapter doesn't preserve hook behavior | Results don't reflect real harness performance | Integration test the adapter against known hook behavior before benchmarking |
| Scoring is noisy | Can't draw conclusions | Run each task 3x; report mean + variance; require statistical significance |

## Cost Model

| Scenario | Tasks | Tokens/task | Price/task (Opus) | Total |
|----------|-------|-------------|-------------------|-------|
| Quick check | 3 | 1M | ~$20 | $60 |
| Phase 0 | 3 tasks × 2 configs × 3 runs | 1M | ~$20 | $360 |
| Phase 1 | 10 tasks × 3 configs × 3 runs | 1M | ~$20 | $1,800 |
| Full suite | 30 tasks × 4 configs × 3 runs | 1M | ~$20 | $7,200 |

Sonnet as a cheaper sweep model: ~$6/task → full suite = ~$2,160.

## Design Decisions

### Non-determinism (minimized, not eliminated)
- All runs use temperature=0 where the API supports it.
- 2 runs per task (not 3). Require effect size > 15% to call a difference real. Cheaper, still statistically meaningful for 30-task suites.
- If 2 runs disagree on pass/fail for a given task, run a tiebreaker third.

### Difficulty Tiers
Each repo contains tasks at three tiers:

| Tier | Min reading set | Token budget | Target |
|------|----------------|--------------|--------|
| **Easy** | 5-10 files | 300K | Calibration — all good agents should pass |
| **Medium** | 10-20 files | 600K | Discriminates good from average |
| **Hard** | 20-40 files | 1M+ | Discriminates great from good; many agents fail |

Distribution: ~20% easy, ~40% medium, ~40% hard. Easy tasks establish a baseline; hard tasks are where harness quality matters.

### Code generation is not the bottleneck
If the agent correctly identifies all relevant files and understands their relationships, writing the patch is mechanical. The benchmark IS the retrieval + reasoning. Code generation is the trivial tail.

Implication: scoring does not separately evaluate "plan quality" vs "code quality." Wrong code means wrong understanding — the agent lost the needle.

### Contamination Strategy (mixed corpus)
Neither pure-OSS nor pure-synthetic. Both in known proportions:

| Source | Tasks | Contamination risk | Control level | Purpose |
|--------|-------|-------------------|---------------|---------|
| **OSS-seeded** | ~40% | Moderate-high | Lower | Ecological validity; tests real-world messiness |
| **Synthetic** | ~60% | Zero | Highest | Clean measurement; guaranteed no memorization |

Report scores by source separately. If a model aces OSS tasks but struggles on synthetic, contamination is the likely explanation.

Synthetic repos:
- Generated with specific complexity properties (invariant density, pattern variance, red herring saturation)
- Human-reviewed for naturalness — code must look organic, not template-stamped
- Contain canary strings (unique identifiers) to detect if they appear in future training data
- Rotated annually (new versions with same structural properties but different implementations)

OSS-seeded repos:
- Forked at a specific commit, augmented with additional modules
- Tasks defined against the augmented version (not the original) — so pure memorization of the OSS project doesn't help
- Augmentations are the load-bearing parts of each task

### Assertion Scoring: Gate + Bonus
Assertions split into two classes:
- **Gates** (fail any → correctness = 0): 3-4 assertions testing core understanding. If wrong, the agent fundamentally misunderstood the problem.
- **Bonus** (additive): convention adherence, edge cases, style. Differentiates great from passing.

### Wall-Clock Timeout
Hard cap at 45 minutes, with early-exit on token exhaustion. Most runs hit token budget first. The cap is a safety valve against pathological retry loops. Wall-clock time reported as a secondary metric.

### Language Strategy
Python-primary (2 repos) + TypeScript (1 repo). Scored and reported separately (`score_python`, `score_typescript`, `score_combined`). Python + TS are both high-fluency for all major agents — neither confounds the measurement. Divergence between languages signals language-specific harness effects.

## Task Attribute Framework

Seven independent dimensions that control what a task demands from the agent:

### Retrieval Difficulty (can the agent FIND the right information?)

| Attribute | Low | High |
|-----------|-----|------|
| **Scatter** | All relevant files in one directory | Relevant files spread across 6+ directories |
| **Signal-to-Noise** | 75%+ of repo files are relevant | <10% of repo files are relevant |
| **Red Herring Density** | Few misleading files | Many files with similar names/structure that aren't relevant |

### Reasoning Difficulty (once found, can the agent THINK correctly?)

| Attribute | Low | High |
|-----------|-----|------|
| **Inferential Distance** | Issue describes the fix | Issue is a symptom; cause is 4+ hops away |
| **Pattern Ambiguity** | One example gives the pattern | 3+ variants; which applies depends on classification |
| **Coupling Depth** | Change A → change B (direct) | Change A → B → C → D (transitive chain) |
| **Spec Completeness** | "Add field X to model Y" | "Users need rate limiting" (requirements inferred from code) |

### Attribute Profiles by Task Type

| | Scatter | SNR | Red Herrings | Inf. Distance | Ambiguity | Coupling | Spec |
|---|---|---|---|---|---|---|---|
| **T1** Invariant Prop. | High | Med | Low | Short | Med | Deep | Under |
| **T2** Causal Debug | High | Low | Dense | Long | Low | Deep | Symptom |
| **T3** Convention Inf. | Med | Med | Med | Short | High | Shallow | Under |
| **T4** Semantic Migr. | Low | High | Low | Short | Med | Shallow | Full |
| **T5** Spec-Gap | Med | Med | Low | Med | Med | Med | Partial |
| **T6** Adversarial Ret. | High | Low | Dense | Med | Low | Med | Full |

### Harness-Sensitive Attributes

These are the axes where "agent + harness" should outperform "naked agent":

1. **SNR** — does CLAUDE.md help the agent ignore noise?
2. **Scatter** — does project structure documentation help navigation?
3. **Spec Completeness** — does memory/conventions fill in what the issue doesn't say?
4. **Pattern Ambiguity** — do exemplars in context help pick the right variant?

The others (inferential distance, coupling depth) test raw model capability regardless of harness. Both categories must be represented so results show harness lift vs. model ceiling independently.
