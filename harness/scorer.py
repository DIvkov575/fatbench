"""Scoring: the SWE-bench `resolved` bit + separate retrieval diagnostics.

TWO INDEPENDENT METRIC GROUPS, kept separate on purpose (no blend, no composite):

1. VERDICT (the benchmark metric) — SWE-bench's single bit. `resolved` iff, after applying the
   agent's patch and overlaying the PR's real tests:
     - every FAIL_TO_PASS test passes (`gate_tests`), AND
     - every PASS_TO_PASS test still passes (`regression_command`).
   Nothing else touches this bit.

2. RETRIEVAL DIAGNOSTICS (interpretability only, NOT part of the verdict) — file-overlap of the
   agent's implementation vs the gold PR's files. Two `resolved: false` runs look identical in the
   verdict but completeness/precision tell you *why*: "touched 7/9 gold files, missed the event
   layer" (close) vs "touched 2 unrelated files" (lost). This is also where context bloat would
   show up first — if it derails retrieval, completeness drops before correctness does.

     completeness = |agent_impl ∩ gold| / |gold|    (migration-aware)
     precision    = |agent_impl ∩ gold| / |agent_impl|

`tokens_consumed` is recorded as metadata (like SWE-bench cost reporting), not scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .diffutil import is_migration_file


# ---- retrieval diagnostics (file overlap) -------------------------------------------------

@dataclass
class FileMetrics:
    completeness: float
    precision: float
    matched: list[str]
    missed: list[str]      # gold files the agent didn't touch
    extra: list[str]       # agent impl files not in gold
    migration_expected: bool
    migration_satisfied: bool


def _split_migrations(paths: list[str]) -> tuple[set[str], bool]:
    non_mig = {p for p in paths if not is_migration_file(p)}
    has_mig = any(is_migration_file(p) for p in paths)
    return non_mig, has_mig


def score_files(agent_impl_files: list[str], gold_patch_files: list[str]) -> FileMetrics:
    """Completeness/precision with migration files collapsed to one synthetic slot.

    Diagnostics only — this does NOT feed `resolved`.
    """
    gold_non_mig, gold_has_mig = _split_migrations(gold_patch_files)
    agent_non_mig, agent_has_mig = _split_migrations(agent_impl_files)

    matched = sorted(gold_non_mig & agent_non_mig)
    missed = sorted(gold_non_mig - agent_non_mig)

    gold_slots = len(gold_non_mig) + (1 if gold_has_mig else 0)
    matched_slots = len(matched)
    migration_satisfied = gold_has_mig and agent_has_mig
    if migration_satisfied:
        matched_slots += 1
    if gold_has_mig and not agent_has_mig:
        missed = missed + ["<a migration under zerver/migrations/>"]

    completeness = matched_slots / gold_slots if gold_slots else 0.0

    agent_slots = len(agent_non_mig) + (1 if agent_has_mig else 0)
    correct_agent_slots = len(matched) + (1 if migration_satisfied else 0)
    precision = correct_agent_slots / agent_slots if agent_slots else 0.0

    extra = sorted(agent_non_mig - gold_non_mig)
    if agent_has_mig and not gold_has_mig:
        extra = extra + ["<a migration under zerver/migrations/>"]

    return FileMetrics(
        completeness=round(completeness, 4),
        precision=round(precision, 4),
        matched=matched,
        missed=missed,
        extra=extra,
        migration_expected=gold_has_mig,
        migration_satisfied=migration_satisfied,
    )


# ---- verdict + diagnostics bundled for recording ------------------------------------------

@dataclass
class Scores:
    # --- verdict (the benchmark metric): SWE-bench resolved bit ---
    resolved: bool                 # FAIL_TO_PASS all pass AND PASS_TO_PASS all pass
    fail_to_pass: bool             # all gate_tests passed
    pass_to_pass: bool             # regression suite passed
    fail_to_pass_detail: str       # "N/M" gate tests passed
    ran: bool                      # tests actually executed (False -> resolved meaningless)
    # --- retrieval diagnostics (separate metrics, NOT part of the verdict) ---
    completeness: float            # |agent_impl ∩ gold| / |gold|
    precision: float               # |agent_impl ∩ gold| / |agent_impl|
    file_detail: dict              # matched / missed / extra / migration flags
    # --- metadata ---
    tokens_consumed: int           # not scored
    notes: list[str] = field(default_factory=list)


def compute_scores(
    *,
    gates_total: int,
    gates_passed: int,
    gates_ran: bool,
    regression: float,             # 1.0 pass, 0.0 fail, -1.0 did-not-run
    file_metrics: FileMetrics,
    tokens_consumed: int,
) -> Scores:
    notes: list[str] = []

    if not gates_ran or regression < 0:
        notes.append("Tests did not run (no evaluator/container) — `resolved` is not meaningful. "
                     "Retrieval diagnostics (completeness/precision) are still valid.")
        resolved = fail_to_pass = pass_to_pass = False
        ran = False
        detail = f"{gates_passed}/{gates_total} (not run)"
    else:
        fail_to_pass = gates_total > 0 and gates_passed == gates_total
        pass_to_pass = regression >= 1.0
        resolved = fail_to_pass and pass_to_pass
        ran = True
        detail = f"{gates_passed}/{gates_total}"

    return Scores(
        resolved=resolved,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        fail_to_pass_detail=detail,
        ran=ran,
        completeness=file_metrics.completeness,
        precision=file_metrics.precision,
        file_detail={
            "matched": file_metrics.matched,
            "missed": file_metrics.missed,
            "extra": file_metrics.extra,
            "migration_expected": file_metrics.migration_expected,
            "migration_satisfied": file_metrics.migration_satisfied,
        },
        tokens_consumed=tokens_consumed,
        notes=notes,
    )
