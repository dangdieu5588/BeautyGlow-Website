"""Offline safety checks: no Docker, production files or credentials accessed."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("beautyglow_restore", ROOT / "scripts/beautyglow-backup-restore.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestoreSafety(unittest.TestCase):
    def test_utf8mb4_query_preserves_emoji_filename(self):
        value = "2026/10/DIOR-💯.jpeg"
        for container in [None, "isolated-container"]:
            with patch.object(module, "command", return_value=(value + "\n").encode("utf-8")) as run:
                self.assertEqual(module.query("SELECT meta_value FROM wp_postmeta;", container), value)
                self.assertIn("--default-character-set=utf8mb4", " ".join(run.call_args.args[0]))

    def test_unicode_upload_inventory_and_archive_roundtrip(self):
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uploads = root / "uploads"
            uploads.mkdir()
            (uploads / "DIOR-💯.jpeg").write_bytes(b"fake image fixture")
            archive = root / "uploads.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(uploads, arcname="uploads")
            restored = root / "restore"
            restored.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(restored, filter="data")
            self.assertEqual(module.inventory(uploads), module.inventory(restored / "uploads"))

    def test_production_writes_and_multi_statements_are_rejected(self):
        with patch.object(module, "command") as run:
            for sql in ["CREATE TABLE forbidden (id INT);", "SELECT 1; CREATE TABLE forbidden (id INT);", "SELECT 1 INTO OUTFILE '/tmp/x';"]:
                with self.assertRaises(RuntimeError):
                    module.query(sql)
            run.assert_not_called()

    def test_only_redundant_column_charset_is_normalized(self):
        original = b"  `title` text COLLATE utf8mb4_unicode_520_ci,\n"
        explicit = b"  `title` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci,\n"
        self.assertEqual(module.normalize_dump(original), module.normalize_dump(explicit))
        self.assertNotEqual(module.normalize_dump(original), module.normalize_dump(explicit.replace(b"text", b"longtext")))

    def test_row_values_are_never_normalized(self):
        row = b"INSERT INTO `test` VALUES (' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci');\n"
        self.assertEqual(module.normalize_dump(row), row)

    def test_default_is_plan_without_docker(self):
        with patch("sys.argv", ["backup"]), patch.object(module, "command") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                module.main()
            run.assert_not_called()

    def test_reuse_existing_project_rejected_before_any_inspect(self):
        with patch("sys.argv", ["backup", "--execute"]), patch.object(module.os, "geteuid", return_value=0):
            with patch.object(module, "command", return_value=b"existing-container"), patch.object(module, "inspect") as inspect:
                with self.assertRaisesRegex(RuntimeError, "refuse reuse"):
                    module.main()
                inspect.assert_not_called()

    def test_inventory_checks_content_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "image.txt").write_bytes(b"offline fixture")
            first = module.inventory(path)
            self.assertEqual(first["image.txt"]["bytes"], 15)
            (path / "image.txt").write_bytes(b"different bytes")
            self.assertNotEqual(first, module.inventory(path))

    def test_inventory_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "link").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(RuntimeError, "Unexpected uploads entry"):
                module.inventory(path)

    def test_unsafe_backup_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            path.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "Unsafe backup directory"):
                module.protected_directory(path)

    def test_restore_compose_cannot_reach_production(self):
        config = yaml.safe_load((ROOT / "compose.restore-test.yaml").read_text())
        service = config["services"]["mysql"]
        self.assertEqual(config["name"], "beautyglow-restore-test")
        self.assertEqual(service["networks"], ["isolated"])
        self.assertNotIn("ports", service)
        self.assertEqual(service["volumes"], ["restore_mysql_data:/var/lib/mysql"])
        self.assertTrue(config["networks"]["isolated"]["internal"])
        self.assertEqual(config["volumes"]["restore_mysql_data"]["name"], "beautyglow-restore-test_mysql_data")
        self.assertEqual(service["pull_policy"], "never")
        self.assertNotIn("MYSQL_ROOT_PASSWORD", service["environment"])
        self.assertIn("MYSQL_ROOT_PASSWORD_FILE", service["environment"])

    def test_production_query_argv_has_only_password_reference(self):
        argv = module.source_client("mysqldump", ["--single-transaction"])
        self.assertIn(module.SOURCE, argv)
        self.assertIn('$MYSQL_ROOT_PASSWORD', " ".join(argv))
        with self.assertRaises(AssertionError):
            module.source_client("arbitrary-command")


if __name__ == "__main__":
    unittest.main()
