"""author tests: path classification, diff splitting, fatness, leakage scan (all offline)."""

from harness.author import (
    SplitDiff,
    classify_path,
    fatness,
    scan_leakage,
    split_diff,
)


def test_classify_path():
    assert classify_path("zerver/models/realms.py") == "backend"
    assert classify_path("zerver/tests/test_realm.py") == "test"
    assert classify_path("zerver/migrations/0710_realm_topics_policy.py") == "migration"
    assert classify_path("web/src/settings.ts") == "frontend"
    assert classify_path("static/js/foo.js") == "frontend"
    assert classify_path("docs/foo.md") == "doc"


_SAMPLE_DIFF = """\
diff --git a/zerver/models/realms.py b/zerver/models/realms.py
index 1..2 100644
--- a/zerver/models/realms.py
+++ b/zerver/models/realms.py
@@ -1 +1 @@
-old
+new
diff --git a/zerver/migrations/0710_x.py b/zerver/migrations/0710_x.py
new file mode 100644
--- /dev/null
+++ b/zerver/migrations/0710_x.py
@@ -0,0 +1 @@
+migration
diff --git a/zerver/tests/test_realm.py b/zerver/tests/test_realm.py
--- a/zerver/tests/test_realm.py
+++ b/zerver/tests/test_realm.py
@@ -1 +1 @@
-t
+t2
diff --git a/web/src/settings.ts b/web/src/settings.ts
--- a/web/src/settings.ts
+++ b/web/src/settings.ts
@@ -1 +1 @@
-f
+f2
"""


def test_split_diff_partitions_by_role():
    s = split_diff(_SAMPLE_DIFF)
    assert s.backend_files == ["zerver/models/realms.py", "zerver/migrations/0710_x.py"]
    assert s.migration_files == ["zerver/migrations/0710_x.py"]
    assert s.test_files == ["zerver/tests/test_realm.py"]
    assert s.frontend_files == ["web/src/settings.ts"]
    # content routed to the right bucket
    assert "models/realms.py" in s.backend and "migrations/0710_x.py" in s.backend
    assert "test_realm.py" in s.tests
    assert "settings.ts" in s.frontend
    # each file's hunk stays intact in exactly one bucket
    assert s.frontend.count("diff --git") == 1
    assert s.backend.count("diff --git") == 2


def test_split_diff_roundtrip_file_count():
    s = split_diff(_SAMPLE_DIFF)
    total = (s.backend.count("diff --git") + s.tests.count("diff --git")
             + s.frontend.count("diff --git"))
    assert total == _SAMPLE_DIFF.count("diff --git") == 4


def test_fatness_thin_vs_fat():
    thin = split_diff(_SAMPLE_DIFF)
    f = fatness(thin)
    assert f["has_migration"] is True
    assert f["verdict"] == "thin"  # only 2 backend files, low scatter

    fat_split = SplitDiff(
        backend_files=[f"zerver/{d}/f{i}.py" for i, d in enumerate(
            ["models", "actions", "lib", "views", "lib", "actions",
             "models", "views", "lib", "actions", "models", "views"])],
    )
    ff = fatness(fat_split)
    assert ff["backend_files"] == 12 and ff["dir_scatter"] >= 4
    assert ff["verdict"] == "fat"


def test_scan_leakage_flags_solution_hints():
    body = "This adds `topics_policy` to zerver/models/realms.py via def do_set_policy."
    flags = scan_leakage(body)
    joined = " ".join(flags)
    assert "file name" in joined       # realms.py
    assert "code identifier" in joined  # `topics_policy`
    assert "function definition" in joined  # def do_set_policy
    assert scan_leakage("") == []
