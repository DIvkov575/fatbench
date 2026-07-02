"""evaluator tests: node-id translation, output parsing, NullEvaluator, runtime detection."""

from pathlib import Path

from harness import evaluator
from harness.evaluator import (
    ContainerEvaluator,
    NullEvaluator,
    detect_container_runtime,
    nodeid_to_dotted,
    parse_test_backend_output,
)


def test_nodeid_to_dotted():
    # test-backend cannot run a single method (validated on a real container 2026-07-01),
    # so a method component collapses to its class.
    assert (nodeid_to_dotted("zerver/tests/test_realm.py::RealmAPITest::test_x")
            == "zerver.tests.test_realm.RealmAPITest")
    assert (nodeid_to_dotted("zerver/tests/test_realm.py::RealmAPITest")
            == "zerver.tests.test_realm.RealmAPITest")  # class level unchanged
    assert (nodeid_to_dotted("zerver/tests/test_events.py")
            == "zerver.tests.test_events")
    assert (nodeid_to_dotted("zerver.tests.test_realm")
            == "zerver.tests.test_realm")  # already dotted module, passthrough
    assert (nodeid_to_dotted("zerver.tests.test_realm.RealmAPITest.test_x")
            == "zerver.tests.test_realm.RealmAPITest")  # dotted method collapses too


def test_parse_import_crash_fail_side():
    # Real FAIL-side output: gold tests applied without impl -> collection ImportError,
    # no "Ran N" summary, non-zero exit. Must read as not-ok.
    out = (
        "Found 20 test(s).\nTraceback (most recent call last):\n"
        "ImportError: cannot import name 'RealmTopicsPolicyEnum' from 'zerver.models.realms'\n"
    )
    r = parse_test_backend_output(out, "", 1)
    assert r.ran and not r.ok


def test_parse_failures_and_errors_combined():
    # Real FAIL-side (class-level) summary: "FAILED (failures=1, errors=34)" over 20 tests.
    out = "Ran 20 tests in 8.4s\n\nFAILED (failures=1, errors=34)\n"
    r = parse_test_backend_output(out, "", 1)
    assert r.ran and not r.ok
    assert r.total == 20 and r.failed == 35 and r.passed == 0


def test_parse_ok_run():
    out = "test_a ... ok\ntest_b ... ok\n----\nRan 2 tests in 1.2s\n\nOK\n"
    r = parse_test_backend_output(out, "", 0)
    assert r.ran and r.ok
    assert r.total == 2 and r.passed == 2 and r.failed == 0


def test_parse_failures():
    out = (
        "FAIL: test_invalid_topics_policy (zerver.tests.test_realm.RealmAPITest)\n"
        "Ran 5 tests in 3.0s\n\nFAILED (failures=1)\n"
    )
    r = parse_test_backend_output(out, "", 1)
    assert r.ran and not r.ok
    assert r.total == 5 and r.failed == 1 and r.passed == 4
    assert any("test_invalid_topics_policy" in f for f in r.failures)


def test_parse_errors_count():
    out = "Ran 3 tests in 0.5s\n\nFAILED (errors=2)\n"
    r = parse_test_backend_output(out, "", 1)
    assert r.failed == 2 and r.passed == 1


def test_parse_no_summary_uses_returncode():
    r_ok = parse_test_backend_output("provision crashed", "boom", 0)
    assert r_ok.ok
    r_bad = parse_test_backend_output("provision crashed", "boom", 2)
    assert not r_bad.ok


def test_null_evaluator():
    ev = NullEvaluator()
    r = ev.run_tests(Path("/tmp"), ["a", "b"])
    assert not r.ran
    assert r.total == 2
    cmd = ev.run_command(Path("/tmp"), "echo hi")
    assert not cmd.ran


def test_container_setup_raises_without_runtime(monkeypatch):
    monkeypatch.setattr(evaluator, "detect_container_runtime", lambda: None)
    ev = ContainerEvaluator()
    ev.runtime = None
    try:
        ev.setup(Path("/tmp"))
        assert False, "should raise EnvironmentError"
    except EnvironmentError as e:
        assert "container runtime" in str(e)


def test_detect_runtime_returns_none_or_str():
    # On this host: None. Just assert the contract.
    rt = detect_container_runtime()
    assert rt is None or isinstance(rt, str)
