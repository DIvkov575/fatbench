"""FatBench harness orchestrator.

Run one agent on one task under one config, score it, write results.

    python -m harness.run --task tasks/zulip-001.yaml --config configs/baseline.yaml
    python -m harness.run --task ... --config ... --no-tests   # file metrics only (this host)
    python -m harness.run --task ... --config ... --dry-run     # set up + score gold, skip agent

Pipeline (spec §Pipeline): set up -> ask -> collect -> grade -> record.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import agent as agent_mod
from . import diffutil, scorer
from . import workspace as ws_mod
from .config import load_config
from .evaluator import (
    ContainerEvaluator,
    Evaluator,
    NullEvaluator,
    detect_container_runtime,
)
from .task import Task, load_task

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _read_gold_tests_diff(task: Task, tasks_dir: Path) -> str:
    p = tasks_dir / task.gold_tests_diff_path
    return p.read_text() if p.exists() else ""


def pick_evaluator(no_tests: bool, image: str | None) -> Evaluator:
    if no_tests:
        return NullEvaluator()
    if detect_container_runtime() is None:
        print("[harness] no container runtime detected -> NullEvaluator "
              "(file metrics only). Use a Linux/container host for correctness.",
              file=sys.stderr)
        return NullEvaluator()
    return ContainerEvaluator(image=image)


def run(
    task_path: str,
    config_path: str,
    *,
    no_tests: bool = False,
    dry_run: bool = False,
    results_root: Path | None = None,
    repo_override: str | None = None,
    image: str | None = None,
    keep_workspace: bool = False,
) -> dict:
    task = load_task(task_path)
    config = load_config(config_path)
    tasks_dir = Path(task_path).resolve().parent
    results_root = results_root or (REPO_ROOT / "results")

    repo_path = Path(repo_override) if repo_override else (REPO_ROOT / "repos" / task.repo)

    run_id = f"{_now_stamp()}_{task.id}_{config.name}"
    out_dir = results_root / run_id / task.id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. SET UP -----------------------------------------------------------------
    work = ws_mod.create_workspace(repo_path, task.parent_commit)
    agent_result = None
    try:
        if config.writes_claude_md:
            work.write_claude_md(config.claude_md)

        # 2. ASK + 3. COLLECT -------------------------------------------------
        if dry_run:
            # Score the gold backend diff as a sanity oracle instead of invoking the agent.
            agent_diff = (tasks_dir / task.gold_backend_diff_path).read_text()
        else:
            agent_result = agent_mod.invoke_claude_code(
                work.path, task.description, timeout_seconds=task.wall_clock_cap_seconds,
            )
            agent_diff = work.collect_diff(exclude_claude_md=True)

        (out_dir / "patch.diff").write_text(agent_diff)
        if agent_result is not None:
            (out_dir / "transcript.json").write_text(
                json.dumps(agent_result.raw_envelope, indent=2)
            )

        agent_paths = diffutil.parse_changed_paths(agent_diff)

        # 4. GRADE ------------------------------------------------------------
        file_metrics = scorer.score_files(agent_paths.impl_paths, task.gold_patch_files)

        evaluator = pick_evaluator(no_tests, image)
        gate_result = evaluator.run_tests(work.path, task.gate_tests)  # NullEvaluator: ran=False
        regression_result = None
        correctness = scorer.score_correctness(
            gate_result.total, gate_result.passed, gate_result.ran
        )
        if gate_result.ran:
            try:
                evaluator.setup(work.path)
                # Re-run gates inside the provisioned env, then regression.
                gate_result = _grade_in_env(evaluator, work, task, agent_diff, tasks_dir)
                correctness = scorer.score_correctness(
                    gate_result.total, gate_result.passed, gate_result.ran
                )
                regression_result = evaluator.run_command(work.path, task.regression_command)
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
            file_metrics=file_metrics,
            correctness=correctness,
            regression=regression,
            tokens_consumed=tokens,
        )

        # 5. RECORD -----------------------------------------------------------
        scores_dict = asdict(scores)
        (out_dir / "scores.json").write_text(json.dumps(scores_dict, indent=2))

        meta = {
            "run_id": run_id,
            "task": task.id,
            "config": config.name,
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


def _grade_in_env(evaluator, work, task, agent_diff, tasks_dir) -> "object":
    """Apply agent impl diff + overlay gold tests inside the provisioned env, then run gates.

    NOTE: the workspace already contains the agent's changes (it worked in-place). We only need
    to overlay the gold test files so the agent's own tests can't pass for it. Applying the gold
    tests diff on top of the agent's tree gives the PR's tests authority.
    """
    gold_tests_diff = _read_gold_tests_diff(task, tasks_dir)
    if gold_tests_diff.strip():
        ok, msg = ws_mod.apply_diff(work.path, gold_tests_diff)
        if not ok:
            print(f"[harness] WARNING: gold test overlay failed: {msg}", file=sys.stderr)
    return evaluator.run_tests(work.path, task.gate_tests)


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
    ap = argparse.ArgumentParser(description="FatBench harness: run + score one (task, config).")
    ap.add_argument("--task", required=True, help="path to tasks/<id>.yaml")
    ap.add_argument("--config", required=True, help="path to configs/<name>.yaml")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip test execution (file metrics only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="score the gold backend diff instead of invoking the agent (oracle check)")
    ap.add_argument("--results", default=None, help="results root dir (default: ./results)")
    ap.add_argument("--repo", default=None, help="override repo path (default: repos/<task.repo>)")
    ap.add_argument("--image", default=None, help="override container image")
    ap.add_argument("--keep-workspace", action="store_true", help="don't delete the temp workspace")
    args = ap.parse_args(argv)

    summary = run(
        args.task, args.config,
        no_tests=args.no_tests, dry_run=args.dry_run,
        results_root=Path(args.results) if args.results else None,
        repo_override=args.repo, image=args.image, keep_workspace=args.keep_workspace,
    )
    print(json.dumps(summary["scores"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
