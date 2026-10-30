"""OS primitives for private files and moves that never replace a destination."""
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def system_executable(name):
    """Resolve a closed list through Windows, never PATH, CWD or SystemRoot."""
    relative = {"tasklist": "tasklist.exe", "cmd": "cmd.exe",
                "powershell": "WindowsPowerShell/v1.0/powershell.exe"}[name]
    if os.name != "nt":
        raise OSError("Windows system commands require Windows")
    # LOAD_LIBRARY_SEARCH_SYSTEM32; kernel32 is also a Windows KnownDLL.
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    get_directory = kernel.GetSystemDirectoryW
    get_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("Cannot locate the Windows system directory")
    executable = Path(buffer.value) / relative
    if not executable.is_absolute() or not executable.is_file():
        raise OSError("Required Windows system command is unavailable")
    return str(executable)


def rename_noreplace(source, destination):
    """Atomic same-filesystem rename; an existing destination always wins."""
    source, destination = Path(source), Path(destination)
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("Absolute paths required")
    if os.name == "nt":
        # Windows os.rename raises FileExistsError instead of replacing dst.
        os.rename(source, destination)
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(os.fsencode(source), os.fsencode(destination), 0x4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        # Used by isolated Linux tests; cleanup still disallows Linux writes.
        libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(libc, "renameat2"):
            raise OSError(errno.ENOTSUP, "No exclusive rename primitive")
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                             ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOTSUP, "No exclusive rename primitive")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def private_fd(fd):
    """Restrict an already-open POSIX object before writing sensitive bytes."""
    info = os.fstat(fd)
    directory = stat.S_ISDIR(info.st_mode)
    if not (directory or stat.S_ISREG(info.st_mode)) or info.st_uid != os.geteuid():
        raise OSError("Private object must be a regular file/directory owned by this user")
    if not directory and info.st_nlink != 1:
        raise OSError("Refusing to change a hard-linked file")
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        for name, arguments, result in (
            ("acl_init", [ctypes.c_int], ctypes.c_void_p),
            ("acl_set_fd", [ctypes.c_int, ctypes.c_void_p], ctypes.c_int),
            ("acl_get_fd_np", [ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
            ("acl_get_entry", [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            ("acl_free", [ctypes.c_void_p], ctypes.c_int),
        ):
            function = getattr(libc, name)
            function.argtypes, function.restype = arguments, result
        empty = libc.acl_init(0)
        if not empty:
            raise OSError("Cannot allocate private ACL")
        try:
            if libc.acl_set_fd(fd, empty):
                raise OSError("Cannot clear inherited/explicit ACL")
        finally:
            libc.acl_free(empty)
        ctypes.set_errno(0)
        actual = libc.acl_get_fd_np(fd, 0x100)  # ACL_TYPE_EXTENDED
        # An open, valid fd with ENOENT means the object has no extended ACL.
        if not actual and ctypes.get_errno() != errno.ENOENT:
            raise OSError("Cannot verify private ACL")
        if actual:
            try:
                entry = ctypes.c_void_p()
                ctypes.set_errno(0)
                result = libc.acl_get_entry(actual, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
                if result != -1 or ctypes.get_errno() != errno.EINVAL:
                    raise OSError("Unexpected ACL entry")
            finally:
                libc.acl_free(actual)
    mode = 0o700 if directory else 0o600
    os.fchmod(fd, mode)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        raise OSError("Private permissions verification failed")


def private_paths(paths):
    """Caller validates paths; protect every object, including moved NTFS files."""
    if os.name != "nt":
        for path in paths:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                private_fd(fd)
            finally:
                os.close(fd)
        return
    records = [{"path": str(path), "directory": path.is_dir()} for path in paths]
    script = r"""$ErrorActionPreference='Stop';
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding;
$items=ConvertFrom-Json ([Console]::In.ReadToEnd());
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;
foreach($item in $items) {
if($item.directory) {
 $acl=New-Object System.Security.AccessControl.DirectorySecurity;
 $inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
} else {
 $acl=New-Object System.Security.AccessControl.FileSecurity;
 $inherit=[System.Security.AccessControl.InheritanceFlags]::None
};
$acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false);
foreach($id in @($sid,(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')))) {
 $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($id,'FullControl',$inherit,'None','Allow');
 $acl.AddAccessRule($rule)
};
Set-Acl -LiteralPath $item.path -AclObject $acl;
$check=Get-Acl -LiteralPath $item.path;
if(-not $check.AreAccessRulesProtected){throw 'ACL not protected'};
if($check.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'Unexpected owner'};
foreach($rule in $check.Access) {
 $s=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;
 if($s -ne $sid.Value -and $s -ne 'S-1-5-18'){throw 'Unexpected ACL'}
}}
"""
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    result = subprocess.run([system_executable("powershell"), "-NoProfile", "-NonInteractive", "-Command", script],
                            input=json.dumps(records, ensure_ascii=False).encode("utf-8"),
                            env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise OSError("Cannot set or verify private Windows ACL")
