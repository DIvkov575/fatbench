"""Test-execution seam.

Grading correctness/regression requires running Zulip's backend tests, which need a full
Linux service stack (Postgres/Redis/RabbitMQ/memcached) — container only. This module isolates
that behind the `Evaluator` interface so the rest of the pipeline runs anywhere.

- ContainerEvaluator: runs `./tools/test-backend` inside a PRE-PROVISIONED Zulip container
  (the `fatbench/zulip-provisioned:zulip-001` snapshot). Validated on a real Zulip stack
  (2026-07-01). Stages via diffs (reset → apply impl → overlay gold tests) so the provisioned
  venv/DB survive; starts the service stack by hand (no init system in the container).
- RemoteContainerEvaluator: same, but drives docker on a remote host over SSH (the docker
  daemon + snapshot live on the Cloud Desktop; the harness runs on macOS).
- NullEvaluator: skips execution, returns ran=False. Lets the full pipeline + file metrics run
  on a host with no container runtime.

`run_tests` returns gate results. test ids may be pytest node-ids
(`zerver/tests/test_realm.py::Cls::test_x`) from the task YAML; we translate to test-backend's
dotted paths.

IMPORTANT (validated on a real Zulip container, 2026-07-01): test-backend's Django test loader
accepts only MODULE (`zerver.tests.test_realm`) or CLASS (`zerver.tests.test_realm.Cls`) labels.
A method-level dotted label (`...Cls.test_x`) is fed raw to `__import__` and dies with
"is not a package". So we collapse any method component down to the class — the finest
granularity test-backend can actually run. Gate node ids should be pinned at class or module
level in task YAML; a method-level id runs its whole class (a strict superset, still a valid gate).
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestRunResult:
    ran: bool
    passed: int
    failed: int
    total: int
    ok: bool                       # all selected tests passed
    raw_log: str = ""
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def nodeid_to_dotted(test_id: str) -> str:
    """Translate a task-YAML test id to a label test-backend can actually run.

    `zerver/tests/test_realm.py::Cls::test_x` -> `zerver.tests.test_realm.Cls`  (class level)
    `zerver/tests/test_realm.py::Cls`         -> `zerver.tests.test_realm.Cls`
    `zerver/tests/test_events.py`             -> `zerver.tests.test_events`     (module level)

    test-backend cannot run a single method (see module docstring), so a `::method`
    component is dropped — the class runs instead. Already-dotted ids pass through, except
    a trailing `.test_*` method component is likewise stripped to its class.
    """
    test_id = test_id.strip()
    if "::" in test_id:
        file_part, *rest = test_id.split("::")
        mod = file_part.removesuffix(".py").replace("/", ".")
        # Keep at most the class component; drop a method component if present.
        class_part = [rest[0]] if rest else []
        return ".".join([mod, *class_part])
    if test_id.endswith(".py"):
        return test_id.removesuffix(".py").replace("/", ".")
    if "/" in test_id:
        return test_id.replace("/", ".")
    # Already dotted. Drop a trailing method component (`.test_foo`) only when it follows a
    # class component (uppercase-initial), so module names like `...tests.test_realm` survive.
    parts = test_id.split(".")
    if (len(parts) > 2 and parts[-1].startswith("test_") and parts[-2][:1].isupper()):
        return ".".join(parts[:-1])
    return test_id


# test-backend summary line, e.g. "Ran 12 tests ... OK" / "FAILED (failures=2)".
_RAN_RE = re.compile(r"Ran (\d+) test", re.IGNORECASE)
_FAIL_RE = re.compile(r"FAILED \((?:failures=(\d+))?(?:,\s*)?(?:errors=(\d+))?\)")
_FAILLINE_RE = re.compile(r"^(?:FAIL|ERROR):\s+(.+)$", re.MULTILINE)


def parse_test_backend_output(stdout: str, stderr: str, returncode: int) -> TestRunResult:
    """Parse `./tools/test-backend` output into a TestRunResult.

    test-backend wraps Django/unittest, so the unittest summary lines are the signal.
    Falls back to the process return code when the summary can't be found.
    """
    text = stdout + "\n" + stderr
    ran_m = _RAN_RE.search(text)
    total = int(ran_m.group(1)) if ran_m else 0

    fail_m = _FAIL_RE.search(text)
    failures = errors = 0
    if fail_m:
        failures = int(fail_m.group(1) or 0)
        errors = int(fail_m.group(2) or 0)
    failed = failures + errors

    failure_names = _FAILLINE_RE.findall(text)

    # If we couldn't parse a summary, trust the return code.
    if total == 0 and not fail_m:
        ok = returncode == 0
        return TestRunResult(
            ran=True, passed=0, failed=0 if ok else 1, total=0, ok=ok,
            raw_log=text[-8000:], failures=failure_names,
            error=None if ok else "no test summary parsed; using return code",
        )

    ok = (failed == 0) and returncode == 0
    return TestRunResult(
        ran=True, passed=max(total - failed, 0), failed=failed, total=total, ok=ok,
        raw_log=text[-8000:], failures=failure_names,
    )


class Evaluator(ABC):
    @abstractmethod
    def setup(self, workspace_path: Path) -> None: ...

    def stage(self, impl_diff: str, gold_tests_diff: str) -> tuple[bool, str]:
        """Prepare the test environment: reset to parent, apply the agent's impl diff, then
        overlay the gold test files (so the agent's own tests can't pass for it).

        Default no-op for evaluators that test the local workspace in place. Container
        evaluators override this to apply the diffs *inside* the provisioned environment.
        """
        return True, "no-op (in-place workspace)"

    @abstractmethod
    def run_tests(self, workspace_path: Path, test_ids: list[str]) -> TestRunResult: ...

    @abstractmethod
    def run_command(self, workspace_path: Path, command: str) -> TestRunResult:
        """Run a raw shell command (the regression_command) and parse its output."""

    @abstractmethod
    def teardown(self) -> None: ...


class NullEvaluator(Evaluator):
    """No-op: tests are not run. file metrics still scored; correctness/regression = unknown."""

    def setup(self, workspace_path: Path) -> None:
        return None

    def run_tests(self, workspace_path: Path, test_ids: list[str]) -> TestRunResult:
        return TestRunResult(
            ran=False, passed=0, failed=0, total=len(test_ids), ok=False,
            error="NullEvaluator: test execution skipped (no container runtime)",
        )

    def run_command(self, workspace_path: Path, command: str) -> TestRunResult:
        return TestRunResult(
            ran=False, passed=0, failed=0, total=0, ok=False,
            error="NullEvaluator: regression skipped (no container runtime)",
        )

    def teardown(self) -> None:
        return None


def detect_container_runtime() -> str | None:
    """Return the name of an available, working container runtime, or None."""
    for binname in ("docker", "podman"):
        if shutil.which(binname):
            probe = subprocess.run(
                [binname, "info"], capture_output=True, text=True
            )
            if probe.returncode == 0:
                return binname
    return None


class ContainerEvaluator(Evaluator):
    """Run test-backend inside a PRE-PROVISIONED Zulip container.

    Validated on a real Zulip stack (2026-07-01). Assumes an image that already has the venv
    + service stack installed and `/srv/zulip` checked out at the task's parent commit — i.e.
    the `fatbench/zulip-provisioned:zulip-001` snapshot, NOT the bare `zulip/ci` image (which
    would need a ~15-min provision). Flow:

      setup():     start the container, then start the service stack by hand (the bare container
                   has no init system, so postgres/redis/rabbitmq/memcached don't auto-start).
      stage():     inside the container: `git reset --hard <parent>` + clean, RE-SYNC the venv to
                   the parent's lockfile (see below), apply the agent's impl diff, then overlay the
                   gold test files. We ship diffs (never overwrite the tree) so `var/` survives and
                   diffs live in /tmp (outside /srv/zulip, so `git clean` can't wipe them).
      run_tests(): `test-backend --skip-provision-check <class/module labels>` with the venv
                   active; output is written to a file and read back (survives flaky proxies).

    Snapshot-vs-parent skew (validated 2026-07-08 on zulip-002/003): the
    `fatbench/zulip-provisioned:zulip-001` snapshot was provisioned at zulip-001's era, so for a
    NEWER task parent (a) its `var/provision_version` differs -> test-backend aborts unless
    `--skip-provision-check`, and (b) its venv lacks that parent's deps -> imports fail unless we
    `uv sync` to the parent's lockfile first. `stage()` re-syncs after reset (idempotent no-op when
    the parent already matches the snapshot, e.g. zulip-001).

    Local docker. `RemoteContainerEvaluator` overrides the transport to run over SSH.
    """

    runs_tests = True
    DEFAULT_IMAGE = "fatbench/zulip-provisioned:zulip-001"
    WORKDIR = "/srv/zulip"
    VENV = "/srv/zulip/.venv"
    SERVICES = ("postgresql", "redis-server", "rabbitmq-server", "memcached")
    # test-backend flags: skip the provision-version check (snapshot era != task parent era).
    TEST_BACKEND_FLAGS = "--skip-provision-check"
    # Re-sync the venv to the checked-out parent's lockfile before running tests.
    _SYNC_CMD = (f"VIRTUAL_ENV={VENV} UV_PROJECT_ENVIRONMENT={VENV} "
                 "uv sync --frozen --group dev --inexact")
    _LOG = "/tmp/fatbench_tb.log"
    _RC = "/tmp/fatbench_tb.rc"

    def __init__(self, image: str | None = None, runtime: str | None = None,
                 container_name: str = "fatbench-eval", parent_commit: str | None = None,
                 sync_venv: bool = True):
        self.image = image or self.DEFAULT_IMAGE
        self.runtime = runtime
        self.container = container_name
        self.parent_commit = parent_commit
        self.sync_venv = sync_venv

    # --- transport primitives (overridden by the remote subclass) -------------------
    def _host_sh(self, script: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        """Run a bash script on the docker host (here: this machine)."""
        return subprocess.run(
            ["bash", "-c", script], input=stdin, capture_output=True, text=True,
        )

    def _ensure_runtime(self) -> None:
        self.runtime = self.runtime or detect_container_runtime()
        if not self.runtime:
            raise EnvironmentError(
                "No working container runtime found on the docker host. Zulip's backend tests "
                "need a Linux service stack. Run the harness against a host with docker (see "
                "RemoteContainerEvaluator / --remote-host), or use --no-tests for file metrics only."
            )

    # --- helpers on top of the transport --------------------------------------------
    def _exec(self, container_cmd: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        """Run `bash -lc <container_cmd>` inside the container (login shell for PATH)."""
        argv = f"{self.runtime} exec -i {self.container} bash -lc {shlex.quote(container_cmd)}"
        return self._host_sh(argv, stdin=stdin)

    def _put(self, text: str, dest: str) -> None:
        """Pipe `text` into a file at `dest` inside the container, VERIFIED.

        Raises RuntimeError if the write didn't land intact. This matters over SSH: the WSSH
        proxy can drop a streamed stdin mid-transfer (getaddrinfo ENOTFOUND / connection closed)
        and `docker exec` still exits 0 with a truncated or empty file. A silent partial write is
        catastrophic here — a stale `dest` (e.g. baked into the snapshot image from a prior task's
        validation) would then be applied instead. So we byte-count both sides and retry.
        """
        want = len(text.encode())
        argv = f"{self.runtime} exec -i {self.container} bash -c {shlex.quote(f'cat > {dest}')}"
        last = ""
        for attempt in range(3):
            self._host_sh(argv, stdin=text)
            got = self._exec(f"wc -c < {shlex.quote(dest)} 2>/dev/null || echo -1").stdout.strip()
            if got.isdigit() and int(got) == want:
                return
            last = f"wrote {got} bytes, expected {want}"
        raise RuntimeError(f"failed to stage {dest} after 3 attempts ({last}) — "
                           "likely a flaky SSH/WSSH transport; retry the run")

    # --- Evaluator interface --------------------------------------------------------
    def setup(self, workspace_path: Path) -> None:
        self._ensure_runtime()
        # Fresh container from the provisioned snapshot.
        self._host_sh(f"{self.runtime} rm -f {self.container}")
        r = self._host_sh(
            f"{self.runtime} run -d --name {self.container} "
            f"-w {self.WORKDIR} {self.image} sleep infinity"
        )
        if r.returncode != 0:
            raise EnvironmentError(f"failed to start container: {r.stderr.strip()}")
        # Start the service stack (no init system in the bare container).
        svc = " ".join(f"service {s} start >/dev/null 2>&1;" for s in self.SERVICES)
        self._host_sh(f"{self.runtime} exec -u root {self.container} bash -c {shlex.quote(svc)}")

    def stage(self, impl_diff: str, gold_tests_diff: str) -> tuple[bool, str]:
        parent = self.parent_commit
        if not parent:
            return False, "ContainerEvaluator.parent_commit not set"
        # Remove any staging files left in the image/previous run FIRST. The snapshot was
        # `docker commit`ed with a prior task's /tmp/*.diff baked in; a silently-failed _put would
        # otherwise leave those stale diffs to be applied. Delete, then write+verify, then apply.
        self._exec("rm -f /tmp/impl.diff /tmp/gold-tests.diff")
        reset = self._exec(
            f"cd {self.WORKDIR} && git reset --hard {parent} && git clean -fdq"
        )
        if reset.returncode != 0:
            return False, f"reset failed: {reset.stderr.strip()}"
        # Re-sync the venv to THIS parent's lockfile (snapshot venv may be from a different era).
        if self.sync_venv:
            sync = self._exec(f"cd {self.WORKDIR} && {self._SYNC_CMD}")
            if sync.returncode != 0:
                return False, f"venv sync failed: {sync.stderr.strip()[-500:]}"
        try:
            if impl_diff.strip():
                self._put(impl_diff, "/tmp/impl.diff")
                r = self._exec(f"cd {self.WORKDIR} && git apply /tmp/impl.diff")
                if r.returncode != 0:
                    return False, f"impl diff apply failed: {r.stderr.strip()}"
            if gold_tests_diff.strip():
                self._put(gold_tests_diff, "/tmp/gold-tests.diff")
                r = self._exec(f"cd {self.WORKDIR} && git apply /tmp/gold-tests.diff")
                if r.returncode != 0:
                    return False, f"gold-tests overlay failed: {r.stderr.strip()}"
        except RuntimeError as e:  # verified _put gave up after retries
            return False, str(e)
        return True, "staged (impl + gold tests applied in container)"

    def _run_in_env(self, inner: str) -> TestRunResult:
        """Run `inner` under the venv, capturing output to a file and reading it back."""
        self._exec(
            f"cd {self.WORKDIR} && source .venv/bin/activate && "
            f"{{ {inner} ; }} > {self._LOG} 2>&1; echo $? > {self._RC}"
        )
        log = self._exec(f"tail -c 200000 {self._LOG}").stdout
        rc_raw = self._exec(f"cat {self._RC}").stdout.strip()
        try:
            rc = int(rc_raw)
        except ValueError:
            rc = 1
        return parse_test_backend_output(log, "", rc)

    def run_tests(self, workspace_path: Path, test_ids: list[str]) -> TestRunResult:
        dotted = [nodeid_to_dotted(t) for t in test_ids]
        return self._run_in_env(
            f"./tools/test-backend {self.TEST_BACKEND_FLAGS} "
            + " ".join(shlex.quote(d) for d in dotted)
        )

    def run_command(self, workspace_path: Path, command: str) -> TestRunResult:
        # Task regression_commands call `./tools/test-backend <modules>` without the
        # provision-check flag; inject it so they don't abort on a newer-than-snapshot parent.
        # (Harness concern, not the task author's — keeps YAMLs portable across snapshot eras.)
        if "test-backend" in command and self.TEST_BACKEND_FLAGS not in command:
            command = command.replace(
                "./tools/test-backend", f"./tools/test-backend {self.TEST_BACKEND_FLAGS}", 1
            )
        return self._run_in_env(command)

    def teardown(self) -> None:
        if self.runtime:
            self._host_sh(f"{self.runtime} rm -f {self.container}")


class RemoteContainerEvaluator(ContainerEvaluator):
    """ContainerEvaluator that drives docker on a remote host over SSH.

    The docker daemon, the provisioned snapshot, and the Zulip checkout all live on the remote
    Amazon Cloud Desktop (see ~/.rbg.conf); the harness runs on macOS. Commands are wrapped in
    `ssh <host> <script>` (with transient-failure retry); file writes go via `scp` to a host tmp
    path + `docker cp` (NOT ssh-stdin, which the WSSH proxy truncates on large payloads).
    """

    def __init__(self, host: str, **kwargs):
        super().__init__(**kwargs)
        self.host = host
        self.runtime = self.runtime or "docker"  # remote daemon assumed; don't probe locally

    def _ensure_runtime(self) -> None:
        # Runtime lives on the remote; trust it (probing here would check the wrong machine).
        self.runtime = self.runtime or "docker"

    # Substrings that mark a transient SSH/WSSH-proxy failure (not a real command error).
    _TRANSIENT = ("WSSH", "getaddrinfo ENOTFOUND", "Connection closed by",
                  "banner exchange", "Connection timed out", "kex_exchange")

    def _host_sh(self, script: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        # The Amazon WSSH proxy intermittently drops connections. Retry a few times when the
        # failure looks like transport flakiness (never on a genuine non-zero from the command).
        last = None
        for attempt in range(4):
            proc = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
                 self.host, script],
                input=stdin, capture_output=True, text=True,
            )
            last = proc
            if proc.returncode == 0:
                return proc
            blob = (proc.stderr or "") + (proc.stdout or "")
            if not any(m in blob for m in self._TRANSIENT):
                return proc  # real command failure — don't retry
        return last

    def _put(self, text: str, dest: str) -> None:
        """Copy a file into the remote container via scp + docker cp (NOT ssh-stdin streaming).

        The WSSH proxy truncates large streamed stdin (observed: an 18KB diff consistently cut to
        ~7.5KB). So write locally, `scp` to a temp path on the remote HOST, then `docker cp` into
        the container. Verified by byte-count; retries the whole path on transient transport error.
        """
        import os
        import tempfile
        want = len(text.encode())
        base = os.path.basename(dest)
        remote_tmp = f"/tmp/fatbench_put_{base}"
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as tf:
            tf.write(text)
            local_tmp = tf.name
        try:
            last = ""
            for attempt in range(3):
                scp = subprocess.run(
                    ["scp", "-q", "-o", "ConnectTimeout=20", local_tmp,
                     f"{self.host}:{remote_tmp}"],
                    capture_output=True, text=True,
                )
                if scp.returncode == 0:
                    # host tmp -> container dest, then verify size inside the container.
                    self._host_sh(f"{self.runtime} cp {remote_tmp} {self.container}:{dest} "
                                  f"&& rm -f {remote_tmp}")
                    got = self._exec(
                        f"wc -c < {shlex.quote(dest)} 2>/dev/null || echo -1").stdout.strip()
                    if got.isdigit() and int(got) == want:
                        return
                    last = f"container file {got} bytes, expected {want}"
                else:
                    last = f"scp failed: {scp.stderr.strip()[:200]}"
            raise RuntimeError(f"failed to stage {dest} after 3 attempts ({last})")
        finally:
            os.unlink(local_tmp)
