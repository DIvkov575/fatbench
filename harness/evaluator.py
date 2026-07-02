"""Test-execution seam.

Grading correctness/regression requires running Zulip's backend tests, which need a full
Linux service stack (Postgres/Redis/RabbitMQ/memcached) — container only. This module isolates
that behind the `Evaluator` interface so the rest of the pipeline runs anywhere.

- ContainerEvaluator: runs `./tools/test-backend` inside Zulip's CI container. BUILT BUT
  UNVALIDATED — no container runtime exists on the authoring host (see STATUS.md). The
  test-backend output parser and container wiring are written to documented behavior and must
  be validated on a Linux env before results are trusted.
- NullEvaluator: skips execution, returns ran=False. Lets the full pipeline + file metrics run
  on this host today.

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
    """Run test-backend inside Zulip's CI container.

    UNVALIDATED on the authoring host (no runtime). The high-level flow:
      setup():     ensure runtime, pull image, start a container with the workspace mounted,
                   provision the backend (tools/ci/setup-backend) once.
      run_tests(): `<runtime> exec <cid> ./tools/test-backend <dotted ids...>`
    """

    DEFAULT_IMAGE = "zulip/ci:bookworm"
    MOUNT = "/srv/zulip"

    def __init__(self, image: str | None = None, runtime: str | None = None,
                 provision: bool = True):
        self.image = image or self.DEFAULT_IMAGE
        self.runtime = runtime
        self.provision = provision
        self.container_id: str | None = None

    def setup(self, workspace_path: Path) -> None:
        self.runtime = self.runtime or detect_container_runtime()
        if not self.runtime:
            raise EnvironmentError(
                "No working container runtime (docker/podman) found. Zulip's backend tests "
                "need a Linux service stack. Options: install colima+docker (arm64 host runs the "
                "amd64 zulip/ci image emulated), or run the harness on a Linux host. "
                "Use --no-tests to score file metrics only."
            )
        subprocess.run([self.runtime, "pull", self.image], check=True)
        proc = subprocess.run(
            [self.runtime, "run", "-d", "-v", f"{workspace_path}:{self.MOUNT}",
             "-w", self.MOUNT, self.image, "sleep", "infinity"],
            check=True, capture_output=True, text=True,
        )
        self.container_id = proc.stdout.strip()
        if self.provision:
            self._exec(["./tools/ci/setup-backend", "--skip-dev-db-build"])

    def _exec(self, argv: list[str]) -> subprocess.CompletedProcess:
        if not self.container_id:
            raise RuntimeError("ContainerEvaluator.setup() not called")
        return subprocess.run(
            [self.runtime, "exec", self.container_id, *argv],
            capture_output=True, text=True,
        )

    def run_tests(self, workspace_path: Path, test_ids: list[str]) -> TestRunResult:
        dotted = [nodeid_to_dotted(t) for t in test_ids]
        proc = self._exec(["./tools/test-backend", *dotted])
        return parse_test_backend_output(proc.stdout, proc.stderr, proc.returncode)

    def run_command(self, workspace_path: Path, command: str) -> TestRunResult:
        proc = self._exec(["bash", "-lc", command])
        return parse_test_backend_output(proc.stdout, proc.stderr, proc.returncode)

    def teardown(self) -> None:
        if self.container_id and self.runtime:
            subprocess.run([self.runtime, "rm", "-f", self.container_id],
                           capture_output=True, text=True)
            self.container_id = None
