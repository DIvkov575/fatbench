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


import subprocess as _sp

from harness.evaluator import RemoteContainerEvaluator


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_stage_applies_impl_then_gold(monkeypatch):
    """stage() must reset, apply impl diff, then overlay gold tests — in that order."""
    calls = []
    ev = ContainerEvaluator(parent_commit="abc123")
    ev.runtime = "docker"

    def fake_exec(cmd, stdin=None):
        calls.append(("sh", cmd, stdin))
        return _FakeProc(returncode=0)

    # Stub _exec (all container commands) and _put (verified write, tested separately).
    monkeypatch.setattr(ev, "_exec", fake_exec)
    monkeypatch.setattr(ev, "_put", lambda text, dest: calls.append(("put", dest, text)))
    ok, msg = ev.stage("IMPL_DIFF", "GOLD_DIFF")
    assert ok, msg
    scripts = [c[1] for c in calls]
    joined = "\n".join(scripts)
    # reset to parent happens, venv re-syncs, then both diffs are put + applied
    assert "git reset --hard abc123" in joined
    assert "uv sync --frozen --group dev --inexact" in joined
    assert "git apply /tmp/impl.diff" in joined
    assert "git apply /tmp/gold-tests.diff" in joined
    puts = [c[2] for c in calls if c[0] == "put"]
    assert "IMPL_DIFF" in puts and "GOLD_DIFF" in puts
    # ordering: reset -> sync -> impl apply
    reset_i = next(i for i, s in enumerate(scripts) if "git reset" in s)
    sync_i = next(i for i, s in enumerate(scripts) if "uv sync" in s)
    impl_i = next(i for i, s in enumerate(scripts) if "git apply /tmp/impl.diff" in s)
    assert reset_i < sync_i < impl_i


def test_stage_sync_can_be_disabled():
    ev = ContainerEvaluator(parent_commit="abc123", sync_venv=False)
    ev.runtime = "docker"
    calls = []
    ev._exec = lambda cmd, stdin=None: (calls.append(cmd) or _FakeProc(returncode=0))
    ev._put = lambda text, dest: None
    ok, _ = ev.stage("IMPL", "")
    assert ok and not any("uv sync" in c for c in calls)


def test_stage_fails_if_sync_fails():
    ev = ContainerEvaluator(parent_commit="abc123")
    ev.runtime = "docker"

    def fake_exec(cmd, stdin=None):
        rc = 1 if "uv sync" in cmd else 0
        return _FakeProc(stderr="lockfile mismatch" if rc else "", returncode=rc)

    ev._exec = fake_exec
    ev._put = lambda text, dest: None
    ok, msg = ev.stage("IMPL", "GOLD")
    assert not ok and "venv sync failed" in msg


def test_run_tests_passes_skip_provision_check(monkeypatch):
    ev = ContainerEvaluator(parent_commit="abc123")
    ev.runtime = "docker"
    seen = {}
    monkeypatch.setattr(ev, "_run_in_env", lambda inner: seen.setdefault("inner", inner) or
                        __import__("harness.evaluator", fromlist=["TestRunResult"]).TestRunResult(
                            ran=True, passed=1, failed=0, total=1, ok=True))
    ev.run_tests(Path("/x"), ["zerver/tests/test_realm.py::RealmAPITest"])
    assert "--skip-provision-check" in seen["inner"]
    assert "test_realm.RealmAPITest" in seen["inner"]


def test_run_command_injects_skip_provision_check():
    ev = ContainerEvaluator(parent_commit="abc123")
    ev.runtime = "docker"
    seen = {}
    ev._run_in_env = lambda inner: seen.setdefault("inner", inner)
    ev.run_command(Path("/x"), "./tools/test-backend zerver.tests.test_realm")
    assert "./tools/test-backend --skip-provision-check zerver.tests.test_realm" in seen["inner"]
    # non-test-backend commands pass through untouched
    seen.clear()
    ev.run_command(Path("/x"), "echo hi")
    assert seen["inner"] == "echo hi"


def test_stage_fails_without_parent_commit():
    ev = ContainerEvaluator()  # no parent_commit
    ev.runtime = "docker"
    ok, msg = ev.stage("x", "y")
    assert not ok and "parent_commit" in msg


def test_put_verifies_byte_count(monkeypatch):
    """_put must confirm the written byte count matches; retry then raise on persistent mismatch."""
    ev = ContainerEvaluator(parent_commit="abc")
    ev.runtime = "docker"
    # _host_sh is the write; _exec is the verify (wc -c). Simulate a short write forever.
    ev._host_sh = lambda script, stdin=None: _FakeProc(returncode=0)
    ev._exec = lambda cmd, stdin=None: _FakeProc(stdout="5\n")  # always reports 5 bytes
    import pytest
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        ev._put("HELLO_WORLD", "/tmp/x.diff")  # 11 bytes != 5


def test_put_succeeds_when_bytecount_matches():
    ev = ContainerEvaluator(parent_commit="abc")
    ev.runtime = "docker"
    ev._host_sh = lambda script, stdin=None: _FakeProc(returncode=0)
    ev._exec = lambda cmd, stdin=None: _FakeProc(stdout="11\n")
    ev._put("HELLO_WORLD", "/tmp/x.diff")  # 11 bytes — no raise


def test_stage_clears_stale_staging_files():
    """stage() must rm the /tmp diffs before writing (snapshot may carry a prior task's diffs)."""
    ev = ContainerEvaluator(parent_commit="abc123", sync_venv=False)
    ev.runtime = "docker"
    calls = []
    ev._host_sh = lambda script, stdin=None: (calls.append(script) or _FakeProc(returncode=0))
    ev._exec = lambda cmd, stdin=None: (calls.append(cmd) or _FakeProc(stdout="4\n", returncode=0))
    ev._put = lambda text, dest: calls.append(f"PUT {dest}")
    ev.stage("IMPL", "GOLD")
    rm_i = next(i for i, c in enumerate(calls) if "rm -f /tmp/impl.diff" in c)
    reset_i = next(i for i, c in enumerate(calls) if "git reset" in c)
    assert rm_i < reset_i  # cleared before reset/apply


def test_remote_host_sh_retries_transient(monkeypatch):
    """RemoteContainerEvaluator retries WSSH/transport failures but not real command errors."""
    ev = RemoteContainerEvaluator(host="h")
    seq = [_FakeProc(stderr="WSSH Proxy getaddrinfo ENOTFOUND", returncode=255),
           _FakeProc(stdout="ok", returncode=0)]
    calls = {"n": 0}

    def fake_run(argv, input=None, capture_output=None, text=None):
        i = calls["n"]; calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(_sp, "run", fake_run)
    r = ev._host_sh("docker ps")
    assert r.returncode == 0 and calls["n"] == 2  # retried once past the WSSH blip

    # real error (non-transient) is NOT retried
    calls["n"] = 0
    monkeypatch.setattr(_sp, "run",
                        lambda *a, **k: _FakeProc(stderr="fatal: bad object", returncode=128))
    r2 = ev._host_sh("git thing")
    assert r2.returncode == 128


def test_remote_evaluator_wraps_in_ssh(monkeypatch):
    """RemoteContainerEvaluator._host_sh must shell out via ssh to the given host."""
    captured = {}

    def fake_run(argv, input=None, capture_output=None, text=None):
        captured["argv"] = argv
        captured["stdin"] = input
        return _FakeProc(returncode=0)

    monkeypatch.setattr(_sp, "run", fake_run)
    ev = RemoteContainerEvaluator(host="dev-dsk.example.com")
    ev._host_sh("docker ps", stdin="data")
    assert captured["argv"][0] == "ssh"
    assert "dev-dsk.example.com" in captured["argv"]
    assert captured["argv"][-1] == "docker ps"
    assert captured["stdin"] == "data"
