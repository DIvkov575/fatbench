"""FatBench harness orchestrator.

Run one agent on one task under one ARM, score it, write results. The arm is either the
built-in vanilla `baseline` (default) or a user-supplied experiment (a CLAUDE.md / config).

    # vanilla baseline (built-in control):
    python -m harness.run --task tasks/zulip-001.yaml
    # bring-your-own experiment (any CLAUDE.md the user wants to test):
    python -m harness.run --task tasks/zulip-001.yaml --claude-md path/to/EXPERIMENT.CLAUDE.md
    # variants:
    python -m harness.run --task ... --no-tests   # file metrics only (no container)
    python -m harness.run --task ... --dry-run     # set up + score gold, skip the agent

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
from .config import Config, load_config
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
    config_path: str | None = None,
    *,
    claude_md_path: str | None = None,
    experiment_name: str | None = None,
    no_tests: bool = False,
    dry_run: bool = False,
    results_root: Path | None = None,
    repo_override: str | None = None,
    image: str | None = None,
    remote_host: str | None = None,
    keep_workspace: bool = False,
) -> dict:
    task = load_task(task_path)
    # Resolve the arm being measured:
    #   --claude-md PATH  -> bring-your-own experiment (no YAML needed)
    #   --config YAML     -> experiment defined in a config file
    #   neither           -> the built-in vanilla `baseline` control
    if claude_md_path:
        config = Config.from_claude_md(claude_md_path, name=experiment_name)
    elif config_path:
        config = load_config(config_path)
    else:
        config = Config.baseline()
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
        # Grade only the agent's IMPLEMENTATION. Its test edits are discarded before the gold
        # tests are overlaid — otherwise the agent could pass by weakening tests (CLAUDE.md
        # invariant "gates come from the PR, not the agent").
        impl_diff, agent_test_diff = diffutil.split_diff_by_role(agent_diff)
        (out_dir / "patch.impl.diff").write_text(impl_diff)
        if agent_test_diff.strip():
            (out_dir / "patch.agent-tests.diff").write_text(agent_test_diff)

        # 4. GRADE ------------------------------------------------------------
        file_metrics = scorer.score_files(agent_paths.impl_paths, task.gold_patch_files)

        evaluator = pick_evaluator(no_tests, image, remote_host, task.parent_commit)
        gold_tests_diff = _read_gold_tests_diff(task, tasks_dir)
        gate_result = NullEvaluator().run_tests(work.path, task.gate_tests)  # ran=False default
        regression_result = None
        if not isinstance(evaluator, NullEvaluator):
            try:
                evaluator.setup(work.path)
                # Stage inside the (provisioned) env: reset -> agent IMPL diff -> overlay gold
                # tests, so the PR's tests — not the agent's — have authority over the gates.
                staged, msg = evaluator.stage(impl_diff, gold_tests_diff)
                if not staged:
                    print(f"[harness] WARNING: staging failed: {msg}", file=sys.stderr)
                gate_result = evaluator.run_tests(work.path, task.gate_tests)
                regression_result = evaluator.run_command(work.path, task.regression_command)
            finally:
                evaluator.teardown()
        correctness = scorer.score_correctness(
            gate_result.total, gate_result.passed, gate_result.ran
        )
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
        description="FatBench: run one task under one arm and score it. "
                    "Default arm is the vanilla `baseline`; bring an experiment with --claude-md.")
    ap.add_argument("--task", required=True, help="path to tasks/<id>.yaml")
    # The arm under test. None of these -> the built-in vanilla baseline control.
    ap.add_argument("--claude-md", default=None,
                    help="EXPERIMENT: inject this CLAUDE.md into the agent's workspace "
                         "(bring-your-own; e.g. examples/experiments/*.CLAUDE.md). Mutually "
                         "exclusive with --config.")
    ap.add_argument("--config", default=None,
                    help="experiment config YAML (claude_md/claude_md_file). Omit both this and "
                         "--claude-md to run the vanilla baseline.")
    ap.add_argument("--experiment-name", default=None,
                    help="label for results dir when using --claude-md (default: file stem)")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip test execution (file metrics only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="score the gold backend diff instead of invoking the agent (oracle check)")
    ap.add_argument("--results", default=None, help="results root dir (default: ./results)")
    ap.add_argument("--repo", default=None, help="override repo path (default: repos/<task.repo>)")
    ap.add_argument("--image", default=None, help="override container image")
    ap.add_argument("--remote-host", default=None,
                    help="drive docker on this host over SSH (e.g. the Cloud Desktop in ~/.rbg.conf)")
    ap.add_argument("--keep-workspace", action="store_true", help="don't delete the temp workspace")
    args = ap.parse_args(argv)

    if args.claude_md and args.config:
        ap.error("--claude-md and --config are mutually exclusive (both define the experiment arm)")

    summary = run(
        args.task, args.config,
        claude_md_path=args.claude_md, experiment_name=args.experiment_name,
        no_tests=args.no_tests, dry_run=args.dry_run,
        results_root=Path(args.results) if args.results else None,
        repo_override=args.repo, image=args.image, remote_host=args.remote_host,
        keep_workspace=args.keep_workspace,
    )
    print(json.dumps(summary["scores"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
