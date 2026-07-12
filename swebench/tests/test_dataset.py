"""Dataset selection is deterministic and filters correctly (no network in tests)."""

import pytest

from swebench import dataset as ds

_FAKE_ROWS = [
    {"instance_id": f"repo__proj-{n}", "repo": "owner/proj",
     "base_commit": f"commit{n}", "problem_statement": f"fix bug {n}"}
    for n in range(10)
]


@pytest.fixture(autouse=True)
def _stub_fetch(monkeypatch):
    monkeypatch.setattr(ds, "fetch_dataset", lambda *a, **k: list(_FAKE_ROWS))


def test_limit_is_deterministic_prefix():
    a = ds.load_instances(limit=3)
    b = ds.load_instances(limit=3)
    assert [i.instance_id for i in a] == [i.instance_id for i in b]
    assert [i.instance_id for i in a] == ["repo__proj-0", "repo__proj-1", "repo__proj-2"]


def test_no_limit_returns_all():
    assert len(ds.load_instances()) == 10


def test_instance_ids_filter_overrides_limit():
    picked = ds.load_instances(limit=2, instance_ids=["repo__proj-5", "repo__proj-7"])
    assert sorted(i.instance_id for i in picked) == ["repo__proj-5", "repo__proj-7"]


def test_unknown_instance_id_raises():
    with pytest.raises(ValueError, match="not in"):
        ds.load_instances(instance_ids=["nope__missing-1"])


def test_instance_fields_and_repo_url():
    inst = ds.load_instances(limit=1)[0]
    assert inst.repo == "owner/proj"
    assert inst.base_commit == "commit0"
    assert inst.problem_statement == "fix bug 0"
    assert inst.repo_url == "https://github.com/owner/proj.git"
