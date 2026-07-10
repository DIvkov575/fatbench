"""scorer tests: SWE-bench `resolved` bit = FAIL_TO_PASS all pass AND PASS_TO_PASS all pass."""

from harness.scorer import compute_scores


def _score(total, passed, gates_ran=True, regression=1.0, tokens=100_000):
    return compute_scores(gates_total=total, gates_passed=passed, gates_ran=gates_ran,
                          regression=regression, tokens_consumed=tokens)


def test_resolved_when_all_gates_and_regression_pass():
    s = _score(5, 5, regression=1.0)
    assert s.resolved and s.fail_to_pass and s.pass_to_pass
    assert s.fail_to_pass_detail == "5/5" and s.ran


def test_not_resolved_when_a_gate_fails():
    s = _score(5, 4, regression=1.0)
    assert not s.resolved and not s.fail_to_pass and s.pass_to_pass
    assert s.fail_to_pass_detail == "4/5"


def test_not_resolved_when_regression_fails():
    # FAIL_TO_PASS all pass but PASS_TO_PASS broke -> not resolved (SWE-bench requires both).
    s = _score(5, 5, regression=0.0)
    assert not s.resolved and s.fail_to_pass and not s.pass_to_pass


def test_no_gates_is_not_resolved():
    s = _score(0, 0, regression=1.0)
    assert not s.resolved and not s.fail_to_pass


def test_tests_did_not_run_is_not_meaningful():
    s = _score(5, 0, gates_ran=False, regression=-1.0)
    assert not s.resolved and not s.ran
    assert any("did not run" in n for n in s.notes)


def test_tokens_are_metadata_only_not_scored():
    # Same resolved verdict regardless of token count — bloat doesn't touch the bit.
    a = _score(3, 3, regression=1.0, tokens=50_000)
    b = _score(3, 3, regression=1.0, tokens=5_000_000)
    assert a.resolved == b.resolved is True
    assert a.tokens_consumed != b.tokens_consumed
