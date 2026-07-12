"""Generate SWE-bench predictions by running an arm's agent on each instance.

For each (instance, arm): clone the instance's repo at its base_commit into a throwaway
workspace, run `claude -p <problem_statement>` under that arm's config, collect the git diff
as the candidate patch. Emit one predictions JSONL per arm in the SWE-bench schema:

    {"instance_id": ..., "model_name_or_path": <arm>, "model_patch": <diff>}

which the official `swebench.harness.run_evaluation` consumes verbatim. Per-run agent telemetry
(tokens, cost, turns, wall time, ok/timeout) is written alongside for the cost comparison.

Repos are cached under swebench/data/repos/<owner>__<name>.git (a bare mirror) so both arms and
repeated instances of the same repo don't re-clone from GitHub. Each run gets its own working
clone off the cache, hard-reset to base_commit — the agent's changes never touch the cache.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from harness import agent as agent_mod

from .arms import Arm
from .dataset import Instance

DATA_DIR = Path(__file__).resolve().parent / "data"
REPO_CACHE = DATA_DIR / "repos"


def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _ensure_mirror(repo_url: str, repo: str) -> Path:
    """Ensure a bare mirror of `repo` exists in the cache; return its path."""
    mirror = REPO_CACHE / f"{repo.replace('/', '__')}.git"
    if mirror.exists():
        return mirror
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = mirror.with_suffix(".git.tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    _git(["clone", "--bare", repo_url, str(tmp)])
    tmp.rename(mirror)
    return mirror


def _checkout_workspace(instance: Instance) -> tuple[Path, Path]:
    """Clone the cached mirror into a temp working tree at base_commit.

    Returns (tmp_root, work_dir). Caller cleans up tmp_root. Fetches the base_commit into the
    mirror on demand if the initial mirror clone predates it (defensive; bare mirrors are full).
    """
    mirror = _ensure_mirror(instance.repo_url, instance.repo)
    tmp_root = Path(tempfile.mkdtemp(prefix="sweb-ws-"))
    work = tmp_root / "repo"
    _git(["clone", "--local", "--no-hardlinks", str(mirror), str(work)])
    try:
        _git(["checkout", "--detach", instance.base_commit], cwd=work)
    except subprocess.CalledProcessError:
        # base_commit not in the mirror (rare) — fetch it, then retry.
        _git(["fetch", instance.repo_url, instance.base_commit], cwd=work, check=False)
        _git(["checkout", "--detach", instance.base_commit], cwd=work)
    _git(["reset", "--hard", instance.base_commit], cwd=work)
    _git(["clean", "-fdx"], cwd=work, check=False)
    return tmp_root, work


def _collect_patch(work: Path, base_commit: str) -> str:
    """Diff of the agent's changes vs base_commit (tracked + newly created files)."""
    _git(["add", "-A"], cwd=work)
    return _git(["diff", "--cached", "--no-color", base_commit], cwd=work).stdout


@dataclass
class RunRecord:
    instance_id: str
    arm: str
    model_patch: str
    empty_patch: bool
    agent_ok: bool
    timed_out: bool
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    tokens_consumed: int
    cost_usd: float
    num_turns: int
    duration_ms: int
    error: str | None


def run_instance(instance: Instance, arm: Arm, *, timeout_seconds: int,
                 keep_workspace: bool = False) -> RunRecord:
    """Run one arm's agent on one instance; return its prediction + telemetry."""
    tmp_root, work = _checkout_workspace(instance)
    try:
        res = agent_mod.invoke_claude_code(
            work, instance.problem_statement,
            timeout_seconds=timeout_seconds,
            extra_args=arm.extra_args,
            env=arm.env,
            # Both arms run in a fresh, untrusted tmpdir workspace. `--dangerously-skip-permissions`
            # would trigger a one-time trust dialog that hangs -p mode; bypassPermissions needs none.
            perm_args=["--permission-mode", "bypassPermissions"],
        )
        patch = _collect_patch(work, instance.base_commit)
        return RunRecord(
            instance_id=instance.instance_id,
            arm=arm.name,
            model_patch=patch,
            empty_patch=not patch.strip(),
            agent_ok=res.ok,
            timed_out=res.timed_out,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cache_creation_tokens=res.cache_creation_tokens,
            tokens_consumed=res.tokens_consumed,
            cost_usd=res.cost_usd,
            num_turns=res.num_turns,
            duration_ms=res.duration_ms,
            error=res.error,
        )
    finally:
        if not keep_workspace:
            shutil.rmtree(tmp_root, ignore_errors=True)


def prediction_line(rec: RunRecord) -> dict:
    """The SWE-bench predictions JSONL schema (what run_evaluation consumes)."""
    return {
        "instance_id": rec.instance_id,
        "model_name_or_path": rec.arm,
        "model_patch": rec.model_patch,
    }


def write_predictions(records: list[RunRecord], out_path: Path) -> Path:
    """Write predictions JSONL (SWE-bench schema) for one arm."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for rec in records:
            f.write(json.dumps(prediction_line(rec)) + "\n")
    return out_path


def write_telemetry(records: list[RunRecord], out_path: Path) -> Path:
    """Write per-run agent telemetry (tokens/cost/turns) as JSONL."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec)) + "\n")
    return out_path
