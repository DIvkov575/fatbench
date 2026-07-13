# SWE-bench Lite three-arm comparison: bare vs. raw vs. mine

**Question:** what does a Claude Code configuration buy you on a standard benchmark, and at what
token/cost? We run the SAME `claude -p` on the SAME 25 SWE-bench Lite instances three ways, graded
by the OFFICIAL swebench Docker oracle (`resolved` = FAIL_TO_PASS ∧ PASS_TO_PASS).

- **Dataset:** `princeton-nlp/SWE-bench_Lite`, first 25 instances (astropy + django only — repo-skewed).
- **N = 25, single seed per arm.** Direction is strong; treat exact rates as a pilot signal.
- **Runs:** raw+mine `results-swebench/20260712-141842/`, bare `results-swebench/20260713-110208/`.
  Canonical numbers: `three-arm-summary.json` (regenerable from the two run dirs).

## The three arms

| arm | what actually loads (verified at runtime, 2026-07-13) |
|-----|-------------------------------------------------------|
| **bare** | `claude --bare`: claude's native tools only (**Bash/Read/Edit**), NO skills/plugins/MCP/rules/CLAUDE.md. ~1.4K-token base context. Shell-first: routes file create/search through Bash (no Write/Grep/Glob/Task). |
| **raw** | isolated empty `CLAUDE_CONFIG_DIR` + `--strict-mcp-config`. Drops the user's plugins/MCP/personal CLAUDE.md — BUT the Amazon toolbox bootstrap re-injects its 14 built-in skills (deep-research, dataviz, code-review, verify, run, …) + amazon global rules on every startup. ~9.4K-token base. **NOT actually bare** (this was a mislabel in the original pilot). |
| **mine** | the user's real setup: plugins, skills, MCP servers, hooks, CLAUDE.md all load as in normal use. ~40K+-token base. |

## Results

| arm | resolved | rate | total cost | avg tokens | avg turns | billed tok/turn |
|-----|---------:|-----:|-----------:|-----------:|----------:|----------------:|
| bare | 17/25 | 68% | $17.90 | 42,152 | 30.5 | 1,383 |
| raw  | 18/25 | 72% | $16.89 | 30,544 | 19.5 | 1,565 |
| **mine** | **24/25** | **96%** | $45.16 | 99,949 | 32.8 | 3,044 |

Grand total across all three arms: **$79.95**.

## Findings

1. **The resolved sets nest almost perfectly: 17 ⊂ 18 ⊂ 24.** All 17 bare-solved are also solved by
   raw and mine. No arm loses a solve the leaner arm had (`regressions_*` both empty). So richer
   config is a strict Pareto improvement here — it never broke anything, only added.

2. **bare vs raw ≈ noise (68% vs 72%, a 1-instance difference).** Claude's built-in skills + amazon
   rules bought exactly one extra solve (`astropy-14365`) — unsurprising, since deep-research /
   dataviz / code-review don't target Python bugfixing.

3. **The entire lift is the user's plugins/MCP/hooks (raw → mine): +6 solves, 72% → 96%.** That is
   the real signal, and it is clean (mine ⊃ raw). The 6 added: `astropy-7746`, `django-10924`,
   `django-11019`, `django-11283`, `django-11564`, `django-11742`.

4. **mine cost ~2.7× for that lift.** One blemish: mine produced an empty patch on `django-11630`
   (1 turn, aborted early) — a config interaction worth investigating; bare engaged that instance
   fully (124K tokens, 83 turns).

## Why mine burns ~3.3× the tokens (99.9K vs 30.5K avg)

Two multiplicative effects:

- **Bigger per-turn context (dominant).** Every turn re-sends the full loadout. Billed tokens PER
  TURN: bare 1,383 · raw 1,565 · **mine 3,044** — ~2× raw. The overhead is the plugin/skill/MCP
  **tool-definition schemas** + rules + CLAUDE.md, paid on every turn whether or not they're used
  on a Python bugfix. `cache_creation` confirms it: mine writes 60.5K context tokens vs raw's 24.8K.
- **More turns.** mine 32.8 vs raw 19.5 (+68%) — it explores more before committing.

~2× per turn × ~1.7× turns ≈ the 3.3× total. **Biggest lever = the tool schemas** from connected
MCP servers/plugins irrelevant to coding. Open experiment: a `mine`-minus-MCP arm (skills/hooks/
CLAUDE.md kept, `--strict-mcp-config` to drop MCP schemas) to test whether 96% holds at lower cost.

## Methodology note (why "raw" was renamed-in-spirit, and "bare" added)

The original 2-arm pilot labeled the isolated-config arm "raw" / "vanilla claude." Runtime probing
showed that was wrong: the toolbox repopulates a fresh config dir with skills + rules. `--bare` is
the only switch that yields a genuinely stripped agent, and it authenticates via the Bedrock
credential export in `--settings` despite its API-key default. Hence three arms: bare (truly
minimal) · raw (native skills + org rules, no user plugins/MCP) · mine (everything).
