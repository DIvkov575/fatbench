"""SWE-bench-style scoring: a single `resolved` bit.

An instance is RESOLVED iff, after applying the agent's patch and overlaying the PR's real tests:
  - every FAIL_TO_PASS test passes (here: `gate_tests` — the tests the PR added/changed), AND
  - every PASS_TO_PASS test still passes (here: the `regression_command` suite).

That's it. No file-overlap, no precision, no efficiency, no composite. The benchmark metric across
a task set is the resolved rate (fraction of instances resolved), exactly as in SWE-bench.

`tokens_consumed` is recorded as metadata (like SWE-bench's cost reporting) but is NOT scored —
context bloat therefore affects the result only if it degrades the patch enough to fail tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Scores:
    resolved: bool                 # the SWE-bench bit: FAIL_TO_PASS all pass AND PASS_TO_PASS all pass
    fail_to_pass: bool             # all gate_tests passed
    pass_to_pass: bool             # regression suite passed
    fail_to_pass_detail: str       # "N/M" gate tests passed
    ran: bool                      # tests actually executed (False -> resolved is meaningless)
    tokens_consumed: int           # metadata only, not scored
    notes: list[str]


def compute_scores(
    *,
    gates_total: int,
    gates_passed: int,
    gates_ran: bool,
    regression: float,             # 1.0 pass, 0.0 fail, -1.0 did-not-run
    tokens_consumed: int,
) -> Scores:
    notes: list[str] = []

    if not gates_ran or regression < 0:
        notes.append("Tests did not run (no evaluator/container) — `resolved` is not meaningful.")
        return Scores(
            resolved=False, fail_to_pass=False, pass_to_pass=False,
            fail_to_pass_detail=f"{gates_passed}/{gates_total} (not run)",
            ran=False, tokens_consumed=tokens_consumed, notes=notes,
        )

    fail_to_pass = gates_total > 0 and gates_passed == gates_total
    pass_to_pass = regression >= 1.0
    resolved = fail_to_pass and pass_to_pass

    return Scores(
        resolved=resolved,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        fail_to_pass_detail=f"{gates_passed}/{gates_total}",
        ran=True,
        tokens_consumed=tokens_consumed,
        notes=notes,
    )
