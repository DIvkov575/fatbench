"""Multi-axis scoring (LLD.md §5).

    completeness = |agent ∩ gold| / |gold|       (migration-aware)
    precision    = |agent ∩ gold| / |agent|
    correctness  = all gate_tests pass ? fraction passing : 0   (hard gate)
    regression   = pre-existing suite still passes ? 1 : 0
    efficiency   = composite / tokens_consumed   (then reported per-Mtoken)
    composite    = 0.35*correctness + 0.25*completeness + 0.15*precision
                 + 0.15*regression + 0.10*efficiency_norm

File metrics use the agent's IMPLEMENTATION files only (test files it touched are ignored —
only the PR's own tests gate correctness). Gold migration files (gold-specific names) collapse
into a single synthetic slot satisfied by "agent added >=1 migration".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .diffutil import is_migration_file

# Composite weights (LLD.md §5 / HLD.md).
W_CORRECTNESS = 0.35
W_COMPLETENESS = 0.25
W_PRECISION = 0.15
W_REGRESSION = 0.15
W_EFFICIENCY = 0.10


@dataclass
class FileMetrics:
    completeness: float
    precision: float
    matched: list[str]
    missed: list[str]      # gold files the agent didn't touch
    extra: list[str]       # agent impl files not in gold (precision drag)
    migration_expected: bool
    migration_satisfied: bool


def _split_migrations(paths: list[str]) -> tuple[set[str], bool]:
    """Return (non-migration paths, any-migration-present)."""
    non_mig = {p for p in paths if not is_migration_file(p)}
    has_mig = any(is_migration_file(p) for p in paths)
    return non_mig, has_mig


def score_files(agent_impl_files: list[str], gold_patch_files: list[str]) -> FileMetrics:
    """Completeness/precision with migration files collapsed to one synthetic slot."""
    gold_non_mig, gold_has_mig = _split_migrations(gold_patch_files)
    agent_non_mig, agent_has_mig = _split_migrations(agent_impl_files)

    matched = sorted(gold_non_mig & agent_non_mig)
    missed = sorted(gold_non_mig - agent_non_mig)

    # Denominator for completeness: distinct non-migration gold files + 1 if gold has migrations.
    gold_slots = len(gold_non_mig) + (1 if gold_has_mig else 0)
    matched_slots = len(matched)
    migration_satisfied = gold_has_mig and agent_has_mig
    if migration_satisfied:
        matched_slots += 1
    if gold_has_mig and not agent_has_mig:
        missed = missed + ["<a migration under zerver/migrations/>"]

    completeness = matched_slots / gold_slots if gold_slots else 0.0

    # Precision: of the agent's impl files, how many are "in gold"? A migration counts as
    # a correct hit when gold also expected a migration.
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


def score_correctness(gates_total: int, gates_passed: int, ran: bool) -> float:
    """Hard gate: 0 unless ALL gate tests pass. If tests didn't run, returns -1 (unknown)."""
    if not ran:
        return -1.0
    if gates_total == 0:
        return 0.0
    if gates_passed < gates_total:
        return 0.0
    return round(gates_passed / gates_total, 4)  # == 1.0 when all pass


def efficiency_norm(composite_pre_eff: float, tokens_consumed: int) -> float:
    """Score achieved per million tokens, clamped to [0,1].

    composite_pre_eff is the composite computed with the efficiency term set to 0 and
    reweighted to the remaining 0.90; this avoids a circular definition.
    """
    if tokens_consumed <= 0:
        return 0.0
    per_mtoken = composite_pre_eff / (tokens_consumed / 1_000_000)
    return round(min(per_mtoken, 1.0), 4)


@dataclass
class Scores:
    completeness: float
    precision: float
    correctness: float       # -1.0 means tests did not run (unknown)
    regression: float        # -1.0 means tests did not run (unknown)
    efficiency: float
    composite: float
    tokens_consumed: int
    file_detail: dict
    notes: list[str]


def compute_scores(
    *,
    file_metrics: FileMetrics,
    correctness: float,
    regression: float,
    tokens_consumed: int,
) -> Scores:
    notes: list[str] = []

    # Treat "unknown" (tests didn't run) as 0 for the composite, but flag it.
    corr_eff = correctness if correctness >= 0 else 0.0
    reg_eff = regression if regression >= 0 else 0.0
    if correctness < 0 or regression < 0:
        notes.append(
            "Test execution did not run (no evaluator/container); correctness and "
            "regression are UNKNOWN and treated as 0 in the composite. File metrics are valid."
        )

    # composite without the efficiency term, reweighted to 0.90, for the efficiency calc.
    composite_pre_eff = (
        W_CORRECTNESS * corr_eff
        + W_COMPLETENESS * file_metrics.completeness
        + W_PRECISION * file_metrics.precision
        + W_REGRESSION * reg_eff
    ) / (1 - W_EFFICIENCY)

    eff = efficiency_norm(composite_pre_eff, tokens_consumed)

    composite = (
        W_CORRECTNESS * corr_eff
        + W_COMPLETENESS * file_metrics.completeness
        + W_PRECISION * file_metrics.precision
        + W_REGRESSION * reg_eff
        + W_EFFICIENCY * eff
    )

    return Scores(
        completeness=file_metrics.completeness,
        precision=file_metrics.precision,
        correctness=round(correctness, 4),
        regression=round(regression, 4),
        efficiency=eff,
        composite=round(composite, 4),
        tokens_consumed=tokens_consumed,
        file_detail=asdict(file_metrics),
        notes=notes,
    )
