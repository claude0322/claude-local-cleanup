"""Regression tests for ACL inheritance and writes at transaction boundaries."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cleanup as c
import safe_io


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name).resolve()
        self.system = "Windows" if os.name == "nt" else "Darwin"
        self.source = self.fixture / ".claude.json"
        self.original = b'{"oauthAccount":{"email":"fake@example.test"},"setting":"old"}'
        self.source.write_bytes(self.original)
        self.addCleanup(patch.stopall)
        patch.object(c, "confirm").start()
        patch.object(c, "require_stopped").start()
        patch.object(c, "environment_check").start()
        patch("sys.stdout", new=io.StringIO()).start()

    def plan(self):
        return c.make_plan(self.fixture, self.system, ["code-login"])

    def apply(self, plan=None):
        return c.apply_plan(plan or self.plan(), self.fixture, self.system)

    def receipt(self, plan):
        return c.batch_path(self.fixture, plan) / "receipt.json"

    def test_exclusive_move_preserves_existing_file_and_empty_directory(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                left, right = self.fixture / f"left-{directory}", self.fixture / f"right-{directory}"
                if directory:
                    left.mkdir()
                    right.mkdir()
                else:
                    left.write_bytes(b"old")
                    right.write_bytes(b"new")
                with self.assertRaises(FileExistsError):
                    safe_io.rename_noreplace(left, right)
                self.assertTrue(left.exists())
                self.assertTrue(right.exists())
                if not directory:
                    self.assertEqual(right.read_bytes(), b"new")

    def test_new_output_collision_is_not_overwritten(self):
        destination = self.fixture / "plan.json"
        move = c.rename_noreplace
        def concurrent_create(source, target):
            if target == destination:
                destination.write_bytes(b"new user file")
            return move(source, target)
        with patch.object(c, "rename_noreplace", side_effect=concurrent_create):
            with self.assertRaises(FileExistsError):
                c.atomic_write(destination, b"planned output", self.fixture, overwrite=False)
        self.assertEqual(destination.read_bytes(), b"new user file")

    def test_json_change_after_receipt_update_is_preserved(self):
        plan = self.plan()
        save = c.save_json
        triggered = []
        def concurrent_write(path, value, *args, **kwargs):
            result = save(path, value, *args, **kwargs)
            actions = value.get("actions", [])
            if not triggered and actions and actions[0].get("after"):
                triggered.append(True)
                self.source.write_bytes(b'{"setting":"concurrent"}')
            return result
        with patch.object(c, "save_json", side_effect=concurrent_write):
            with self.assertRaises(c.Refused):
                self.apply(plan)
        self.assertTrue(triggered)
        self.assertEqual(self.source.read_bytes(), b'{"setting":"concurrent"}')
        self.assertEqual(json.loads(self.receipt(plan).read_text(encoding="utf-8"))["status"], "partial")

    def test_json_change_between_comparison_and_capture_is_returned(self):
        move = c.rename_noreplace
        def concurrent_write(source, destination):
            if source == self.source and destination.name.endswith(".before-edit"):
                self.source.write_bytes(b'{"setting":"concurrent"}')
            return move(source, destination)
        with patch.object(c, "rename_noreplace", side_effect=concurrent_write):
            with self.assertRaises(c.Refused):
                self.apply()
        self.assertEqual(self.source.read_bytes(), b'{"setting":"concurrent"}')

    def test_json_recreated_after_capture_is_not_overwritten(self):
        plan = self.plan()
        write = c.atomic_write
        def concurrent_create(path, *args, **kwargs):
            if path == self.source:
                self.source.write_bytes(b'{"setting":"new after capture"}')
            return write(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=concurrent_create):
            with self.assertRaises(c.Refused):
                self.apply(plan)
        self.assertEqual(self.source.read_bytes(), b'{"setting":"new after capture"}')
        batch = self.receipt(plan).parent
        self.assertEqual((batch / "000.original.before-edit").read_bytes(), self.original)
        self.assertEqual((batch / "000.original").read_bytes(), self.original)

    def test_apply_interruption_after_capture_can_restore_missing_json(self):
        plan = self.plan()
        write = c.atomic_write
        def interrupted(path, *args, **kwargs):
            if path == self.source:
                raise OSError("synthetic interruption")
            return write(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=interrupted):
            with self.assertRaises(c.Refused):
                self.apply(plan)
        receipt = self.receipt(plan)
        self.assertFalse(self.source.exists())
        data = c.load_receipt(receipt, self.fixture, self.system)
        self.assertEqual(c.recovery_check(data, receipt.parent, self.fixture), ["restore-missing"])
        c.restore(receipt, self.fixture, self.system)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_restore_interruption_after_capture_is_resumable(self):
        receipt = self.apply()
        write = c.atomic_write
        def interrupted(path, *args, **kwargs):
            if path == self.source:
                raise OSError("synthetic interruption")
            return write(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=interrupted):
            with self.assertRaises(c.Refused):
                c.restore(receipt, self.fixture, self.system)
        self.assertFalse(self.source.exists())
        c.restore(receipt, self.fixture, self.system)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_restore_json_concurrent_creation_preserves_both_versions(self):
        receipt = self.apply()
        write = c.atomic_write
        def concurrent_create(path, *args, **kwargs):
            if path == self.source:
                self.source.write_bytes(b'{"setting":"new during restore"}')
            return write(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=concurrent_create):
            with self.assertRaises(c.Refused):
                c.restore(receipt, self.fixture, self.system)
        self.assertEqual(self.source.read_bytes(), b'{"setting":"new during restore"}')
        self.assertTrue((receipt.parent / "000.original.before-restore").is_file())
        self.assertEqual((receipt.parent / "000.original").read_bytes(), self.original)

    def test_restore_move_collision_preserves_new_and_backup_files(self):
        desktop = "AppData/Roaming/Claude" if os.name == "nt" else "Library/Application Support/Claude"
        source = self.fixture / desktop / "Cookies"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"old synthetic store")
        plan = c.make_plan(self.fixture, self.system, ["desktop-login"])
        receipt = self.apply(plan)
        move = c.rename_noreplace
        def concurrent_create(left, right):
            if right == source:
                source.write_bytes(b"new synthetic store")
            return move(left, right)
        with patch.object(c, "rename_noreplace", side_effect=concurrent_create):
            with self.assertRaises(c.Refused):
                c.restore(receipt, self.fixture, self.system)
        self.assertEqual(source.read_bytes(), b"new synthetic store")
        self.assertEqual((receipt.parent / "000.original").read_bytes(), b"old synthetic store")

    def test_late_writer_to_captured_inode_is_retained_and_blocks_restore(self):
        receipt = self.apply()
        retained = receipt.parent / "000.original.before-edit"
        retained.write_bytes(b'{"setting":"late write to retained inode"}')
        with self.assertRaises(c.Refused):
            c.restore(receipt, self.fixture, self.system)
        self.assertIn(b"late write", retained.read_bytes())
        self.assertNotIn(b"oauthAccount", self.source.read_bytes())

    def test_legacy_receipt_can_restore_but_legacy_plan_cannot_apply(self):
        plan = self.plan()
        plan["version"] = "1.0.0"
        plan["plan_id"] = c.seal({k: v for k, v in plan.items() if k != "plan_id"})
        with self.assertRaises(c.Refused):
            self.apply(plan)
        batch = c.batch_path(self.fixture, plan)
        c.private_dir(batch, self.fixture)
        c.atomic_write(batch / "000.original", self.original, self.fixture, overwrite=False)
        self.source.write_bytes(b'{"setting":"old"}')
        receipt = {"tool": "claude-local-cleanup", "plan": plan, "status": "complete",
                   "actions": [{"entry": plan["entries"][0], "backup": "000.original", "status": "done",
                                "after": c.snapshot(self.source, self.fixture)}]}
        path = batch / "receipt.json"
        c.save_json(path, receipt, self.fixture)
        c.restore(path, self.fixture, self.system)
        self.assertEqual(self.source.read_bytes(), self.original)

    @unittest.skipUnless(sys.platform == "darwin", "native macOS ACL regression")
    def test_macos_inherited_acl_removed_from_backups_and_replacement(self):
        rule = "everyone allow read,execute,readattr,readextattr,readsecurity,file_inherit,directory_inherit"
        subprocess.run(["/bin/chmod", "+a", rule, str(self.fixture)], check=True, capture_output=True)
        receipt = self.apply()
        paths = [self.fixture / c.STATE, receipt.parent, self.source,
                 *[p for p in receipt.parent.rglob("*") if p.is_file()]]
        for path in paths:
            listing = subprocess.check_output(["/bin/ls", "-lde", str(path)], text=True)
            self.assertNotIn("everyone", listing)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    @unittest.skipUnless(os.name != "nt", "POSIX descriptor permissions")
    def test_permission_failure_does_not_publish_sensitive_bytes(self):
        path = self.fixture / "new-sensitive-file"
        with patch.object(c, "private_fd", side_effect=OSError("synthetic ACL failure")):
            with self.assertRaises(OSError):
                c.atomic_write(path, b"fake-secret", self.fixture, overwrite=False)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.fixture.glob(".cleanup-*")), [])

    @unittest.skipUnless(os.name == "nt", "native Windows command resolution")
    def test_windows_commands_ignore_working_directory_and_environment(self):
        (self.fixture / "tasklist.exe").write_bytes(b"not an executable")
        (self.fixture / "powershell.exe").write_bytes(b"not an executable")
        previous = Path.cwd()
        try:
            os.chdir(self.fixture)
            with patch.dict(os.environ, {"PATH": str(self.fixture), "SystemRoot": str(self.fixture)}):
                for name in ("tasklist", "powershell"):
                    command = Path(safe_io.system_executable(name))
                    self.assertTrue(command.is_absolute())
                    self.assertNotIn(self.fixture, command.parents)
            result = subprocess.run([safe_io.system_executable("tasklist"), "/FO", "CSV", "/NH"], capture_output=True)
            self.assertEqual(result.returncode, 0)
            safe_io.private_paths([self.source])
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
