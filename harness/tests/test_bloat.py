"""bloat tests: inflation grows files, git-restore reverts, oracle-safety guard in task loader."""

import subprocess
from pathlib import Path

import pytest

from harness import bloat


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _mk_repo(tmp_path: Path) -> tuple[Path, str]:
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "zerver").mkdir()
    (tmp_path / "zerver" / "decoy.py").write_text("def real():\n    return 1\n")
    (tmp_path / "docs.md").write_text("# Docs\nshort.\n")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-qm", "init"], tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                         capture_output=True, text=True).stdout.strip()
    return tmp_path, sha


def test_inflate_grows_files_and_stays_parseable(tmp_path):
    ws, _ = _mk_repo(tmp_path)
    py = ws / "zerver" / "decoy.py"
    before = py.stat().st_size
    done = bloat.inflate_files(ws, ["zerver/decoy.py", "docs.md"], target_tokens_per_file=2000)
    assert set(done) == {"zerver/decoy.py", "docs.md"}
    assert py.stat().st_size > before + 4000  # ~2000 tokens ~ 8000 chars, well over
    # still valid python (filler is comments + a docstring var)
    compile(py.read_text(), "decoy.py", "exec")


def test_missing_bloat_file_is_skipped(tmp_path):
    ws, _ = _mk_repo(tmp_path)
    done = bloat.inflate_files(ws, ["zerver/decoy.py", "nope/gone.py"], 500)
    assert done == ["zerver/decoy.py"]


def test_restore_reverts_bloat_to_parent(tmp_path):
    ws, sha = _mk_repo(tmp_path)
    orig = (ws / "zerver" / "decoy.py").read_text()
    bloat.inflate_files(ws, ["zerver/decoy.py"], 3000)
    assert (ws / "zerver" / "decoy.py").read_text() != orig
    bloat.restore_files(ws, sha, ["zerver/decoy.py"])
    assert (ws / "zerver" / "decoy.py").read_text() == orig  # pristine again


def test_restore_also_strips_agent_edits_to_decoy(tmp_path):
    # A decoy edited by the agent must still revert — it's off the solution path.
    ws, sha = _mk_repo(tmp_path)
    (ws / "zerver" / "decoy.py").write_text("def real():\n    return 999  # agent scribbled\n")
    bloat.restore_files(ws, sha, ["zerver/decoy.py"])
    assert "999" not in (ws / "zerver" / "decoy.py").read_text()


def test_inflate_then_restore_leaves_no_trace_in_diff(tmp_path):
    """THE oracle-safety invariant: after inflate->restore, the collected diff is empty.

    Simulates the run.py flow: bloat the workspace, then restore before collect_diff(). If any
    bloat leaked into the diff, it would ship to the scoring container and could corrupt the
    verdict. The diff must be byte-empty (agent made no real edits here).
    """
    from harness.workspace import Workspace
    ws, sha = _mk_repo(tmp_path)
    files = ["zerver/decoy.py", "docs.md"]
    bloat.inflate_files(ws, files, target_tokens_per_file=5000)
    bloat.restore_files(ws, sha, files)
    w = Workspace(path=ws, parent_commit=sha, _tmp_root=ws)
    assert w.collect_diff().strip() == ""  # nothing leaked


def test_task_loader_rejects_bloat_overlapping_gold(tmp_path):
    from harness.task import load_task
    y = tmp_path / "bad.yaml"
    y.write_text(
        "id: x\nrepo: zulip\nparent_commit: abc\ndescription: d\n"
        "gold_patch_files: [zerver/models/realms.py]\n"
        "bloat_files: [zerver/models/realms.py]\n"
    )
    with pytest.raises(ValueError, match="overlap gold"):
        load_task(y)
