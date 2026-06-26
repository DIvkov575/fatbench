"""Workspace setup and diff collection.

A run never touches the vendored `repos/zulip`. We create a fresh working tree pinned at the
task's parent commit, let the agent work in it, then collect its diff against that baseline.

Implementation: `git worktree` would be ideal but ties the temp tree to the source repo's
lifetime; instead we do a local clone (fast, shares objects via hardlinks on the same
filesystem) and hard-reset to the parent commit. The clone is its own throwaway repo, so the
agent's commits/diffs can't pollute the fixture.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CLAUDE_MD = "CLAUDE.md"


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True
    )


@dataclass
class Workspace:
    path: Path
    parent_commit: str
    _tmp_root: Path

    def write_claude_md(self, content: str) -> None:
        (self.path / CLAUDE_MD).write_text(content)

    def collect_diff(self, exclude_claude_md: bool = True) -> str:
        """Diff of the agent's changes vs the parent-commit baseline.

        Includes tracked modifications AND newly created files (the agent adds new
        migrations / new lib files). We stage everything first so `git diff --cached`
        captures untracked files, then diff against the baseline commit.
        """
        # Optionally drop an injected CLAUDE.md so it isn't scored as an agent edit.
        if exclude_claude_md:
            cm = self.path / CLAUDE_MD
            if cm.exists():
                cm.unlink()

        _git(["add", "-A"], cwd=self.path)
        proc = _git(
            ["diff", "--cached", "--no-color", self.parent_commit],
            cwd=self.path,
        )
        return proc.stdout

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp_root, ignore_errors=True)


def create_workspace(repo_path: str | Path, parent_commit: str, keep: bool = False) -> Workspace:
    """Clone `repo_path` into a temp dir and hard-reset to `parent_commit`."""
    repo_path = Path(repo_path).resolve()
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"{repo_path} is not a git repo")

    tmp_root = Path(tempfile.mkdtemp(prefix="fatbench-ws-"))
    work = tmp_root / "repo"

    # Local clone shares objects via hardlinks on the same fs (fast, low disk).
    subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", str(repo_path), str(work)],
        check=True, capture_output=True, text=True,
    )
    _git(["checkout", "--detach", parent_commit], cwd=work)
    _git(["reset", "--hard", parent_commit], cwd=work)
    _git(["clean", "-fdx"], cwd=work, check=False)

    return Workspace(path=work, parent_commit=parent_commit, _tmp_root=tmp_root)


def apply_diff(workspace_path: str | Path, diff_text: str) -> tuple[bool, str]:
    """Apply a unified diff to a workspace. Returns (ok, message).

    Tolerant: tries a 3-way apply, then a plain apply. Used both for the agent's diff
    (during scoring) and for overlaying gold test files.
    """
    workspace_path = Path(workspace_path)
    if not diff_text.strip():
        return True, "empty diff (no changes)"

    patch_file = workspace_path / ".fatbench_patch.diff"
    patch_file.write_text(diff_text)
    try:
        for extra in (["--3way"], []):
            proc = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", *extra, str(patch_file)],
                cwd=workspace_path, capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return True, f"applied {'with 3way' if extra else 'cleanly'}"
        return False, proc.stderr.strip() or "git apply failed"
    finally:
        patch_file.unlink(missing_ok=True)
