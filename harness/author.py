"""Task-authoring scaler: turn real merged PRs into review-ready FatBench candidates.

FatBench's methodology is "reconstruct tasks from real merged PRs" (see LLD §1). Doing that
by hand for zulip-001 took hours. This tool mechanizes the deterministic 90%:

  discover  -> query merged PRs on a repo, post knowledge-cutoff, touching N..M files
  fetch     -> pull each PR's metadata + unified diff via the GitHub API (`gh`)
  split     -> partition the diff into backend-impl / test / frontend (+ migrations)
  score     -> a cheap "fatness" heuristic (file count, directory scatter, has-migration)
  emit      -> write tasks/<id>.yaml (DRAFT) + <id>.gold-backend.diff / .gold-tests.diff /
               .frontend.diff, mirroring the zulip-001 layout

What it deliberately does NOT do (left to a human reviewer, flagged in the emitted YAML):
  - scrub solution leakage from the prompt (the PR body names files/functions/approach),
  - pick the exact gate_tests node ids (needs a run on the container — see evaluator),
  - confirm the parent commit is the right "before" state.

So the output is a *candidate*, not a finished task. The human does the last-mile review the
methodology requires ("never leak the solution"), not the mechanical diff-wrangling.

Transport: shells out to `gh api` (authenticated, 5000 req/hr) — Zulip rebase-merges, so PR
boundaries/bodies live in GitHub, not local git. No PyGithub dependency.

Usage:
    python -m harness.author discover --repo zulip/zulip --since 2025-05-01 \
        --min-files 15 --max-files 40 --limit 30
    python -m harness.author build --repo zulip/zulip --pr 34897 --id zulip-002 \
        --out tasks/
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import diffutil

# Frontend / non-backend path prefixes for Zulip. A task is "backend-only"; frontend files are
# split out and excluded (as zulip-001 did with web/). Extend per-repo as needed.
_FRONTEND_PREFIXES = ("web/", "static/", "frontend/")
_FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".hbs", ".css", ".scss")
# Docs/config noise that shouldn't count toward backend impl (kept in backend diff but flagged).
_DOC_SUFFIXES = (".md", ".txt")


def _gh(args: list[str], raw: bool = False) -> str:
    """Run a `gh` CLI command and return stdout. `raw=True` for non-JSON (e.g. .diff)."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def classify_path(path: str) -> str:
    """One of: 'frontend', 'test', 'migration', 'backend', 'doc'."""
    if path.startswith(_FRONTEND_PREFIXES) or path.endswith(_FRONTEND_SUFFIXES):
        return "frontend"
    if diffutil.is_migration_file(path):
        return "migration"
    if diffutil.is_test_file(path):
        return "test"
    if path.endswith(_DOC_SUFFIXES):
        return "doc"
    return "backend"


# ---- discover -----------------------------------------------------------------------------

@dataclass
class Candidate:
    number: int
    title: str
    merged_at: str
    changed_files: int
    additions: int
    deletions: int
    url: str

    def as_row(self) -> str:
        return (f"#{self.number:<6} files={self.changed_files:<3} "
                f"+{self.additions}/-{self.deletions:<5} {self.merged_at[:10]}  {self.title}")


def discover(repo: str, since: str, min_files: int, max_files: int,
             limit: int) -> list[Candidate]:
    """List merged PRs on `repo` merged after `since`, touching [min_files, max_files] files.

    Uses the search API for the merged+date filter, then hydrates file counts per PR (search
    results don't include changed_files). Ranked by proximity to the fat-context sweet spot.
    """
    # gh search returns lightweight items; fetch numbers, then detail each.
    query = f"repo:{repo} is:pr is:merged merged:>={since} sort:updated-desc"
    out = _gh(["api", "-X", "GET", "search/issues",
               "-f", f"q={query}", "-f", "per_page=100",
               "--jq", ".items[].number"])
    numbers = [int(n) for n in out.split()]
    cands: list[Candidate] = []
    for num in numbers:
        if len(cands) >= limit:
            break
        try:
            pr = json.loads(_gh(["api", f"repos/{repo}/pulls/{num}"]))
        except RuntimeError:
            continue
        cf = pr.get("changed_files", 0)
        if not (min_files <= cf <= max_files):
            continue
        cands.append(Candidate(
            number=num, title=pr.get("title", ""),
            merged_at=pr.get("merged_at") or "",
            changed_files=cf, additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0), url=pr.get("html_url", ""),
        ))
    return cands


# ---- build --------------------------------------------------------------------------------

@dataclass
class SplitDiff:
    backend: str = ""
    tests: str = ""
    frontend: str = ""
    backend_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    frontend_files: list[str] = field(default_factory=list)
    migration_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)


_FILE_HDR = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$")


def split_diff(diff_text: str) -> SplitDiff:
    """Partition a unified diff into backend / test / frontend by per-file `diff --git` blocks.

    Migrations count as backend (they ship in the gold solution) but are tracked separately so
    the task YAML can use the "added >=1 migration" completeness rule instead of exact names.
    """
    result = SplitDiff()
    # Break the diff into per-file chunks keyed by their post-image path.
    chunks: list[tuple[str, str]] = []
    cur_path: str | None = None
    cur_lines: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        m = _FILE_HDR.match(line.rstrip("\n"))
        if m:
            if cur_path is not None:
                chunks.append((cur_path, "".join(cur_lines)))
            cur_path = m.group("b") or m.group("a")
            cur_lines = [line]
        else:
            cur_lines.append(line)
    if cur_path is not None:
        chunks.append((cur_path, "".join(cur_lines)))

    backend_parts, test_parts, frontend_parts = [], [], []
    for path, chunk in chunks:
        kind = classify_path(path)
        if kind == "frontend":
            frontend_parts.append(chunk); result.frontend_files.append(path)
        elif kind == "test":
            test_parts.append(chunk); result.test_files.append(path)
        elif kind == "migration":
            backend_parts.append(chunk); result.migration_files.append(path)
            result.backend_files.append(path)
        elif kind == "doc":
            backend_parts.append(chunk); result.doc_files.append(path)
            result.backend_files.append(path)
        else:
            backend_parts.append(chunk); result.backend_files.append(path)
    result.backend = "".join(backend_parts)
    result.tests = "".join(test_parts)
    result.frontend = "".join(frontend_parts)
    return result


# Heuristics for leakage the human must scrub — solution hints in a PR body.
_LEAK_PATTERNS = [
    (re.compile(r"\b\w+\.py\b"), "file name"),
    (re.compile(r"`[A-Za-z_][A-Za-z0-9_]*`"), "code identifier in backticks"),
    (re.compile(r"\bdef [a-z_]+"), "function definition"),
    (re.compile(r"\bclass [A-Z]\w+"), "class name"),
]


def scan_leakage(body: str) -> list[str]:
    """Flag likely solution-leakage in a PR body so the reviewer knows what to scrub."""
    flags = []
    for pat, label in _LEAK_PATTERNS:
        hits = pat.findall(body or "")
        if hits:
            sample = ", ".join(sorted(set(hits))[:5])
            flags.append(f"{label}: {sample}")
    return flags


def fatness(split: SplitDiff) -> dict:
    """Cheap selection-time signal: is this genuinely fat-context?"""
    backend_non_doc = [p for p in split.backend_files if p not in split.doc_files]
    dirs = {str(Path(p).parent) for p in backend_non_doc}
    return {
        "backend_files": len(backend_non_doc),
        "test_files": len(split.test_files),
        "dir_scatter": len(dirs),
        "has_migration": bool(split.migration_files),
        "verdict": "fat" if len(backend_non_doc) >= 12 and len(dirs) >= 4 else "thin",
    }


def _parent_commit(repo: str, pr_number: int) -> tuple[str, str]:
    """Return (merge/head sha, parent sha) for the PR. Parent = the 'before' state."""
    pr = json.loads(_gh(["api", f"repos/{repo}/pulls/{pr_number}"]))
    head_sha = pr["merge_commit_sha"] or pr["head"]["sha"]
    commit = json.loads(_gh(["api", f"repos/{repo}/commits/{head_sha}"]))
    parents = commit.get("parents", [])
    parent_sha = parents[0]["sha"] if parents else ""
    return head_sha, parent_sha


def build(repo: str, pr_number: int, task_id: str, out_dir: Path) -> dict:
    """Fetch a PR and emit a review-ready candidate task (YAML + split diffs)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pr = json.loads(_gh(["api", f"repos/{repo}/pulls/{pr_number}"]))
    diff_text = _gh(["api", f"repos/{repo}/pulls/{pr_number}",
                     "-H", "Accept: application/vnd.github.v3.diff"], raw=True)
    split = split_diff(diff_text)
    head_sha, parent_sha = _parent_commit(repo, pr_number)
    fat = fatness(split)
    leaks = scan_leakage(pr.get("body") or "")

    # Write the split diffs.
    (out_dir / f"{task_id}.gold-backend.diff").write_text(split.backend)
    (out_dir / f"{task_id}.gold-tests.diff").write_text(split.tests)
    if split.frontend.strip():
        (out_dir / f"{task_id}.frontend.diff").write_text(split.frontend)

    non_migration_backend = [
        p for p in split.backend_files
        if p not in split.migration_files and p not in split.doc_files
    ]
    yaml_text = _render_yaml(
        task_id=task_id, repo=repo.split("/")[-1], pr=pr, parent_sha=parent_sha,
        head_sha=head_sha, backend_files=non_migration_backend,
        migration_files=split.migration_files, test_files=split.test_files,
        fat=fat, leaks=leaks,
    )
    (out_dir / f"{task_id}.yaml").write_text(yaml_text)
    return {"task_id": task_id, "parent_commit": parent_sha, "fatness": fat,
            "leakage_flags": leaks, "files": out_dir}


def _render_yaml(task_id, repo, pr, parent_sha, head_sha, backend_files, migration_files,
                 test_files, fat, leaks) -> str:
    """Emit a DRAFT task YAML mirroring zulip-001's schema, with review TODOs inline."""
    def yl(items):
        return "\n".join(f"  - {i}" for i in items) if items else "  []"
    leak_block = ("\n".join(f"#   - {f}" for f in leaks)
                  if leaks else "#   (none auto-detected — still verify by hand)")
    body = (pr.get("body") or "").strip()
    # Indent the raw PR body as a YAML block scalar; DO NOT trust it as the final prompt.
    body_indented = "\n".join("    " + ln for ln in body.splitlines()) or "    (empty)"
    return f"""\
id: {task_id}
title: {json.dumps(pr.get('title',''))}
source_pr: {json.dumps(pr.get('html_url',''))}
source_commit: "{head_sha}"
parent_commit: "{parent_sha}"
merged_date: "{(pr.get('merged_at') or '')[:10]}"
repo: {repo}
language: python
tier: TODO            # classify: easy/medium/hard
type: TODO            # e.g. T1 distributed-invariant propagation

# --- AUTO-GENERATED DRAFT — human review required before use ---------------------------
# Fatness signal: backend_files={fat['backend_files']} dir_scatter={fat['dir_scatter']} \
has_migration={fat['has_migration']} verdict={fat['verdict']}
#
# LEAKAGE TO SCRUB from `description` below (auto-detected hints — the prompt must describe
# desired BEHAVIOR only, no file/function/approach names):
{leak_block}
#
# TODO(reviewer):
#   1. Rewrite `description` from the PR body into an outcome-only spec (strip the above).
#   2. Pin `gate_tests` to CLASS/MODULE node ids after a container run (test-backend can't
#      run single methods — see evaluator/CLAUDE.md).
#   3. Confirm parent_commit is the correct "before" state and is present in repos/{repo}.
#   4. Set tier/type and fill minimum_reading_set / red_herrings (selection-time docs).

description: |
{body_indented}

# --- Gold patch (real merged diff), backend only ---
gold_patch_files:
{yl(backend_files)}
{yl(migration_files) if migration_files else ''}
# Migration filenames are gold-specific; completeness matches non-migration files +
# "added >=1 migration" rather than exact names.

gold_test_files:
{yl(test_files)}

gate_tests:
  # TODO: finalize at class/module granularity after a container run.
{yl([t + '::TODO_ClassName' for t in test_files])}

regression_command: |
  # TODO: scope to the modules most likely affected.
  ./tools/test-backend {' '.join(sorted({p.replace('/', '.').removesuffix('.py') for p in test_files}))}

token_budget: 1200000
wall_clock_cap_seconds: 2700
"""


# ---- CLI ----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FatBench task-authoring scaler (PR -> candidate task).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="list fat-context candidate PRs")
    d.add_argument("--repo", required=True, help="owner/name, e.g. zulip/zulip")
    d.add_argument("--since", required=True, help="merged-after date, YYYY-MM-DD (post-cutoff)")
    d.add_argument("--min-files", type=int, default=15)
    d.add_argument("--max-files", type=int, default=40)
    d.add_argument("--limit", type=int, default=30)

    b = sub.add_parser("build", help="emit a review-ready candidate task from one PR")
    b.add_argument("--repo", required=True)
    b.add_argument("--pr", type=int, required=True)
    b.add_argument("--id", required=True, help="task id, e.g. zulip-002")
    b.add_argument("--out", default="tasks/", help="output dir (default: tasks/)")

    args = ap.parse_args(argv)

    if args.cmd == "discover":
        cands = discover(args.repo, args.since, args.min_files, args.max_files, args.limit)
        if not cands:
            print("no candidates in range", file=sys.stderr)
            return 1
        for c in cands:
            print(c.as_row())
        print(f"\n{len(cands)} candidate(s). Build one: "
              f"python -m harness.author build --repo {args.repo} --pr <N> --id <id>",
              file=sys.stderr)
        return 0

    if args.cmd == "build":
        info = build(args.repo, args.pr, args.id, Path(args.out))
        print(json.dumps(info, indent=2, default=str))
        if info["leakage_flags"]:
            print("\n[author] REVIEW REQUIRED: prompt likely leaks the solution — scrub the "
                  "description before use (flags above).", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
