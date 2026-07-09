"""Load a run config: what experiment context (if any) the agent gets.

FatBench is a benchmarking PLATFORM. The built-in control is `baseline` — vanilla Claude Code
with only the task prompt + the raw repo, no injected context. An EXPERIMENT is whatever a user
brings to test against that control: most commonly a `CLAUDE.md` (onboarding/instructions), but
the platform is agnostic about what's inside it.

Two ways to specify the experiment's CLAUDE.md:
  1. `--claude-md PATH` on the CLI (bring-your-own; no YAML needed), or
  2. a config YAML with `claude_md:` (inline) or `claude_md_file:` (path relative to the YAML).

`baseline` (claude_md=None) is the only config the platform ships; experiment configs/docs live
with the user (see `examples/experiments/` for a sample).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    name: str
    claude_md: str | None  # CLAUDE.md content to inject, or None for the vanilla baseline

    @property
    def writes_claude_md(self) -> bool:
        return bool(self.claude_md)

    @classmethod
    def baseline(cls) -> "Config":
        """The built-in vanilla control: no injected context."""
        return cls(name="baseline", claude_md=None)

    @classmethod
    def from_claude_md(cls, md_path: str | Path, name: str | None = None) -> "Config":
        """Build an experiment config directly from a CLAUDE.md file (bring-your-own)."""
        md_path = Path(md_path)
        content = md_path.read_text()
        # Derive a clean label: strip .md and a trailing .CLAUDE (e.g. "exp.CLAUDE.md" -> "exp").
        stem = md_path.stem
        if stem.endswith(".CLAUDE"):
            stem = stem[: -len(".CLAUDE")]
        return cls(name=name or stem, claude_md=content or None)


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
