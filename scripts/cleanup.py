#!/usr/bin/env python3
"""Local-only, reviewed-plan cleanup with verified recovery. Python 3.10+."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

VERSION = "1.0.0"
SCOPES = ("cache", "code-login", "desktop-login")
STATE = ".claude-local-cleanup"


class Refused(Exception):
    """An operation cannot be performed without changing the reviewed scope."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal(value):
    return hashlib.sha256(encode(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def exists(path):
    return os.path.lexists(path)


def safe_path(path, root):
    """Reject traversal, symlinks, junctions and other reparse points, including parents."""
    path, root = Path(path), Path(root)
    if not path.is_absolute() or not root.is_absolute() or ".." in path.parts:
        raise Refused("路径必须是没有 .. 的绝对路径")
    try:
        path.relative_to(root)
    except ValueError:
        raise Refused("路径不在当前用户目录内") from None
    for part in [path, *path.parents]:
        if exists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise Refused(f"拒绝链接或重解析点：{part}")
    return path


def snapshot(path, root):
    path = safe_path(path, root)
    if not exists(path):
        return None
    h, total, count = hashlib.sha256(), 0, 0

    def visit(item, name):
        nonlocal total, count
        safe_path(item, root)
        info = item.lstat()
        if stat.S_ISDIR(info.st_mode):
            h.update(encode([name, "directory"]))
            for child in sorted(item.iterdir(), key=lambda p: p.name):
                visit(child, name + "/" + child.name)
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink > 1:
                raise Refused(f"拒绝多重硬链接文件：{item}")
            digest = hashlib.sha256()
            with item.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            after = item.lstat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise Refused(f"检查期间文件变化：{item}")
            total += info.st_size
            count += 1
            h.update(encode([name, "file", info.st_size, digest.hexdigest()]))
        else:
            raise Refused(f"拒绝特殊文件：{item}")

    visit(path, ".")
    return {"sha256": h.hexdigest(), "bytes": total, "files": count}


def json_object(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise Refused("配置文件超过 16 MiB，需人工检查")

    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise Refused("JSON 有重复键，停止修改")
            out[k] = v
        return out

    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise Refused("配置不是有效的 UTF-8 JSON") from None
    if not isinstance(result, dict):
        raise Refused("配置顶层不是 JSON 对象")
    return result


def targets(home, system):
    """A closed allowlist: no fuzzy path matching and no arbitrary plan targets."""
    if system not in ("Darwin", "Windows"):
        raise Refused("客户端写入只支持 macOS 与原生 Windows；Linux/WSL 可使用浏览器盘点")
    out = []

    def add(relative, scope, impact, keys=()):
        out.append({"relative": relative, "scope": scope, "impact": impact,
                    "operation": "json" if keys else "move", "keys": list(keys)})

    add(".claude/cache", "cache", "Claude Code 可再生缓存")
    add(".claude.json", "code-login", "仅移除 oauthAccount；项目与其他配置键保留", ("oauthAccount",))
    add(".claude/.credentials.json", "code-login", "仅移除 claudeAiOauth；其他认证字段保留", ("claudeAiOauth",))
    desktop = "Library/Application Support/Claude" if system == "Darwin" else "AppData/Roaming/Claude"
    for name in ("Cache", "Code Cache", "GPUCache", "DawnCache"):
        add(f"{desktop}/{name}", "cache", "Desktop 可再生缓存")
    if system == "Darwin":
        add("Library/Caches/com.anthropic.claudefordesktop", "cache", "Desktop 系统缓存")
    # Cookies and their WAL/SHM files are separate planned items, moved only with app stopped.
    for name in ("Cookies", "Cookies-wal", "Cookies-shm", "Network/Cookies", "Network/Cookies-wal", "Network/Cookies-shm",
                 "Local Storage", "Session Storage", "IndexedDB", "Service Worker", "WebStorage"):
        add(f"{desktop}/{name}", "desktop-login", "Desktop 登录/离线网站存储；可能需重新登录，离线页面数据将移入恢复目录")
    return out


def environment_check(home, system):
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        raise Refused("检测到 CLAUDE_CONFIG_DIR；自定义配置目录需要另行审阅")
    if system == "Windows":
        for key, relative in (("APPDATA", "AppData/Roaming"), ("LOCALAPPDATA", "AppData/Local")):
            actual = os.environ.get(key)
            if actual and os.path.normcase(os.path.abspath(actual)) != os.path.normcase(str(home / relative)):
                raise Refused(f"{key} 已重定向；本版本不修改重定向目录")
    if system == "Linux":
        raise Refused("不支持 Linux/WSL 客户端写入")


def make_plan(home, system, scopes, created=None):
    scopes = sorted(set(scopes))
    if not scopes or any(s not in SCOPES for s in scopes):
        raise Refused("请选择有效清理范围")
    entries = []
    for spec in targets(home, system):
        if spec["scope"] not in scopes:
            continue
        path = home / spec["relative"]
        before = snapshot(path, home)
        if before is None:
            continue
        if spec["operation"] == "json":
            data = json_object(path)
            if not any(key in data for key in spec["keys"]):
                continue
        entries.append({**spec, "before": before})
    payload = {"version": VERSION, "home": str(home), "system": system,
               "scopes": scopes, "created": created or now(), "entries": entries}
    return {**payload, "plan_id": seal(payload)}


def validate_plan(plan, home, system, fresh=True):
    if not isinstance(plan, dict):
        raise Refused("计划格式不正确")
    payload = {k: v for k, v in plan.items() if k != "plan_id"}
    if plan.get("plan_id") != seal(payload):
        raise Refused("计划内容已变更，请重新生成")
    if (plan.get("home"), plan.get("system"), plan.get("version")) != (str(home), system, VERSION):
        raise Refused("计划不属于当前用户、系统或脚本版本")
    known = {s["relative"]: s for s in targets(home, system)}
    seen = set()
    for entry in plan.get("entries", []):
        rel = entry.get("relative")
        spec = known.get(rel)
        if not spec or rel in seen or any(entry.get(k) != v for k, v in spec.items()):
            raise Refused("计划包含未允许或重复的操作")
        if entry["scope"] not in plan["scopes"]:
            raise Refused("计划范围不一致")
        seen.add(rel)
        safe_path(home / rel, home)
    if fresh and make_plan(home, system, plan["scopes"], plan["created"]) != plan:
        raise Refused("文件已变化或目标增减；请重新生成并确认计划")


def running_clients(system):
    """Inspect names only, never print command lines or environment variables."""
    if system == "Windows":
        result = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise Refused("无法读取进程列表")
        names = [row[0] for row in csv.reader(io.StringIO(result.stdout)) if row]
    else:
        result = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise Refused("无法读取进程列表")
        names = result.stdout.splitlines()
    found = set()
    for name in names:
        base = name.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in ("claude", "claude.exe") or base.startswith("claude helper") or "/Claude.app/" in name:
            found.add("Claude")
        # Legacy npm installs run as node; refuse conservatively without reading arguments.
        if base in ("node", "node.exe"):
            found.add("Node.js（可能运行 Claude Code；请关闭后重试）")
    return sorted(found)


def require_stopped(system):
    found = running_clients(system)
    if found:
        raise Refused("相关进程仍在运行：" + "、".join(found))


def restrict_directory(path):
    if os.name != "nt":
        path.chmod(0o700)
        return
    # Set an explicit DACL, rather than retaining inherited grants or trusting chmod.
    script = r"""$ErrorActionPreference='Stop'; $p=$env:CLEANUP_PRIVATE_DIR;
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;
$acl=New-Object System.Security.AccessControl.DirectorySecurity;
$acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false);
foreach($id in @($sid,(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')))) {
$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); $acl.AddAccessRule($rule) };
Set-Acl -LiteralPath $p -AclObject $acl;
$check=Get-Acl -LiteralPath $p; if(-not $check.AreAccessRulesProtected){throw 'ACL not protected'};
foreach($rule in $check.Access){$s=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;
if($s -ne $sid.Value -and $s -ne 'S-1-5-18'){throw 'Unexpected ACL'}}"""
    env = dict(os.environ)
    env["CLEANUP_PRIVATE_DIR"] = str(path)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, timeout=30)
    if result.returncode:
        raise Refused("无法设置或验证恢复目录 ACL；未开始清理")


def private_dir(path, home):
    safe_path(path, home)
    if not path.exists():
        if path.parent != home:
            private_dir(path.parent, home)
        path.mkdir(mode=0o700)
    if not path.is_dir():
        raise Refused("恢复位置不是目录")
    restrict_directory(path)


def atomic_write(path, data, home, overwrite=True):
    safe_path(path, home)
    if not overwrite and exists(path):
        raise Refused(f"文件已存在：{path}")
    fd, temporary = tempfile.mkstemp(prefix=".cleanup-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path, home)
        if not overwrite and exists(path):
            raise Refused("输出文件已存在")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def save_json(path, value, home, overwrite=True):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n", home, overwrite)


@contextlib.contextmanager
def operation_lock(home):
    root = home / STATE
    private_dir(root, home)
    lock = root / "operation.lock"
    safe_path(lock, home)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Refused("另一操作或中断后的锁仍存在；核实进程停止后人工移除此锁") from None
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def batch_path(home, plan):
    return home / STATE / "recovery" / plan["plan_id"]


def show(plan):
    print(f"计划：{plan['plan_id']}\n系统：{plan['system']}\n范围：{', '.join(plan['scopes'])}")
    for entry in plan["entries"]:
        print(f"  {Path(plan['home']) / entry['relative']}\n    {entry['impact']}；{entry['before']['bytes']} bytes")
    print(f"恢复目录：{batch_path(Path(plan['home']), plan)}")
    print("保留项目、会话、MCP、skills、plugins、密码、其他网站数据。macOS 钥匙串需单独处理。")


def confirm(phrase):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Refused("写入操作需要交互终端，不能使用管道代答")
    print("确认已正常退出 Claude 与相关 Node.js 进程，且已阅读上方清单。")
    if input(f"请输入 {phrase}：").strip() != phrase:
        raise Refused("未确认，未执行操作")


def apply_plan(plan, home, system):
    validate_plan(plan, home, system)
    if not plan["entries"]:
        raise Refused("计划为空，没有需要处理的数据")
    require_stopped(system)
    show(plan)
    confirm("APPLY " + plan["plan_id"][:12])
    environment_check(home, system)
    require_stopped(system)
    validate_plan(plan, home, system)
    with operation_lock(home):
        batch = batch_path(home, plan)
        safe_path(batch, home)
        if exists(batch):
            raise Refused("这个计划已有恢复批次，请先检查 receipt，不能重复执行")
        private_dir(batch, home)
        receipt = {"tool": "claude-local-cleanup", "plan": plan, "status": "preparing", "actions": [], "created": now()}
        receipt_path = batch / "receipt.json"
        save_json(receipt_path, receipt, home)
        try:
            # Complete and verify every JSON backup before any source modification.
            for i, entry in enumerate(plan["entries"]):
                source = home / entry["relative"]
                backup = batch / f"{i:03d}.original"
                if source.stat().st_dev != batch.stat().st_dev:
                    raise Refused("目标与恢复目录不在同一文件系统，停止移动")
                action = {"entry": entry, "backup": backup.name, "status": "pending"}
                receipt["actions"].append(action)
                if entry["operation"] == "json":
                    atomic_write(backup, source.read_bytes(), home, overwrite=False)
                    if snapshot(backup, home) != entry["before"]:
                        raise Refused("备份校验失败")
            save_json(receipt_path, receipt, home)
            validate_plan(plan, home, system)
            for action in receipt["actions"]:
                entry = action["entry"]
                source, backup = home / entry["relative"], batch / action["backup"]
                if snapshot(source, home) != entry["before"]:
                    raise Refused("执行前源文件发生变化")
                action["status"] = "started"
                save_json(receipt_path, receipt, home)
                if entry["operation"] == "move":
                    if exists(backup):
                        raise Refused("恢复位置已占用")
                    if os.name == "nt":
                        # NTFS rename retains the old DACL; copy so every new object
                        # inherits the restricted recovery directory's permissions.
                        if source.is_dir():
                            shutil.copytree(source, backup, symlinks=True, copy_function=shutil.copyfile)
                        else:
                            shutil.copyfile(source, backup, follow_symlinks=False)
                        if snapshot(backup, home) != entry["before"] or snapshot(source, home) != entry["before"]:
                            raise Refused("Windows 恢复副本或源文件校验失败")
                        if source.is_dir():
                            shutil.rmtree(source)
                        else:
                            source.unlink()
                    else:
                        source.rename(backup)
                    if snapshot(backup, home) != entry["before"]:
                        raise Refused("移动后校验失败；保留恢复资料")
                    action["after"] = None
                else:
                    data = json_object(source)
                    for key in entry["keys"]:
                        data.pop(key, None)
                    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
                    # Record expected post-state before the write, so interrupted writes are recoverable.
                    staged = batch / (action["backup"] + ".edited")
                    atomic_write(staged, encoded, home, overwrite=False)
                    action["after"] = snapshot(staged, home)
                    save_json(receipt_path, receipt, home)
                    atomic_write(source, encoded, home)
                    if snapshot(source, home) != action["after"]:
                        raise Refused("配置写入后校验失败")
                action["status"] = "done"
                save_json(receipt_path, receipt, home)
            receipt["status"] = "complete"
        except (Exception, KeyboardInterrupt) as exc:
            receipt["status"] = "partial"
            receipt["error"] = type(exc).__name__
            save_json(receipt_path, receipt, home)
            raise Refused(f"操作中止；已处理项和恢复位置见 {receipt_path}") from None
        save_json(receipt_path, receipt, home)
        print(f"已完成 {len(receipt['actions'])} 项。恢复记录：{receipt_path}")
        return receipt_path


def load_receipt(path, home, system):
    safe_path(path, home)
    receipt = json_object(path)
    if receipt.get("tool") != "claude-local-cleanup":
        raise Refused("不是本工具的恢复记录")
    plan = receipt["plan"]
    validate_plan(plan, home, system, fresh=False)
    if path != batch_path(home, plan) / "receipt.json":
        raise Refused("恢复记录不在对应的本机批次中")
    entries = plan["entries"]
    actions = receipt.get("actions", [])
    if len(actions) > len(entries):
        raise Refused("恢复动作数量不正确")
    for i, action in enumerate(actions):
        if action.get("entry") != entries[i] or action.get("backup") != f"{i:03d}.original":
            raise Refused("恢复映射不匹配")
    return receipt


def recovery_check(receipt, batch, home):
    states = []
    for action in receipt["actions"]:
        entry = action["entry"]
        original = snapshot(home / entry["relative"], home)
        backup = snapshot(batch / action["backup"], home)
        if original == entry["before"]:
            state = "already-original"
        elif backup != entry["before"]:
            raise Refused("恢复副本缺失或已修改；停止恢复")
        elif entry["operation"] == "move" and original is None:
            state = "restore"
        elif entry["operation"] == "json" and original == action.get("after") and original is not None:
            state = "restore"
        else:
            raise Refused(f"原位置已有新数据，拒绝覆盖：{home / entry['relative']}")
        states.append(state)
    return states


def restore(path, home, system):
    receipt = load_receipt(path, home, system)
    require_stopped(system)
    recovery_check(receipt, path.parent, home)
    show(receipt["plan"])
    confirm("RESTORE " + receipt["plan"]["plan_id"][:12])
    environment_check(home, system)
    require_stopped(system)
    with operation_lock(home):
        receipt = load_receipt(path, home, system)
        states = recovery_check(receipt, path.parent, home)
        for action, state in zip(receipt["actions"], states):
            if state == "already-original":
                continue
            entry = action["entry"]
            source, backup = home / entry["relative"], path.parent / action["backup"]
            safe_path(source.parent, home)
            if not source.parent.is_dir():
                raise Refused("原父目录不再存在，需人工恢复")
            # Recheck immediately before each write; never overwrite new user work.
            recovery_check({"actions": [action]}, path.parent, home)
            if entry["operation"] == "move":
                backup.rename(source)
            else:
                atomic_write(source, backup.read_bytes(), home)
            if snapshot(source, home) != entry["before"]:
                raise Refused("恢复后校验失败")
            action["status"] = "restored"
            save_json(path, receipt, home)
        receipt["status"] = "restored"
        save_json(path, receipt, home)
    print(f"恢复完成：{path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude 本地清理助手：audit → plan → apply → restore")
    parser.add_argument("--version", action="version", version=VERSION)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("audit", help="只读检查已知目标，不显示配置内容")
    p = subs.add_parser("plan", help="生成清单；默认只选缓存")
    p.add_argument("--scope", action="append", choices=SCOPES)
    p.add_argument("--out", type=Path, required=True)
    for command in ("apply", "restore", "verify"):
        p = subs.add_parser(command)
        p.add_argument("path", type=Path, help="apply 使用计划；restore/verify 使用 receipt.json")
    args = parser.parse_args(argv)
    home, system = Path.home().resolve(), platform.system()
    try:
        if args.command == "audit":
            report = {"version": VERSION, "system": system, "targets": [],
                      "notes": ["未读取或删除浏览器 Cookie；使用 browser_inventory.py 盘点配置。",
                                "钥匙串、Windows 凭据管理器、MSIX/未知布局、自定义目录需人工核验。",
                                "未扫描配置内部备份、历史版本、快照或第三方认证配置。"]}
            for spec in targets(home, system):
                path = home / spec["relative"]
                row = {**spec, "path": str(path)}
                try:
                    row["snapshot"] = snapshot(path, home)
                    row["status"] = "present" if row["snapshot"] else "absent"
                except (Refused, OSError):
                    row["status"] = "blocked"
                report["targets"].append(row)
            try:
                environment_check(home, system)
                report["running"] = running_clients(system)
            except (Refused, OSError, subprocess.SubprocessError) as exc:
                report["environment"] = str(exc) if isinstance(exc, Refused) else type(exc).__name__
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "plan":
            environment_check(home, system)
            plan = make_plan(home, system, args.scope or ["cache"])
            destination = args.out.expanduser().absolute()
            safe_path(destination, home)
            if not destination.parent.is_dir():
                raise Refused("计划输出父目录必须已存在")
            # Never allow plan output to overwrite or be placed in cleanup targets.
            for spec in targets(home, system):
                target = home / spec["relative"]
                if destination == target or target in destination.parents:
                    raise Refused("计划不能保存到清理目标中")
            save_json(destination, plan, home, overwrite=False)
            show(plan)
            print(f"计划已保存：{destination}；尚未清理")
        elif args.command == "apply":
            environment_check(home, system)
            source = safe_path(args.path.expanduser().absolute(), home)
            apply_plan(json_object(source), home, system)
        else:
            source = args.path.expanduser().absolute()
            if args.command == "restore":
                environment_check(home, system)
                restore(source, home, system)
            else:
                receipt = load_receipt(source, home, system)
                states = recovery_check(receipt, source.parent, home)
                print(json.dumps({"status": receipt["status"], "recovery_states": states,
                                  "meaning": "restore 表示已清理且可恢复；already-original 表示原数据在原处"}, ensure_ascii=False, indent=2))
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Never render JSON fragments or subprocess output that might contain credentials.
        print("停止：" + (str(exc) if isinstance(exc, Refused) else type(exc).__name__), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("已取消；若开始过写入，请检查本次 receipt.json。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
