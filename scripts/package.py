#!/usr/bin/env python3
"""Build a deterministic ZIP from an explicit source allowlist."""
import hashlib
from pathlib import Path
import zipfile

FILES = (
    "SKILL.md", "README.md", "LICENSE", "CHANGELOG.md", "agents/openai.yaml",
    "references/browser-guide.md", "references/recovery.md", "references/sources.md",
    "references/cc-switch.md", "scripts/cleanup.py", "scripts/browser_inventory.py",
)


def build(root):
    destination = root / "dist/claude-local-cleanup.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(FILES):
            source = root / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Missing or linked package source: " + name)
            info = zipfile.ZipInfo("claude-local-cleanup/" + name, (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_bytes(f"{digest}  {destination.name}\n".encode("ascii"))
    print(f"Built {destination.name} ({len(FILES)} files); SHA256 {digest}")
    return destination


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1])
