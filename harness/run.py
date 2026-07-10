"""FatBench harness orchestrator.

Run one test: invoke an agent on a task, collect its diff, score it.

    python -m harness.run --task tasks/zulip-001.yaml --remote-host <host>
    python -m harness.run --task ... --no-tests   # file metrics only (no container)
    python -m harness.run --task ... --dry-run     # score the gold diff, skip the agent

The harness does NOT inject, configure, or model the agent's environment. Whatever claude -p
picks up from the user's setup (CLAUDE.md, plugins, MCP, hooks) is what gets measured. The
harness just invokes, collects, and scores.

Pipeline: set up workspace -> invoke agent -> collect diff -> grade -> record.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import agent as agent_mod
from . import bloat, diffutil, scorer
from . import workspace as ws_mod
from .evaluator import (
    ContainerEvaluator,
    Evaluator,
    NullEvaluator,
    RemoteContainerEvaluator,
    detect_container_runtime,
)
from .task import Task, load_task

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _read_gold_tests_diff(task: Task, tasks_dir: Path) -> str:
    p = tasks_dir / task.gold_tests_diff_path
    return p.read_text() if p.exists() else ""


def pick_evaluator(no_tests: bool, image: str | None, remote_host: str | None,
                   parent_commit: str | None) -> Evaluator:
    if no_tests:
        return NullEvaluator()
    if remote_host:
        return RemoteContainerEvaluator(host=remote_host, image=image,
                                        parent_commit=parent_commit)
    if detect_container_runtime() is None:
        print("[harness] no local container runtime -> NullEvaluator (file metrics only). "
              "Pass --remote-host <host> to drive docker on a Linux host, or --no-tests.",
              file=sys.stderr)
        return NullEvaluator()
    return ContainerEvaluator(image=image, parent_commit=parent_commit)


def run(
    task_path: str,
    *,
    name: str = "run",
    no_tests: bool = False,
    dry_run: bool = False,
    results_root: Path | None = None,
    repo_override: str | None = None,
    image: str | None = None,
    remote_host: str | None = None,
    keep_workspace: bool = False,
) -> dict:
    task = load_task(task_path)
    tasks_dir = Path(task_path).resolve().parent
    results_root = results_root or (REPO_ROOT / "results")

    repo_path = Path(repo_override) if repo_override else (REPO_ROOT / "repos" / task.repo)

    run_id = f"{_now_stamp()}_{task.id}_{name}"
    out_dir = results_root / run_id / task.id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. SET UP -----------------------------------------------------------------
    work = ws_mod.create_workspace(repo_path, task.parent_commit)
    agent_result = None
    try:
        # 2. ASK + 3. COLLECT -------------------------------------------------
        if dry_run:
            # Score the gold backend diff as a sanity oracle instead of invoking the agent.
            agent_diff = (tasks_dir / task.gold_backend_diff_path).read_text()
        else:
            # Context bloat: inflate off-path decoy files so the agent burns tokens reading them,
            # then restore them to pristine parent content before collecting the diff — bloat
            # never reaches the oracle, it only drags efficiency.
            bloated = bloat.inflate_files(
                work.path, task.bloat_files, task.bloat_tokens_per_file) if task.bloat_files else []
            if task.bloat_files and len(bloated) < len(task.bloat_files):
                missing = sorted(set(task.bloat_files) - set(bloated))
                print(f"[harness] WARNING: {len(missing)} bloat file(s) not found in parent, "
                      f"skipped: {missing}", file=sys.stderr)
            agent_result = agent_mod.invoke_claude_code(
                work.path, task.description, timeout_seconds=task.wall_clock_cap_seconds,
            )
            if bloated:
                bloat.restore_files(work.path, task.parent_commit, bloated)
            agent_diff = work.collect_diff()

        (out_dir / "patch.diff").write_text(agent_diff)
        if agent_result is not None:
            (out_dir / "transcript.json").write_text(
                json.dumps(agent_result.raw_envelope, indent=2)
            )

        # Apply only the agent's IMPLEMENTATION; its test edits are discarded before the PR's
        # gold tests are overlaid — otherwise the agent could "pass" by weakening tests
        # (invariant "gates come from the PR, not the agent"). This is a staging rule, not scoring.
        impl_diff, agent_test_diff = diffutil.split_diff_by_role(agent_diff)
        (out_dir / "patch.impl.diff").write_text(impl_diff)
        if agent_test_diff.strip():
            (out_dir / "patch.agent-tests.diff").write_text(agent_test_diff)

        # 4. GRADE (SWE-bench style: resolved = FAIL_TO_PASS all pass AND PASS_TO_PASS all pass) -
        evaluator = pick_evaluator(no_tests, image, remote_host, task.parent_commit)
        gold_tests_diff = _read_gold_tests_diff(task, tasks_dir)
        gate_result = NullEvaluator().run_tests(work.path, task.gate_tests)  # ran=False default
        regression_result = None
        if not isinstance(evaluator, NullEvaluator):
            try:
                evaluator.setup(work.path)
                # Stage inside the provisioned env: reset -> agent IMPL diff -> overlay gold tests.
                staged, msg = evaluator.stage(impl_diff, gold_tests_diff)
                if not staged:
                    print(f"[harness] WARNING: staging failed: {msg}", file=sys.stderr)
                gate_result = evaluator.run_tests(work.path, task.gate_tests)       # FAIL_TO_PASS
                regression_result = evaluator.run_command(work.path, task.regression_command)  # PASS_TO_PASS
            finally:
                evaluator.teardown()
        regression = (
            (1.0 if regression_result.ok else 0.0)
            if (regression_result and regression_result.ran) else -1.0
        )

        (out_dir / "test_output.log").write_text(
            (gate_result.raw_log or gate_result.error or "tests not run")
            + ("\n\n=== REGRESSION ===\n" + (regression_result.raw_log or "")
               if regression_result else "")
        )

        tokens = agent_result.tokens_consumed if agent_result else 0
        scores = scorer.compute_scores(
            gates_total=gate_result.total,
            gates_passed=gate_result.passed,
            gates_ran=gate_result.ran,
            regression=regression,
            tokens_consumed=tokens,
        )

        # 5. RECORD -----------------------------------------------------------
        scores_dict = asdict(scores)
        (out_dir / "scores.json").write_text(json.dumps(scores_dict, indent=2))

        meta = {
            "run_id": run_id,
            "task": task.id,
            "name": name,
            "parent_commit": task.parent_commit,
            "dry_run": dry_run,
            "tests_ran": gate_result.ran,
            "agent": _agent_meta(agent_result),
            "gate_tests": task.gate_tests,
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        summary = {"run_id": run_id, "scores": scores_dict, "meta": meta}
        (results_root / run_id / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary
    finally:
        if not keep_workspace:
            work.cleanup()


def _agent_meta(agent_result) -> dict | None:
    if agent_result is None:
        return None
    return {
        "ok": agent_result.ok,
        "timed_out": agent_result.timed_out,
        "input_tokens": agent_result.input_tokens,
        "output_tokens": agent_result.output_tokens,
        "cache_creation_tokens": agent_result.cache_creation_tokens,
        "tokens_consumed": agent_result.tokens_consumed,
        "cost_usd": agent_result.cost_usd,
        "num_turns": agent_result.num_turns,
        "duration_ms": agent_result.duration_ms,
        "error": agent_result.error,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="FatBench: run one test — invoke an agent on a task, score it.")
    ap.add_argument("--task", required=True, help="path to tasks/<id>.yaml")
    ap.add_argument("--name", default="run", help="label for the results dir (default: 'run')")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip test execution (file metrics only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="score the gold backend diff instead of invoking the agent (oracle check)")
    ap.add_argument("--results", default=None, help="results root dir (default: ./results)")
    ap.add_argument("--repo", default=None, help="override repo path (default: repos/<task.repo>)")
    ap.add_argument("--image", default=None, help="override container image")
    ap.add_argument("--remote-host", default=None,
                    help="drive docker on this host over SSH (e.g. the Cloud Desktop)")
    ap.add_argument("--keep-workspace", action="store_true", help="don't delete the temp workspace")
    args = ap.parse_args(argv)

    summary = run(
        args.task,
        name=args.name, no_tests=args.no_tests, dry_run=args.dry_run,
        results_root=Path(args.results) if args.results else None,
        repo_override=args.repo, image=args.image, remote_host=args.remote_host,
        keep_workspace=args.keep_workspace,
    )
    print(json.dumps(summary["scores"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
