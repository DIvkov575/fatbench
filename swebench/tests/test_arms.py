"""The arm definitions ARE the experiment: verify raw is isolated and mine inherits."""

import json

from swebench.arms import build_bare_arm, build_mine_arm, build_raw_arm


def test_raw_arm_is_isolated(tmp_path):
    arm = build_raw_arm(
        config_dir=tmp_path / "cfg",
        aws_credential_export='"/path/to/claude" default-credential-export',
        model="global.anthropic.claude-opus-4-8",
    )
    assert arm.name == "raw"
    # Isolated config dir set, and it's NOT the user's ~/.claude.
    assert arm.env is not None
    assert arm.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    # MCP fully suppressed: strict flag present, no --mcp-config provided.
    assert "--strict-mcp-config" in arm.extra_args
    assert "--mcp-config" not in arm.extra_args
    # Model pinned.
    assert "--model" in arm.extra_args
    # A minimal settings.json was written carrying ONLY the credential export (no plugins etc).
    settings_path = tmp_path / "cfg" / "settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    assert set(settings.keys()) <= {"awsCredentialExport"}
    assert "awsCredentialExport" in settings


def test_raw_arm_carries_only_auth_env(tmp_path, monkeypatch):
    # A user plugin/skill marker in the ambient env must NOT reach the raw arm.
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("CLAUDECODE", "1")  # ambient marker that should be dropped
    arm = build_raw_arm(config_dir=tmp_path / "cfg", aws_credential_export=None)
    assert arm.env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert arm.env["AWS_REGION"] == "us-west-2"
    assert "CLAUDECODE" not in arm.env  # not an auth key -> dropped for the raw arm


def test_raw_arm_without_credential_export_writes_empty_settings(tmp_path):
    arm = build_raw_arm(config_dir=tmp_path / "cfg", aws_credential_export=None)
    settings = json.loads((tmp_path / "cfg" / "settings.json").read_text())
    assert settings == {}
    assert arm.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")


def test_bare_arm_uses_bare_flag_and_isolation(tmp_path):
    arm = build_bare_arm(
        config_dir=tmp_path / "barecfg",
        aws_credential_export='"/path/to/claude" default-credential-export',
        model="global.anthropic.claude-opus-4-8",
    )
    assert arm.name == "bare"
    # --bare strips skills/hooks/CLAUDE.md; --strict-mcp-config kills MCP.
    assert "--bare" in arm.extra_args
    assert "--strict-mcp-config" in arm.extra_args
    assert "--mcp-config" not in arm.extra_args
    # Isolated config dir + auth-only settings (so the toolbox can't leak rules into ~/.claude).
    assert arm.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "barecfg")
    settings = json.loads((tmp_path / "barecfg" / "settings.json").read_text())
    assert set(settings.keys()) <= {"awsCredentialExport"}


def test_mine_arm_inherits_env():
    arm = build_mine_arm(model="global.anthropic.claude-opus-4-8")
    assert arm.name == "mine"
    # env is None => invoke_claude_code inherits the ambient environment (plugins/skills/MCP load).
    assert arm.env is None
    assert arm.extra_args == ["--model", "global.anthropic.claude-opus-4-8"]


def test_mine_arm_no_model():
    arm = build_mine_arm()
    assert arm.extra_args == []
    assert arm.env is None
