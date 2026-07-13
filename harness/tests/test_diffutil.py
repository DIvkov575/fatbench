"""diffutil tests, run against the real gold diff fixtures."""

from pathlib import Path

import pytest

from harness import diffutil

TASKS = Path(__file__).resolve().parent.parent.parent / "tasks"


def test_is_test_file():
    assert diffutil.is_test_file("zerver/tests/test_realm.py")
    assert diffutil.is_test_file("zerver/lib/foo_test.py")
    assert not diffutil.is_test_file("zerver/models/realms.py")
    assert not diffutil.is_test_file("zerver/lib/events.py")


def test_is_migration_file():
    assert diffutil.is_migration_file("zerver/migrations/0710_realm_topics_policy.py")
    assert not diffutil.is_migration_file("zerver/models/realms.py")


def test_parse_gold_backend_diff_has_no_test_files():
    text = (TASKS / "zulip-001.gold-backend.diff").read_text()
    paths = diffutil.parse_changed_paths(text)
    assert paths.all_paths, "should find changed files"
    assert paths.test_paths == [], "gold backend diff must contain no test files"
    assert "zerver/models/realms.py" in paths.impl_paths
    assert paths.has_migration
    # 4 migrations in the real PR.
    assert sum(diffutil.is_migration_file(p) for p in paths.all_paths) == 4


def test_parse_gold_tests_diff_is_all_tests():
    text = (TASKS / "zulip-001.gold-tests.diff").read_text()
    paths = diffutil.parse_changed_paths(text)
    assert paths.all_paths
    assert paths.impl_paths == [], "gold tests diff should be test files only"
    assert "zerver/tests/test_realm.py" in paths.test_paths


def test_parse_handles_plain_git_diff():
    text = (
        "diff --git a/foo/bar.py b/foo/bar.py\n"
        "index 111..222 100644\n--- a/foo/bar.py\n+++ b/foo/bar.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    paths = diffutil.parse_changed_paths(text)
    assert paths.all_paths == ["foo/bar.py"]


def test_empty_diff():
    paths = diffutil.parse_changed_paths("")
    assert paths.all_paths == []
    assert not paths.has_migration


def test_split_diff_by_role_separates_impl_and_tests():
    text = (
        "diff --git a/zerver/models/realms.py b/zerver/models/realms.py\n"
        "--- a/zerver/models/realms.py\n+++ b/zerver/models/realms.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/zerver/tests/test_realm.py b/zerver/tests/test_realm.py\n"
        "--- a/zerver/tests/test_realm.py\n+++ b/zerver/tests/test_realm.py\n"
        "@@ -1 +1 @@\n-t\n+t2\n"
    )
    impl, tests = diffutil.split_diff_by_role(text)
    # impl bucket has only the model file; test bucket only the test file
    assert "models/realms.py" in impl and "test_realm.py" not in impl
    assert "test_realm.py" in tests and "models/realms.py" not in tests
    assert impl.count("diff --git") == 1 and tests.count("diff --git") == 1


def test_split_diff_by_role_all_impl_when_no_tests():
    text = (
        "diff --git a/foo/bar.py b/foo/bar.py\n"
        "--- a/foo/bar.py\n+++ b/foo/bar.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    impl, tests = diffutil.split_diff_by_role(text)
    assert impl.count("diff --git") == 1 and tests == ""
