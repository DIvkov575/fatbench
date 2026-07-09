"""The context injected into a run.

FatBench runs a test: (task, context) -> score. `context` is whatever you put in front of the
agent — most commonly a `CLAUDE.md`, but the platform is agnostic. There is no privileged
"baseline"/"control" run: injecting nothing is simply one possible context, not a reference point.
If you want to compare two setups, run two tests and diff the scores yourself.

Two ways to specify a run's context:
  1. `--claude-md PATH` on the CLI (bring-your-own; no YAML needed), or
  2. a config YAML with `claude_md:` (inline) or `claude_md_file:` (path relative to the YAML).
Omit both and the run injects no context (labeled `no-context`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    name: str
    claude_md: str | None  # CLAUDE.md content to inject, or None to inject nothing

    @property
    def writes_claude_md(self) -> bool:
        return bool(self.claude_md)

    @classmethod
    def none(cls) -> "Config":
        """A run with no injected context."""
        return cls(name="no-context", claude_md=None)

    @classmethod
    def from_claude_md(cls, md_path: str | Path, name: str | None = None) -> "Config":
        """Build a run's context directly from a CLAUDE.md file (bring-your-own)."""
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
        claude_md = None  # empty content -> no injected context

    return Config(name=name, claude_md=claude_md)
