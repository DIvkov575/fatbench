"""The two arms of the comparison — this is the whole point of the experiment.

Both arms invoke the SAME `claude -p` binary on the SAME checked-out repo with the SAME prompt
(the SWE-bench `problem_statement`). The ONLY thing that differs is what Claude Code loads:

  - raw   : a stripped agent. An isolated empty CLAUDE_CONFIG_DIR (no global CLAUDE.md, no
            plugins, no skills, no user settings) + `--strict-mcp-config` with no --mcp-config
            (no MCP servers). This is "vanilla claude" — the control.
  - mine  : `claude -p` under the user's real environment — whatever plugins, skills, MCP
            servers, hooks, and CLAUDE.md they actually run with. The treatment.

Auth note (validated 2026-07-12): `claude --bare` would be the obvious "clean" switch, but it
forces ANTHROPIC_API_KEY/apiKeyHelper auth and disables Bedrock — and this machine authenticates
via Bedrock (CLAUDE_CODE_USE_BEDROCK=1 + awsCredentialExport). So instead of --bare we isolate
via an empty CLAUDE_CONFIG_DIR and carry ONLY the Bedrock credential export into a minimal
--settings file. That keeps auth working while loading none of the user's config. A smoke test
(empty config dir + --strict-mcp-config + minimal settings) authenticated and answered correctly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Arm:
    """How to invoke `claude -p` for one arm. `name` becomes SWE-bench's model_name_or_path."""

    name: str
    extra_args: list[str] = field(default_factory=list)
    env: dict | None = None            # None => inherit this process's env (the "mine" arm)
    description: str = ""


# Env keys that carry Bedrock auth (must survive into the isolated raw arm, or it can't call the
# model). Everything else about the user's environment is intentionally dropped for `raw`.
_AUTH_ENV_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_REGION",
    "AWS_PROFILE",
    "AWS_DEFAULT_REGION",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "AWS_BEARER_TOKEN_BEDROCK",
)


def build_raw_arm(config_dir: Path, aws_credential_export: str | None,
                  model: str | None = None) -> Arm:
    """A stripped 'vanilla claude' arm: isolated empty config, no plugins/skills/MCP/CLAUDE.md.

    `config_dir` must be an (ideally empty) directory used as CLAUDE_CONFIG_DIR — it isolates the
    agent from ~/.claude (global CLAUDE.md, installed plugins, skills, user settings). We write a
    minimal settings.json there carrying only the Bedrock credential export so auth still works.
    """
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)

    settings_path = config_dir / "settings.json"
    settings: dict = {}
    if aws_credential_export:
        settings["awsCredentialExport"] = aws_credential_export
    settings_path.write_text(json.dumps(settings, indent=2))

    # Isolated env: inherit only auth-bearing keys + the isolated config dir. Explicitly do NOT
    # inherit the user's plugin/skill paths, CLAUDECODE markers, etc.
    env = {k: os.environ[k] for k in _AUTH_ENV_KEYS if k in os.environ}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)

    extra = [
        "--strict-mcp-config",              # ignore all MCP config sources; none provided => no MCP
        "--settings", str(settings_path),   # minimal settings (auth only)
    ]
    if model:
        extra += ["--model", model]

    return Arm(
        name="raw",
        extra_args=extra,
        env=env,
        description="vanilla claude: isolated empty config, no plugins/skills/MCP/CLAUDE.md",
    )


def build_mine_arm(model: str | None = None) -> Arm:
    """The user's real setup: inherit the ambient environment; load config as normal."""
    extra = ["--model", model] if model else []
    return Arm(
        name="mine",
        extra_args=extra,
        env=None,   # inherit ambient env => plugins, skills, MCP, hooks, CLAUDE.md all load
        description="claude with the user's plugins/skills/MCP/hooks/CLAUDE.md as configured",
    )
