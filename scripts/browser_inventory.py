#!/usr/bin/env python3
"""List current-user browser profiles without opening cookie or password stores."""
import configparser
import json
from pathlib import Path
import platform
import re
from cleanup import Refused, configure_output, exists, safe_path


def inventory(home, system):
    if system == "Darwin":
        roots = [("Chrome", "Library/Application Support/Google/Chrome"),
                 ("Edge", "Library/Application Support/Microsoft Edge"),
                 ("Brave", "Library/Application Support/BraveSoftware/Brave-Browser")]
        firefox = home / "Library/Application Support/Firefox"
    elif system == "Windows":
        roots = [("Chrome", "AppData/Local/Google/Chrome/User Data"),
                 ("Edge", "AppData/Local/Microsoft/Edge/User Data"),
                 ("Brave", "AppData/Local/BraveSoftware/Brave-Browser/User Data")]
        firefox = home / "AppData/Roaming/Mozilla/Firefox"
    else:
        roots = [("Chrome", ".config/google-chrome"), ("Chromium", ".config/chromium"),
                 ("Edge", ".config/microsoft-edge"), ("Brave", ".config/BraveSoftware/Brave-Browser")]
        firefox = home / ".mozilla/firefox"
    rows = []
    for browser, relative in roots:
        root = home / relative
        try:
            safe_path(root, home)
            if not root.is_dir():
                continue
            names = {p.name for p in root.iterdir() if p.name == "Default" or re.fullmatch(r"Profile \d+", p.name)}
            local_state = safe_path(root / "Local State", home)
            if local_state.is_file():
                state = json.loads(local_state.read_text(encoding="utf-8"))
                names.update(state.get("profile", {}).get("info_cache", {}).keys())
            for name in sorted(names):
                if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
                    rows.append({"browser": browser, "status": "blocked-profile-path"})
                    continue
                profile = safe_path(root / name, home)
                rows.append({"browser": browser, "profile": name, "path": str(profile),
                             "status": "present" if profile.is_dir() else "missing"})
        except (Refused, OSError, ValueError, AttributeError):
            rows.append({"browser": browser, "path": str(root), "status": "unreadable-or-unsupported"})
    try:
        ini = safe_path(firefox / "profiles.ini", home)
        if ini.is_file():
            config = configparser.ConfigParser(interpolation=None)
            config.read(ini, encoding="utf-8")
            for section in config.sections():
                if not section.startswith("Profile"):
                    continue
                value = config[section].get("Path", "")
                if not value:
                    continue
                profile = firefox / value if config[section].get("IsRelative", "1") == "1" else Path(value)
                try:
                    safe_path(profile, home)
                    rows.append({"browser": "Firefox", "profile": section, "path": str(profile),
                                 "status": "present" if profile.is_dir() else "missing"})
                except Refused:
                    rows.append({"browser": "Firefox", "profile": section, "status": "blocked-profile-path"})
    except (Refused, OSError, configparser.Error):
        rows.append({"browser": "Firefox", "status": "unreadable-or-unsupported"})
    if system == "Darwin":
        rows.append({"browser": "Safari", "status": "use-native-settings", "note": "在 Safari 设置中逐个核对个人资料与网站数据"})
    return {"system": system, "profiles": rows,
            "limits": "仅列已知默认位置；自定义目录、其他系统用户和未识别浏览器不在覆盖范围。未读取 Cookie、密码或账号邮箱；未执行清理。"}


if __name__ == "__main__":
    configure_output()
    print(json.dumps(inventory(Path.home().resolve(), platform.system()), ensure_ascii=False, indent=2))
