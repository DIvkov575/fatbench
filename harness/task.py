"""Load a FatBench task definition (tasks/<id>.yaml).

The task YAML schema is documented in LLD.md §4. We surface only the fields the harness
consumes; the rest (minimum_reading_set, red_herrings, attributes) are selection-time
documentation and intentionally NOT exposed to the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Task:
    id: str
    repo: str
    parent_commit: str
    description: str
    gold_patch_files: list[str]   # non-test files the real PR changed
    gold_test_files: list[str]    # test files the real PR changed (the gates' source)
    gate_tests: list[str]         # specific test ids that must pass for correctness > 0
    regression_command: str
    token_budget: int
    wall_clock_cap_seconds: int

    # Context-bloat: off-gold-path files inflated with filler in the agent's workspace to waste
    # tokens (efficiency knob). Reverted to pristine parent content before the diff is scored,
    # so they NEVER reach the oracle. Must not overlap gold_patch_files / gold_test_files.
    bloat_files: list[str]
    bloat_tokens_per_file: int

    # Raw dict for anything the harness doesn't model explicitly.
    raw: dict

    @property
    def gold_backend_diff_path(self) -> str:
        return f"{self.id}.gold-backend.diff"

    @property
    def gold_tests_diff_path(self) -> str:
        return f"{self.id}.gold-tests.diff"


def load_task(path: str | Path) -> Task:
    path = Path(path)
    data = yaml.safe_load(path.read_text())

    required = ["id", "repo", "parent_commit", "description", "gold_patch_files"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"{path}: task missing required fields: {missing}")

    gold_patch_files = list(data.get("gold_patch_files", []))
    gold_test_files = list(data.get("gold_test_files", []))
    bloat_files = list(data.get("bloat_files", []))

    # Oracle-safety guard: a bloat target must never be a gold/test file. If it were, reverting
    # it before scoring would strip part of the real solution and corrupt correctness.
    overlap = set(bloat_files) & (set(gold_patch_files) | set(gold_test_files))
    if overlap:
        raise ValueError(
            f"{path}: bloat_files overlap gold/test files {sorted(overlap)} — "
            "bloat must target only off-solution-path files."
        )

    return Task(
        id=data["id"],
        repo=data["repo"],
        parent_commit=data["parent_commit"],
        description=data["description"],
        gold_patch_files=gold_patch_files,
        gold_test_files=gold_test_files,
        gate_tests=list(data.get("gate_tests", [])),
        regression_command=data.get("regression_command", "").strip(),
        token_budget=int(data.get("token_budget", 0)),
        wall_clock_cap_seconds=int(data.get("wall_clock_cap_seconds", 2700)),
        bloat_files=bloat_files,
        bloat_tokens_per_file=int(data.get("bloat_tokens_per_file", 8000)),
        raw=data,
    )
