"""Context bloat: waste the agent's tokens without touching the oracle.

A fat-context task is harder when the agent must wade through more plausible-looking material to
find the real answer. This module inflates a per-task list of OFF-GOLD-PATH files in the agent's
workspace with verbose filler tokens, so the agent's greps/reads burn budget on them.

Oracle safety — the one hard invariant: bloat must NEVER reach the scoring container. The files
listed for bloat are, by construction, files a correct solution does NOT edit (not in
gold_patch_files / gold_test_files). The harness inflates them before invoking the agent, then
`git checkout <parent> -- <files>` restores them to pristine parent content BEFORE the diff is
collected. So:
  - the agent sees bloated files on disk -> its context/budget is spent wading through them,
  - the diff shipped to the container never contains bloat (correctness/regression untouched),
  - any agent edit to a decoy is reverted too — fine, it was off-path and gold tests decide truth.

Scoring is the SWE-bench `resolved` bit (see scorer.py); tokens are recorded but not scored. So
bloat is a STRESSOR, not a scored penalty: it changes the verdict only if it degrades the agent's
patch enough to flip resolved (gate/regression tests fail). An agent that ignores it stays resolved.

Inflation is DETERMINISTIC (no RNG) so runs are reproducible. Content is seeded from each file's
own path so it reads as file-specific rather than obvious lorem-ipsum.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ~4 chars per token is a fine rule of thumb for sizing filler.
_CHARS_PER_TOKEN = 4


def _filler_paragraph(seed: str, n: int) -> str:
    """A deterministic verbose paragraph, varied by seed so files don't look identical."""
    base = (
        f"This module ({seed}) participates in the broader subsystem and its behavior is "
        f"described here in exhaustive detail for maintainers. The following notes elaborate on "
        f"invariants, historical context, edge cases, and cross-cutting concerns that a reader "
        f"should understand before modifying anything in or around {seed}. "
    )
    # Repeat to the requested length, deterministically.
    reps = (n // len(base)) + 1
    return (base * reps)[:n]


def _inflate_python(path: Path, target_tokens: int) -> None:
    """Append a large, syntactically-valid comment+docstring block to a .py file."""
    seed = path.name
    target_chars = target_tokens * _CHARS_PER_TOKEN
    body = _filler_paragraph(seed, target_chars)
    # Keep the file importable/parseable: append as a module-level docstring + comment wall.
    block = (
        "\n\n"
        "# ---------------------------------------------------------------------------\n"
        "# Extended maintainer documentation (auto-expanded). Verbose by design.\n"
        + "".join(f"# {line}\n" for line in _wrap(body, 96))
        + '\n_EXTENDED_NOTES = """\n'
        + body
        + '\n"""\n'
    )
    with path.open("a", encoding="utf-8", errors="ignore") as f:
        f.write(block)


def _inflate_text(path: Path, target_tokens: int) -> None:
    """Pad a docs/text file with overly-verbose prose."""
    seed = path.name
    target_chars = target_tokens * _CHARS_PER_TOKEN
    body = _filler_paragraph(seed, target_chars)
    with path.open("a", encoding="utf-8", errors="ignore") as f:
        f.write("\n\n<!-- Extended documentation (auto-expanded), verbose by design. -->\n\n")
        f.write("\n\n".join(_wrap(body, 100)))
        f.write("\n")


def _wrap(text: str, width: int) -> list[str]:
    return [text[i:i + width] for i in range(0, len(text), width)]


def inflate_files(workspace_path: str | Path, files: list[str],
                  target_tokens_per_file: int) -> list[str]:
    """Inflate each existing file under `workspace_path`. Returns the paths actually bloated.

    Missing files are skipped (a task's decoy list may drift from the parent tree); the caller
    should treat a shrunken return list as a soft warning, not an error.
    """
    workspace_path = Path(workspace_path)
    done: list[str] = []
    for rel in files:
        p = workspace_path / rel
        if not p.is_file():
            continue
        if p.suffix == ".py":
            _inflate_python(p, target_tokens_per_file)
        else:
            _inflate_text(p, target_tokens_per_file)
        done.append(rel)
    return done


def restore_files(workspace_path: str | Path, parent_commit: str, files: list[str]) -> None:
    """Revert bloated files to pristine parent content before the diff is collected.

    Uses `git checkout <parent> -- <files>` so both the injected bloat AND any agent edits to
    these off-path decoys are stripped from what reaches the scoring container.
    """
    if not files:
        return
    subprocess.run(
        ["git", "checkout", parent_commit, "--", *files],
        cwd=str(workspace_path), check=False, capture_output=True, text=True,
    )
