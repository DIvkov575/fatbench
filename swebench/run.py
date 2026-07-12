"""Orchestrate the two-arm SWE-bench comparison.

    # Predict + grade + compare, 3-instance smoke:
    python -m swebench.run --arms raw,mine --limit 3 --remote-host <host> \
        --remote-python /path/to/venv/bin/python

    # Predictions only (no grading), e.g. to sanity-check patches first:
    python -m swebench.run --arms raw,mine --limit 3 --no-eval

    # Grade an existing predictions dir (skip re-running agents):
    python -m swebench.run --grade-only results-swebench/<run>/ --remote-host <host>

Flow per arm: for each instance, clone repo@base_commit, run the arm's `claude -p`, collect the
diff -> predictions JSONL + telemetry JSONL. Then the official swebench grader runs on the remote
and we pull back resolved bits. Finally compare.py renders raw-vs-mine.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import compare as compare_mod
from . import predict as predict_mod
from .arms import build_mine_arm, build_raw_arm
from .dataset import DEFAULT_DATASET, load_instances
from .evaluate import RemoteSweBenchGrader, check_remote_ready

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "results-swebench"


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _default_aws_credential_export() -> str | None:
    """Read the user's Bedrock credential export from ~/.claude/settings.json (for the raw arm)."""
    p = Path.home() / ".claude" / "settings.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("awsCredentialExport")
    except (json.JSONDecodeError, OSError):
        return None


def build_arms(names: list[str], out_dir: Path, model: str | None) -> list:
    arms = []
    for name in names:
        if name == "raw":
            arms.append(build_raw_arm(
                config_dir=out_dir / "raw-config",
                aws_credential_export=_default_aws_credential_export(),
                model=model,
            ))
        elif name == "mine":
            arms.append(build_mine_arm(model=model))
        else:
            raise ValueError(f"unknown arm '{name}' (expected 'raw' or 'mine')")
    return arms


def run(*, arm_names: list[str], dataset: str, limit: int | None,
        instance_ids: list[str] | None, timeout_seconds: int, model: str | None,
        remote_host: str | None, remote_python: str, max_workers: int,
        no_eval: bool, keep_workspace: bool) -> dict:
    run_id = _now_stamp()
    out_dir = RESULTS_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(dataset, limit=limit, instance_ids=instance_ids)
    arms = build_arms(arm_names, out_dir, model)
    print(f"[swebench] run {run_id}: {len(instances)} instances x {len(arms)} arms "
          f"({', '.join(a.name for a in arms)})", file=sys.stderr)

    grader = None
    if not no_eval:
        if not remote_host:
            print("[swebench] no --remote-host; skipping grading (predictions only). "
                  "Pass --remote-host or --no-eval to silence.", file=sys.stderr)
        else:
            ready, msg = check_remote_ready(remote_host, remote_python)
            print(f"[swebench] remote check: {msg}", file=sys.stderr)
            if not ready:
                raise SystemExit(f"remote not ready for grading: {msg}")
            grader = RemoteSweBenchGrader(
                remote_host, remote_python=remote_python, dataset=dataset,
                max_workers=max_workers)

    arm_summaries = []
    for arm in arms:
        print(f"[swebench] === arm '{arm.name}': {arm.description} ===", file=sys.stderr)
        records = []
        for i, inst in enumerate(instances, 1):
            print(f"[swebench]   ({i}/{len(instances)}) {inst.instance_id} ...",
                  file=sys.stderr, flush=True)
            rec = predict_mod.run_instance(
                inst, arm, timeout_seconds=timeout_seconds, keep_workspace=keep_workspace)
            flag = "EMPTY" if rec.empty_patch else ("TIMEOUT" if rec.timed_out else "ok")
            print(f"[swebench]       {flag}  tokens={rec.tokens_consumed} "
                  f"cost=${rec.cost_usd:.3f} turns={rec.num_turns}", file=sys.stderr)
            records.append(rec)

        pred_path = out_dir / f"predictions.{arm.name}.jsonl"
        tel_path = out_dir / f"telemetry.{arm.name}.jsonl"
        predict_mod.write_predictions(records, pred_path)
        predict_mod.write_telemetry(records, tel_path)

        resolved_ids: list[str] = []
        submitted_ids = [r.instance_id for r in records]
        if grader:
            print(f"[swebench]   grading '{arm.name}' on {remote_host} ...", file=sys.stderr)
            report = grader.evaluate(arm.name, pred_path, run_id)
            resolved_ids = report.resolved_ids
            submitted_ids = report.submitted_ids or submitted_ids
            print(f"[swebench]   resolved {report.resolved_count}/{report.submitted_count}",
                  file=sys.stderr)

        telemetry = [predict_mod.asdict(r) for r in records]
        arm_summaries.append(
            compare_mod.summarize_arm(arm.name, resolved_ids, submitted_ids, telemetry))

    comparison = compare_mod.compare(arm_summaries)
    comparison["run_id"] = run_id
    comparison["dataset"] = dataset
    comparison["graded"] = grader is not None
    (out_dir / "comparison.json").write_text(json.dumps(comparison, indent=2))

    table = compare_mod.render_table(comparison)
    (out_dir / "comparison.txt").write_text(table)
    print("\n" + table)
    print(f"\n[swebench] results in {out_dir}", file=sys.stderr)
    return comparison


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SWE-bench two-arm comparison: raw vs your config.")
    ap.add_argument("--arms", default="raw,mine",
                    help="comma-separated arms to run (default: raw,mine)")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--limit", type=int, default=None,
                    help="run the first N instances (deterministic pilot subset)")
    ap.add_argument("--instance-ids", default=None,
                    help="comma-separated instance_ids to run (overrides --limit)")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="per-instance agent wall-clock cap in seconds (default 1800)")
    ap.add_argument("--model", default=None, help="pin the model for both arms")
    ap.add_argument("--remote-host", default=None,
                    help="host running docker + swebench for grading (over SSH)")
    ap.add_argument("--remote-python", default="python3",
                    help="python on the remote with swebench installed")
    ap.add_argument("--max-workers", type=int, default=4,
                    help="swebench grader parallelism on the remote")
    ap.add_argument("--no-eval", action="store_true",
                    help="generate predictions only; skip grading")
    ap.add_argument("--keep-workspace", action="store_true")
    args = ap.parse_args(argv)

    run(
        arm_names=[a.strip() for a in args.arms.split(",") if a.strip()],
        dataset=args.dataset,
        limit=args.limit,
        instance_ids=([s.strip() for s in args.instance_ids.split(",")]
                      if args.instance_ids else None),
        timeout_seconds=args.timeout,
        model=args.model,
        remote_host=args.remote_host,
        remote_python=args.remote_python,
        max_workers=args.max_workers,
        no_eval=args.no_eval,
        keep_workspace=args.keep_workspace,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
