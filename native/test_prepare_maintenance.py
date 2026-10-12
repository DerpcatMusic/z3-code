import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("prepare", Path(__file__).with_name("prepare_maintenance.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class MaintenanceTests(unittest.TestCase):
    def source(self, directory):
        root = Path(directory) / "source"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "fixture-base", str(root)], check=True)
        for key, value in [("user.name", "Test"), ("user.email", "test@example.com")]:
            prepare.git(root, "config", key, value)
        (root / "native").mkdir()
        (root / "native/tracked.rs").write_text("old\n")
        (root / ".gitignore").write_text(".env.local\n")
        prepare.git(root, "add", ".")
        prepare.git(root, "commit", "-m", "base")
        return root

    def test_snapshot_preserves_dirty_and_untracked_code_without_touching_source_index(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            original_head = prepare.git(source, "rev-parse", "HEAD")
            (source / "native/tracked.rs").write_text("new\n")
            (source / "native/feature.rs").write_text("new feature\n")
            (source / ".env.local").write_text("PRIVATE_SECRET=fake-test-secret\n")
            (source / "unrelated.txt").write_text("do not publish\n")
            before = prepare.git(source, "status", "--porcelain")
            destination = Path(directory) / "maintenance"
            prepare.snapshot(source, destination)
            self.assertEqual((destination / "native/tracked.rs").read_text(), "new\n")
            self.assertEqual((destination / "native/feature.rs").read_text(), "new feature\n")
            self.assertFalse((destination / ".env.local").exists())
            self.assertFalse((destination / "unrelated.txt").exists())
            self.assertEqual(prepare.git(source, "status", "--porcelain"), before)
            self.assertEqual(prepare.git(source, "rev-parse", "HEAD"), original_head)
            self.assertFalse((destination / ".git/objects/info/alternates").exists())

    def test_repeated_setup_keeps_existing_maintenance_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            destination = Path(directory) / "maintenance"
            prepare.snapshot(source, destination)
            (destination / "native/tracked.rs").write_text("maintenance work\n")
            prepare.snapshot(source, destination)
            self.assertEqual((destination / "native/tracked.rs").read_text(), "maintenance work\n")

    def test_source_objects_are_independent_of_original_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            destination = Path(directory) / "maintenance"
            head = prepare.snapshot(source, destination)
            source.rename(source.with_name("removed-source"))
            self.assertEqual(prepare.git(destination, "rev-parse", "HEAD").decode().strip(), head)
            self.assertIn(b"old", prepare.git(destination, "show", "HEAD:native/tracked.rs"))

    def test_existing_foreign_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            destination = Path(directory) / "maintenance"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "not a Git checkout"):
                prepare.snapshot(source, destination)


if __name__ == "__main__":
    unittest.main()
