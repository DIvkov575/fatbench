"""Invoke the Claude Code agent on a task, in a workspace.

We run `claude -p <prompt> --output-format json` from the workspace dir so the agent sees the
repo (and any injected CLAUDE.md) exactly as in normal use. The task description is the ONLY
input — no file hints, no gold info (LLD.md §6 adapter constraint).

The JSON envelope (probed) provides: result, is_error, num_turns, duration_ms, total_cost_usd,
and usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentResult:
    ok: bool
    result_text: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    num_turns: int
    duration_ms: int
    timed_out: bool
    raw_envelope: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def tokens_consumed(self) -> int:
        # Billed input (incl. cache-creation, which is charged as input) + output.
        return self.input_tokens + self.cache_creation_tokens + self.output_tokens


def invoke_claude_code(
    workspace_path: str | Path,
    prompt: str,
    timeout_seconds: int,
    extra_args: list[str] | None = None,
    claude_bin: str = "claude",
    isolate: bool = True,
) -> AgentResult:
    """Run `claude -p` on the task.

    `isolate=True` (default) adds `--strict-mcp-config` so NO MCP servers load — the agent gets
    plain Claude Code + the repo + the task prompt, with none of the host's user-level MCP
    "plugins". This keeps the benchmark measuring the model's own fat-context ability, not
    whatever MCP tooling a given machine happens to have installed, and it applies IDENTICALLY
    to both the baseline and full-harness arms so the only variable between them stays the
    injected CLAUDE.md.

    Deliberately NOT using `--bare`: verified (2026-07-07) that `--bare` also suppresses the
    *workspace's own* CLAUDE.md, which would silently disable the full-harness config's onboarding
    doc and confound the comparison. `--setting-sources project,local` was also rejected — it drops
    the `user` source where OAuth lives and breaks auth (403). `--strict-mcp-config` alone keeps
    auth + reads the workspace CLAUDE.md while removing MCP servers — exactly what we want.
    """
    workspace_path = Path(workspace_path)
    args = [
        claude_bin, "-p", prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
        *(["--strict-mcp-config"] if isolate else []),
        *(extra_args or []),
    ]

    try:
        proc = subprocess.run(
            args, cwd=workspace_path, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return AgentResult(
            ok=False, result_text="", input_tokens=0, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0, cost_usd=0.0,
            num_turns=0, duration_ms=timeout_seconds * 1000, timed_out=True,
            error=f"agent timed out after {timeout_seconds}s",
        )

    out = proc.stdout.strip()
    if not out:
        return AgentResult(
            ok=False, result_text="", input_tokens=0, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0, cost_usd=0.0,
            num_turns=0, duration_ms=0, timed_out=False,
            error=f"empty agent output (exit {proc.returncode}): {proc.stderr[-500:]}",
        )

    try:
        env = json.loads(out)
    except json.JSONDecodeError as e:
        return AgentResult(
            ok=False, result_text=out[:2000], input_tokens=0, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0, cost_usd=0.0,
            num_turns=0, duration_ms=0, timed_out=False,
            error=f"could not parse agent JSON envelope: {e}",
        )

    usage = env.get("usage", {}) or {}
    return AgentResult(
        ok=not env.get("is_error", False),
        result_text=env.get("result", ""),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0)),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0)),
        cost_usd=float(env.get("total_cost_usd", 0.0)),
        num_turns=int(env.get("num_turns", 0)),
        duration_ms=int(env.get("duration_ms", 0)),
        timed_out=False,
        raw_envelope=env,
        error=None if not env.get("is_error") else env.get("result", "agent reported error"),
    )
