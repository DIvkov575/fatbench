"""Compare arms: resolved rate + agent cost, side by side.

Consumes the per-arm EvalReports (resolved bit from the official grader) and the per-run
telemetry (tokens/cost/turns from prediction time) and produces one comparison dict/table.
The resolved rate is the headline; tokens and cost say what each arm spent to get there.
"""

from __future__ import annotations

import json
from pathlib import Path


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize_arm(arm: str, resolved_ids: list[str], submitted_ids: list[str],
                  telemetry: list[dict]) -> dict:
    """One arm's row: resolved rate + averaged agent telemetry over its runs."""
    resolved = set(resolved_ids)
    submitted = submitted_ids or [t["instance_id"] for t in telemetry]
    n = len(submitted)
    return {
        "arm": arm,
        "n": n,
        "resolved": len(resolved),
        "resolved_rate": len(resolved) / n if n else 0.0,
        "empty_patches": sum(1 for t in telemetry if t.get("empty_patch")),
        "timeouts": sum(1 for t in telemetry if t.get("timed_out")),
        "avg_tokens": round(_mean([t["tokens_consumed"] for t in telemetry]), 1),
        "total_tokens": sum(t["tokens_consumed"] for t in telemetry),
        "avg_cost_usd": round(_mean([t["cost_usd"] for t in telemetry]), 4),
        "total_cost_usd": round(sum(t["cost_usd"] for t in telemetry), 4),
        "avg_turns": round(_mean([t["num_turns"] for t in telemetry]), 1),
        "avg_duration_s": round(_mean([t["duration_ms"] / 1000 for t in telemetry]), 1),
        "resolved_ids": sorted(resolved),
    }


def compare(arm_summaries: list[dict]) -> dict:
    """Build the full comparison: per-arm rows + head-to-head deltas + per-instance breakdown."""
    by_name = {s["arm"]: s for s in arm_summaries}
    out: dict = {"arms": arm_summaries}

    # Head-to-head when exactly the raw/mine pair is present.
    if "raw" in by_name and "mine" in by_name:
        raw, mine = by_name["raw"], by_name["mine"]
        raw_set, mine_set = set(raw["resolved_ids"]), set(mine["resolved_ids"])
        out["head_to_head"] = {
            "resolved_rate_delta": round(mine["resolved_rate"] - raw["resolved_rate"], 4),
            "only_mine_resolved": sorted(mine_set - raw_set),   # your config's wins
            "only_raw_resolved": sorted(raw_set - mine_set),    # regressions from your config
            "both_resolved": sorted(mine_set & raw_set),
            "neither_resolved_of_shared": None,  # filled by caller if instance universe known
            "token_ratio_mine_over_raw": (
                round(mine["avg_tokens"] / raw["avg_tokens"], 3) if raw["avg_tokens"] else None),
            "cost_ratio_mine_over_raw": (
                round(mine["avg_cost_usd"] / raw["avg_cost_usd"], 3)
                if raw["avg_cost_usd"] else None),
        }
    return out


def render_table(comparison: dict) -> str:
    """A compact fixed-width table for the terminal."""
    cols = ["arm", "n", "resolved", "resolved_rate", "empty_patches", "timeouts",
            "avg_tokens", "avg_cost_usd", "avg_turns", "avg_duration_s"]
    widths = {c: max(len(c), *(len(str(a[c])) for a in comparison["arms"])) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    rows = [line, "  ".join("-" * widths[c] for c in cols)]
    for a in comparison["arms"]:
        rows.append("  ".join(str(a[c]).ljust(widths[c]) for c in cols))

    if "head_to_head" in comparison:
        h = comparison["head_to_head"]
        rows += [
            "",
            f"resolved-rate delta (mine - raw): {h['resolved_rate_delta']:+.4f}",
            f"only mine resolved: {h['only_mine_resolved']}",
            f"only raw resolved:  {h['only_raw_resolved']}",
            f"token ratio  (mine/raw): {h['token_ratio_mine_over_raw']}",
            f"cost ratio   (mine/raw): {h['cost_ratio_mine_over_raw']}",
        ]
    return "\n".join(rows)


def load_telemetry(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
