"""Load an A/B run config (configs/<name>.yaml).

A config decides what onboarding context the agent gets. The MVP comparison is
`baseline` (no CLAUDE.md) vs `full-harness` (a Zulip onboarding CLAUDE.md). The CLAUDE.md
body may be inline (`claude_md:`) or a path relative to the config file (`claude_md_file:`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    name: str
    claude_md: str | None  # CLAUDE.md content to inject, or None for baseline

    @property
    def writes_claude_md(self) -> bool:
        return bool(self.claude_md)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}

    name = data.get("name", path.stem)
    claude_md = data.get("claude_md")

    if not claude_md and data.get("claude_md_file"):
        md_path = (path.parent / data["claude_md_file"]).resolve()
        claude_md = md_path.read_text()

    if claude_md is not None and not str(claude_md).strip():
        claude_md = None  # treat empty string as baseline

    return Config(name=name, claude_md=claude_md)
