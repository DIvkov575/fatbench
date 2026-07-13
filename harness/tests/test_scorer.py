"""scorer tests: SWE-bench `resolved` verdict + separate retrieval diagnostics.

Verdict and diagnostics are independent metric groups — a test asserts they don't influence
each other.
"""

from harness.scorer import Scores, compute_scores, score_files

GOLD = [
    "zerver/models/realms.py",
    "zerver/actions/realm_settings.py",
    "zerver/lib/events.py",
    "zerver/migrations/0710_realm_topics_policy.py",  # migration -> one synthetic slot
]


def _fm(agent_files):
    return score_files(agent_files, GOLD)


def _score(total, passed, gates_ran=True, regression=1.0, agent_files=None, tokens=100_000):
    fm = _fm(agent_files if agent_files is not None else GOLD)
    return compute_scores(gates_total=total, gates_passed=passed, gates_ran=gates_ran,
                          regression=regression, file_metrics=fm, tokens_consumed=tokens)


# --- verdict (resolved bit) ----------------------------------------------------------------

def test_resolved_when_all_gates_and_regression_pass():
    s = _score(5, 5, regression=1.0)
    assert s.resolved and s.fail_to_pass and s.pass_to_pass
    assert s.fail_to_pass_detail == "5/5" and s.ran


def test_not_resolved_when_a_gate_fails():
    s = _score(5, 4, regression=1.0)
    assert not s.resolved and not s.fail_to_pass and s.pass_to_pass


def test_not_resolved_when_regression_fails():
    s = _score(5, 5, regression=0.0)
    assert not s.resolved and s.fail_to_pass and not s.pass_to_pass


def test_tests_did_not_run_is_not_meaningful():
    s = _score(5, 0, gates_ran=False, regression=-1.0)
    assert not s.resolved and not s.ran
    assert any("did not run" in n for n in s.notes)


# --- retrieval diagnostics (file overlap) --------------------------------------------------

def test_perfect_overlap_with_differently_named_migration():
    agent = [
        "zerver/models/realms.py", "zerver/actions/realm_settings.py", "zerver/lib/events.py",
        "zerver/migrations/9999_totally_different_name.py",  # counts via migration slot
    ]
    fm = _fm(agent)
    assert fm.completeness == 1.0 and fm.precision == 1.0
    assert fm.migration_satisfied


def test_missing_layer_lowers_completeness_only():
    agent = ["zerver/models/realms.py", "zerver/actions/realm_settings.py"]  # missing events + migration
    fm = _fm(agent)
    assert fm.completeness < 1.0
    assert "zerver/lib/events.py" in fm.missed


def test_extra_files_lower_precision_only():
    agent = GOLD + ["zerver/lib/streams.py"]  # a red-herring the agent wrongly touched
    fm = _fm(agent)
    assert fm.completeness == 1.0        # still hit everything gold wanted
    assert fm.precision < 1.0            # but touched an extra
    assert "zerver/lib/streams.py" in fm.extra


# --- independence: diagnostics never move the verdict, tokens never scored -----------------

def test_diagnostics_do_not_affect_resolved():
    # Terrible file overlap but all tests pass -> still resolved. Verdict ignores diagnostics.
    s = _score(3, 3, regression=1.0, agent_files=["totally/unrelated.py"])
    assert s.resolved is True
    assert s.completeness == 0.0  # diagnostics reflect the bad overlap, verdict doesn't care


def test_tokens_are_metadata_only():
    a = _score(3, 3, tokens=50_000)
    b = _score(3, 3, tokens=5_000_000)
    assert a.resolved == b.resolved is True
    assert a.tokens_consumed != b.tokens_consumed
