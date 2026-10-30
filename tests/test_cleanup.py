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
import browser_inventory as b


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.system = "Windows" if os.name == "nt" else "Darwin"
        self.desktop = "AppData/Roaming/Claude" if os.name == "nt" else "Library/Application Support/Claude"
        self.write(".claude/cache/item", b"disposable cache")
        self.write(".claude/projects/keep/session.json", b"keep this session")
        self.write(".claude/skills/test/SKILL.md", b"keep this skill")
        self.write(".claude/settings.json", b'{"env":{"API_KEY":"fake-non-target"}}')
        self.write(".claude.json", b'{"oauthAccount":{"email":"fake@example.test"},"projects":{"keep":true},"userID":"keep-id"}')
        self.write(".claude/.credentials.json", b'{"claudeAiOauth":{"accessToken":"fake-test-token"},"otherProvider":"retain"}')
        self.write(self.desktop + "/Cookies", b"fake cookie file")
        self.write(self.desktop + "/claude_desktop_config.json", b'{"mcpServers":{"keep":{}}}')
        self.write(self.desktop + "/sessions/keep", b"keep desktop session")
        self.addCleanup(patch.stopall)
        patch.object(c, "running_clients", return_value=[]).start()
        patch.object(c, "environment_check").start()
        patch.object(c, "confirm").start()
        patch("sys.stdout", new=io.StringIO()).start()

    def write(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, scopes=c.SCOPES):
        return c.make_plan(self.home, self.system, scopes)

    def apply(self, plan=None):
        return c.apply_plan(plan or self.plan(), self.home, self.system)

    def test_apply_preserves_non_targets_and_restore_round_trip(self):
        before = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        receipt = self.apply()
        self.assertFalse((self.home / ".claude/cache").exists())
        self.assertFalse((self.home / self.desktop / "Cookies").exists())
        self.assertEqual(json.loads((self.home / ".claude/.credentials.json").read_text()), {"otherProvider": "retain"})
        account = json.loads((self.home / ".claude.json").read_text())
        self.assertNotIn("oauthAccount", account)
        self.assertEqual(account["userID"], "keep-id")
        for rel in (".claude/projects/keep/session.json", ".claude/settings.json", ".claude/skills/test/SKILL.md", self.desktop + "/sessions/keep", self.desktop + "/claude_desktop_config.json"):
            self.assertEqual((self.home / rel).read_bytes(), before[rel])
        c.restore(receipt, self.home, self.system)
        for rel, data in before.items():
            self.assertEqual((self.home / rel).read_bytes(), data)

    def test_default_scope_leaves_login(self):
        self.apply(self.plan(["cache"]))
        self.assertIn("oauthAccount", json.loads((self.home / ".claude.json").read_text()))
        self.assertTrue((self.home / self.desktop / "Cookies").exists())

    def test_changed_file_invalidates_plan(self):
        plan = self.plan()
        self.write(".claude/cache/item", b"new data")
        with self.assertRaises(c.Refused):
            self.apply(plan)
        self.assertFalse((self.home / c.STATE).exists())

    def test_added_target_invalidates_plan(self):
        plan = self.plan()
        self.write(self.desktop + "/Cache/new", b"new")
        with self.assertRaises(c.Refused):
            self.apply(plan)

    def test_tampered_plan_rejected(self):
        plan = self.plan()
        plan["entries"][0]["relative"] = ".claude/projects"
        with self.assertRaises(c.Refused):
            self.apply(plan)

    def test_resealed_arbitrary_target_rejected(self):
        plan = self.plan()
        plan["entries"][0]["relative"] = ".claude/projects"
        plan["plan_id"] = c.seal({k: v for k, v in plan.items() if k != "plan_id"})
        with self.assertRaises(c.Refused):
            self.apply(plan)

    def test_confirmation_refusal_changes_nothing(self):
        with patch.object(c, "confirm", side_effect=c.Refused("declined")):
            with self.assertRaises(c.Refused):
                self.apply()
        self.assertFalse((self.home / c.STATE).exists())
        self.assertTrue((self.home / ".claude/cache/item").exists())

    def test_running_client_blocks(self):
        with patch.object(c, "running_clients", return_value=["Claude"]):
            with self.assertRaises(c.Refused):
                self.apply()

    def test_symlink_in_target_rejected(self):
        target = self.home / ".claude/cache/link"
        try:
            target.symlink_to(self.home / ".claude/projects", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation requires Windows Developer Mode")
        with self.assertRaises(c.Refused):
            self.plan()

    def test_hardlink_rejected(self):
        os.link(self.home / ".claude/cache/item", self.home / "another-link")
        with self.assertRaises(c.Refused):
            self.plan()

    def test_duplicate_json_key_rejected(self):
        self.write(".claude.json", b'{"oauthAccount":1,"oauthAccount":2}')
        with self.assertRaises(c.Refused):
            self.plan()

    def test_restore_refuses_new_data(self):
        receipt = self.apply()
        self.write(".claude/cache/new-work", b"new data must survive")
        with self.assertRaises(c.Refused):
            c.restore(receipt, self.home, self.system)
        self.assertEqual((self.home / ".claude/cache/new-work").read_bytes(), b"new data must survive")

    def test_restore_refuses_changed_json(self):
        receipt = self.apply()
        self.write(".claude.json", b'{"new_setting":true}')
        with self.assertRaises(c.Refused):
            c.restore(receipt, self.home, self.system)

    def test_corrupt_backup_blocks_restore(self):
        receipt = self.apply()
        data = json.loads(receipt.read_text())
        item = next(a for a in data["actions"] if a["entry"]["operation"] == "json")
        (receipt.parent / item["backup"]).write_bytes(b"corrupt")
        with self.assertRaises(c.Refused):
            c.restore(receipt, self.home, self.system)

    def test_backup_failure_does_not_touch_sources(self):
        actual = c.atomic_write
        def fail_backup(path, *args, **kwargs):
            if path.name.endswith(".original"):
                raise OSError("test backup failure")
            return actual(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=fail_backup):
            with self.assertRaises(c.Refused):
                self.apply()
        self.assertTrue((self.home / ".claude/cache/item").exists())
        self.assertIn("oauthAccount", json.loads((self.home / ".claude.json").read_text()))

    def test_partial_failure_has_receipt_and_can_restore(self):
        actual = c.atomic_write
        plan = self.plan()
        def fail_source(path, *args, **kwargs):
            if path == self.home / ".claude.json":
                raise OSError("test write failure")
            return actual(path, *args, **kwargs)
        with patch.object(c, "atomic_write", side_effect=fail_source):
            with self.assertRaises(c.Refused):
                self.apply(plan)
        receipt = c.batch_path(self.home, plan) / "receipt.json"
        self.assertEqual(json.loads(receipt.read_text())["status"], "partial")
        c.restore(receipt, self.home, self.system)
        self.assertTrue((self.home / ".claude/cache/item").exists())

    def test_existing_batch_cannot_be_reused(self):
        plan = self.plan()
        receipt = self.apply(plan)
        c.restore(receipt, self.home, self.system)
        with self.assertRaises(c.Refused):
            self.apply(plan)

    def test_forged_receipt_path_rejected(self):
        receipt = self.apply()
        clone = self.write("copied-receipt.json", receipt.read_bytes())
        with self.assertRaises(c.Refused):
            c.restore(clone, self.home, self.system)

    def test_receipt_cannot_redirect_backup(self):
        receipt = self.apply()
        data = json.loads(receipt.read_text())
        data["actions"][0]["backup"] = "../../outside"
        receipt.write_text(json.dumps(data))
        with self.assertRaises(c.Refused):
            c.restore(receipt, self.home, self.system)

    @unittest.skipUnless(os.name == "nt", "native Windows junction check")
    def test_windows_junction_is_rejected(self):
        link = self.home / ".claude/cache/junction"
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(self.home / ".claude/projects")], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        with self.assertRaises(c.Refused):
            self.plan()

    @unittest.skipUnless(os.name == "nt", "native Windows recovery ACL check")
    def test_windows_recovery_file_acl_is_private(self):
        receipt = self.apply()
        env = dict(os.environ)
        env["CHECK_BATCH"] = str(receipt.parent)
        script = r"""$ErrorActionPreference='Stop';
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
foreach($item in (Get-ChildItem -LiteralPath $env:CHECK_BATCH -Recurse -Force)) {
foreach($rule in (Get-Acl -LiteralPath $item.FullName).Access) {
$id=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;
if($id -ne $sid -and $id -ne 'S-1-5-18'){throw 'Unexpected recovery access'}}}"""
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


class OtherTests(unittest.TestCase):
    def test_cli_rejects_bypass_flags(self):
        script = Path(c.__file__)
        result = subprocess.run([sys.executable, str(script), "apply", "fake-plan.json", "--yes"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_non_tty_confirmation_is_rejected(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(c.Refused):
                c.confirm("APPLY abc")

    def test_inventory_does_not_read_cookies_or_leak_account_name(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            root = home / "Library/Application Support/Google/Chrome"
            (root / "Default").mkdir(parents=True)
            secret = b"fake-cookie-never-read"
            (root / "Default/Cookies").write_bytes(secret)
            (root / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": {"user_name": "private@example.test"}}}}))
            output = json.dumps(b.inventory(home, "Darwin"))
            self.assertIn('"profile": "Default"', output)
            self.assertNotIn("private@example.test", output)
            self.assertNotIn("fake-cookie", output)
            self.assertEqual((root / "Default/Cookies").read_bytes(), secret)

    def test_firefox_external_profile_not_scanned(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            root = home / ".mozilla/firefox"
            root.mkdir(parents=True)
            (root / "profiles.ini").write_text("[Profile0]\nPath=../../../../outside\nIsRelative=1\n")
            output = b.inventory(home, "Linux")
            self.assertEqual(output["profiles"][0]["status"], "blocked-profile-path")


if __name__ == "__main__":
    unittest.main()
