"""Parse changed file paths out of unified-diff text.

Handles both `git diff` and `git log -p` output (the gold `*.diff` fixtures are in
`git log -p` format — a commit header followed by per-file `diff --git` hunks). We only
need the set of touched paths and a way to split them into test vs. implementation files,
so we parse the `diff --git a/<path> b/<path>` lines rather than the hunk bodies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$")

# A path is a "test file" if any path segment is a tests dir or the basename looks like a test.
_TEST_SEG_RE = re.compile(r"(^|/)tests?(/|$)")
_TEST_NAME_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$")

_MIGRATION_RE = re.compile(r"(^|/)migrations/")


def is_test_file(path: str) -> bool:
    return bool(_TEST_SEG_RE.search(path) or _TEST_NAME_RE.search(path))


def is_migration_file(path: str) -> bool:
    return bool(_MIGRATION_RE.search(path))


@dataclass
class DiffPaths:
    """The set of paths touched by a diff, split by role."""

    all_paths: list[str] = field(default_factory=list)
    impl_paths: list[str] = field(default_factory=list)  # non-test
    test_paths: list[str] = field(default_factory=list)

    @property
    def has_migration(self) -> bool:
        return any(is_migration_file(p) for p in self.all_paths)


def split_diff_by_role(diff_text: str) -> tuple[str, str]:
    """Split unified-diff text into (impl_diff, test_diff) by per-file `diff --git` blocks.

    The agent's diff often edits BOTH implementation and test files. For grading we must apply
    only the implementation and then overlay the *gold* tests — otherwise the agent could pass
    by weakening tests (CLAUDE.md invariant). This partitions the raw diff text so each file's
    hunk lands in exactly one bucket, keyed by the same `is_test_file` rule used for scoring.
    """
    impl_parts: list[str] = []
    test_parts: list[str] = []
    cur_is_test = False
    cur: list[str] = []

    def _flush() -> None:
        if cur:
            (test_parts if cur_is_test else impl_parts).append("".join(cur))

    for line in diff_text.splitlines(keepends=True):
        m = _DIFF_GIT_RE.match(line.rstrip("\n"))
        if m:
            _flush()
            cur = [line]
            path = m.group("b") or m.group("a")
            cur_is_test = is_test_file(path)
        else:
            cur.append(line)
    _flush()
    return "".join(impl_parts), "".join(test_parts)


def parse_changed_paths(diff_text: str) -> DiffPaths:
    """Extract touched paths from unified-diff text.

    Uses the `b/` (post-image) path. For pure deletions git still emits a sensible
    `b/<path>`, so this is a faithful "set of files this diff concerns".
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for line in diff_text.splitlines():
        m = _DIFF_GIT_RE.match(line)
        if not m:
            continue
        # Prefer the b-side; fall back to a-side for safety.
        path = m.group("b") or m.group("a")
        if path not in seen_set:
            seen_set.add(path)
            seen.append(path)

    result = DiffPaths(all_paths=seen)
    for p in seen:
        if is_test_file(p):
            result.test_paths.append(p)
        else:
            result.impl_paths.append(p)
    return result
