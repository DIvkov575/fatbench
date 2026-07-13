"""Grade predictions with the OFFICIAL SWE-bench harness on the remote Cloud Desktop.

We do not re-implement SWE-bench's oracle — we call `swebench.harness.run_evaluation`, which
builds/reuses a per-instance x86_64 Docker image, applies the model_patch, applies the gold
test_patch, and checks FAIL_TO_PASS/PASS_TO_PASS. That is the authoritative resolved bit.

Transport mirrors harness.evaluator.RemoteContainerEvaluator: docker + swebench live on the
remote (see ~/.rbg.conf); we run on macOS. Commands go over `ssh <host>`; the predictions file
goes over `scp` (never ssh-stdin — the WSSH proxy truncates large payloads). We retry transient
proxy failures but never a genuine non-zero exit.

Remote prerequisites (one-time): a Python env on the host with `swebench` installed and a working
docker daemon. The grader is x86_64-native there (no emulation), same host used for Zulip gates.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_TRANSIENT = ("WSSH", "getaddrinfo ENOTFOUND", "Connection closed by",
              "banner exchange", "Connection timed out", "kex_exchange")


@dataclass
class EvalReport:
    arm: str
    run_id: str
    resolved_ids: list[str]
    submitted_ids: list[str]
    report_path: Path
    raw: dict

    @property
    def resolved_count(self) -> int:
        return len(self.resolved_ids)

    @property
    def submitted_count(self) -> int:
        return len(self.submitted_ids)

    @property
    def resolved_rate(self) -> float:
        return self.resolved_count / self.submitted_count if self.submitted_count else 0.0


class RemoteSweBenchGrader:
    """Runs the official swebench grader on a remote host over SSH.

    `remote_python` should be a Python (venv) on the host with `swebench` importable.
    `remote_workdir` is a scratch dir on the host for predictions + reports.
    """

    def __init__(self, host: str, *, remote_python: str = "python3",
                 remote_workdir: str = "/tmp/fatbench-swebench",
                 dataset: str = "princeton-nlp/SWE-bench_Lite",
                 max_workers: int = 4):
        self.host = host
        self.remote_python = remote_python
        self.remote_workdir = remote_workdir.rstrip("/")
        self.dataset = dataset
        self.max_workers = max_workers

    # --- transport ------------------------------------------------------------------
    def _ssh(self, script: str) -> subprocess.CompletedProcess:
        last = None
        for _ in range(4):
            proc = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
                 self.host, script],
                capture_output=True, text=True,
            )
            last = proc
            if proc.returncode == 0:
                return proc
            blob = (proc.stderr or "") + (proc.stdout or "")
            if not any(m in blob for m in _TRANSIENT):
                return proc
        return last

    def _scp_up(self, local: Path, remote: str) -> None:
        last = ""
        for _ in range(3):
            r = subprocess.run(
                ["scp", "-q", "-o", "ConnectTimeout=20", str(local), f"{self.host}:{remote}"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                return
            last = r.stderr.strip()[:200]
            if not any(m in last for m in _TRANSIENT):
                break
        raise RuntimeError(f"scp {local} -> {self.host}:{remote} failed: {last}")

    def _scp_down(self, remote: str, local: Path) -> bool:
        r = subprocess.run(
            ["scp", "-q", "-o", "ConnectTimeout=20", f"{self.host}:{remote}", str(local)],
            capture_output=True, text=True,
        )
        return r.returncode == 0

    # --- grading --------------------------------------------------------------------
    def evaluate(self, arm: str, predictions_path: Path, run_id: str) -> EvalReport:
        """scp predictions to the host, run the official grader, pull back the report JSON."""
        self._ssh(f"mkdir -p {shlex.quote(self.remote_workdir)}")
        remote_pred = f"{self.remote_workdir}/predictions_{arm}_{run_id}.jsonl"
        self._scp_up(predictions_path, remote_pred)

        # run_evaluation names the report "<model_name_or_path>.<run_id>.json" in CWD.
        full_run_id = f"{arm}_{run_id}"
        cmd = (
            f"cd {shlex.quote(self.remote_workdir)} && "
            f"{shlex.quote(self.remote_python)} -m swebench.harness.run_evaluation "
            f"--dataset_name {shlex.quote(self.dataset)} "
            f"--predictions_path {shlex.quote(remote_pred)} "
            f"--max_workers {self.max_workers} "
            f"--run_id {shlex.quote(full_run_id)} "
            f"--cache_level env"
        )
        proc = self._ssh(cmd)
        # The report filename uses model_name_or_path (== arm) and the run_id.
        remote_report = f"{self.remote_workdir}/{arm}.{full_run_id}.json"
        local_report = predictions_path.parent / f"report.{arm}.json"
        if not self._scp_down(remote_report, local_report):
            raise RuntimeError(
                f"grader ran but report not found at {remote_report}. "
                f"stdout tail:\n{proc.stdout[-2000:]}\nstderr tail:\n{proc.stderr[-2000:]}"
            )

        raw = json.loads(local_report.read_text())
        return EvalReport(
            arm=arm,
            run_id=full_run_id,
            resolved_ids=list(raw.get("resolved_ids", [])),
            submitted_ids=list(raw.get("submitted_ids", [])),
            report_path=local_report,
            raw=raw,
        )


def check_remote_ready(host: str, remote_python: str = "python3") -> tuple[bool, str]:
    """Probe the remote: docker daemon up + swebench importable. Returns (ok, message)."""
    grader = RemoteSweBenchGrader(host, remote_python=remote_python)
    docker = grader._ssh("docker info >/dev/null 2>&1 && echo OK || echo FAIL")
    if "OK" not in docker.stdout:
        return False, f"docker not ready on {host}: {docker.stdout.strip()} {docker.stderr.strip()}"
    sweb = grader._ssh(
        f"{shlex.quote(remote_python)} -c 'import swebench; print(swebench.__version__)' 2>&1")
    if sweb.returncode != 0 or "Error" in sweb.stdout or "Traceback" in sweb.stdout:
        return False, (f"swebench not importable via {remote_python} on {host}: "
                       f"{sweb.stdout.strip()[:300]}")
    return True, f"remote ready (swebench {sweb.stdout.strip()})"
