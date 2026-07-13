"""task loader tests, against the real task files."""

from pathlib import Path

from harness.task import load_task

ROOT = Path(__file__).resolve().parent.parent.parent


def test_load_zulip_task():
    t = load_task(ROOT / "tasks" / "zulip-001.yaml")
    assert t.id == "zulip-001"
    assert t.repo == "zulip"
    assert t.parent_commit == "8fb1eeeb0985173d81806a944d87327c850082ca"
    assert "zerver/models/realms.py" in t.gold_patch_files
    assert "zerver/tests/test_realm.py" in t.gold_test_files
    assert t.gate_tests, "must define gate tests"
    assert t.token_budget == 1_200_000
    # description must not leak file paths from the gold solution.
    assert "zerver/models/realms.py" not in t.description


