import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package


class PackageTests(unittest.TestCase):
    def test_package_determinism_allowlist_and_extracted_cli(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            for name in package.FILES:
                dest = fixture / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((root / name).read_bytes())
            (fixture / ".env").write_text("PRIVATE=must-not-be-shared")
            (fixture / "receipt.json").write_text("private paths")
            first = package.build(fixture)
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            package.build(fixture)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), digest)
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()), {"claude-local-cleanup/" + n for n in package.FILES})
                archive.extractall(fixture / "unpacked")
            entry = fixture / "unpacked/claude-local-cleanup/scripts/cleanup.py"
            result = subprocess.run([sys.executable, str(entry), "--help"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
