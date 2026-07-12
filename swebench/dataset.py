"""Load SWE-bench instances from the HuggingFace datasets-server (pure stdlib).

We deliberately avoid the heavy `datasets`/`pyarrow` stack — the harness is stdlib+PyYAML. The
HF rows API returns JSON, 100 rows/page, with every field we need (instance_id, repo,
base_commit, problem_statement, and the gold patch/test_patch/FAIL_TO_PASS/PASS_TO_PASS the
official grader uses). We only consume the fields needed to run the agent; grading is delegated
to the official swebench harness, which re-reads the dataset by instance_id.

A cached copy is written to swebench/data/<dataset>.json so runs are reproducible offline.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
_ROWS_API = "https://datasets-server.huggingface.co/rows"
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"


@dataclass
class Instance:
    instance_id: str
    repo: str                 # "owner/name"
    base_commit: str
    problem_statement: str
    raw: dict

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}.git"


def _fetch_page(dataset: str, config: str, split: str, offset: int, length: int) -> dict:
    qs = urllib.parse.urlencode(
        {"dataset": dataset, "config": config, "split": split,
         "offset": offset, "length": length}
    )
    with urllib.request.urlopen(f"{_ROWS_API}?{qs}", timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_dataset(dataset: str = DEFAULT_DATASET, config: str = "default",
                  split: str = "test", cache: bool = True) -> list[dict]:
    """Fetch all rows for a dataset split, paging 100 at a time. Cached to disk."""
    cache_path = DATA_DIR / f"{dataset.replace('/', '__')}.json"
    if cache and cache_path.exists():
        return json.loads(cache_path.read_text())

    first = _fetch_page(dataset, config, split, 0, 100)
    total = int(first.get("num_rows_total", 0))
    rows = [r["row"] for r in first["rows"]]
    offset = len(rows)
    while offset < total:
        page = _fetch_page(dataset, config, split, offset, 100)
        batch = [r["row"] for r in page["rows"]]
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)

    if cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(rows))
    return rows


def _to_instance(row: dict) -> Instance:
    return Instance(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        raw=row,
    )


def load_instances(dataset: str = DEFAULT_DATASET, *, limit: int | None = None,
                   instance_ids: list[str] | None = None,
                   cache: bool = True) -> list[Instance]:
    """Load instances, optionally filtered to specific ids and/or truncated to `limit`.

    Selection is deterministic: rows are returned in dataset order (stable across runs), so
    `limit=25` always yields the same 25-instance pilot. Explicit `instance_ids` override `limit`.
    """
    rows = fetch_dataset(dataset, cache=cache)
    instances = [_to_instance(r) for r in rows]

    if instance_ids:
        wanted = set(instance_ids)
        picked = [i for i in instances if i.instance_id in wanted]
        found = {i.instance_id for i in picked}
        missing = wanted - found
        if missing:
            raise ValueError(f"instance_ids not in {dataset}: {sorted(missing)}")
        return picked

    if limit is not None:
        return instances[:limit]
    return instances
