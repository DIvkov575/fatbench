"""task + config loaders, against the real task/config files."""

from pathlib import Path

from harness.config import Config, load_config
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


def test_no_context_run_injects_nothing():
    c = Config.none()
    assert c.name == "no-context" and c.claude_md is None and not c.writes_claude_md


def test_bring_your_own_context_from_claude_md():
    # The platform ships no context configs; a user brings a CLAUDE.md. Sample lives in examples/.
    md = ROOT / "examples" / "experiments" / "zulip-backend-onboarding.CLAUDE.md"
    c = Config.from_claude_md(md)
    assert c.name == "zulip-backend-onboarding"
    assert c.writes_claude_md
    assert "test-backend" in c.claude_md
    # A well-formed context doc must NOT leak a specific task's solution (e.g. the setting name).
    assert "topics_policy" not in c.claude_md
    # explicit name override
    assert Config.from_claude_md(md, name="exp-A").name == "exp-A"
