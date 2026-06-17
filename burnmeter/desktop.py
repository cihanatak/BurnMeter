"""Create a desktop "Burnmeter" shortcut so users launch the dashboard with a
double-click — no terminal needed after install.

Cross-platform and dependency-free:
- Windows: a real `.lnk` written via PowerShell's WScript.Shell COM (no pywin32).
- macOS:   an executable `Burnmeter.command`.
- Linux:   an XDG `Burnmeter.desktop` entry.

The shortcut runs `<this-python> -m burnmeter app`, opening the dashboard in a
native desktop window (no console window). The window ensures a detached tray
server is running and attaches to it, so closing the window leaves the dashboard
running in the tray.
"""
from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path

# Bump whenever the shortcut's launch command/target changes so ensure_shortcut()
# UPGRADES an existing (older) shortcut instead of leaving it stale. v1 = `serve`
# (pre-stamp), v2 = `tray`, v3 = `app` (native window + background tray).
SHORTCUT_VERSION = 3
_STAMP = Path.home() / ".burnmeter" / "shortcut.json"


def _read_stamp() -> int:
    try:
        return int(json.loads(_STAMP.read_text(encoding="utf-8"))["version"])
    except Exception:
        return 0          # no stamp → an old (pre-stamp / v1 'serve') shortcut


def _write_stamp(v: int = SHORTCUT_VERSION) -> None:
    try:
        _STAMP.parent.mkdir(parents=True, exist_ok=True)
        _STAMP.write_text(json.dumps({"version": v}), encoding="utf-8")
    except Exception:
        pass


def _rm(p: Path) -> None:
    try:
        p.unlink()
    except Exception:
        pass


@functools.lru_cache(maxsize=1)
def _win_real_desktop():
    """Resolve the REAL Windows desktop via the Known Folder API. Honors OneDrive
    redirection AND localized folder names (Turkish 'Masaüstü', French 'Bureau', …),
    which `~/Desktop` misses — there the shortcut would land in an invisible legacy
    folder. Pure ctypes: Unicode result, no subprocess, no codepage/cp1254 issues.
    Returns a Path or None on any failure."""
    try:
        import ctypes
        from ctypes import wintypes, byref, c_wchar_p

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                    (wintypes.BYTE * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
        ptr = c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(byref(fid), 0, None, byref(ptr)) == 0:
            path = ptr.value
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            return Path(path) if path else None
    except Exception:
        pass
    return None


def _desktop_dir(override=None) -> Path:
    if override:
        return Path(override)
    if sys.platform == "win32":
        real = _win_real_desktop()
        if real and real.exists():
            return real
    return Path.home() / "Desktop"


def create_shortcut(port: int = 7654, desktop_dir=None) -> Path:
    """Create the shortcut for the current OS, stamp it, and return its path."""
    desktop = _desktop_dir(desktop_dir)
    desktop.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    if sys.platform == "win32":
        path = _win(desktop, py, port)
    elif sys.platform == "darwin":
        path = _mac(desktop, py, port)
    else:
        path = _linux(desktop, py, port)
    _write_stamp()        # so `burnmeter desktop` carries a stamp too (no re-write churn)
    return path


def _shortcut_target(desktop_dir=None) -> Path:
    d = _desktop_dir(desktop_dir)
    if sys.platform == "win32":
        return d / "Burnmeter.lnk"
    if sys.platform == "darwin":
        return d / "Burnmeter.command"
    return d / "Burnmeter.desktop"


def ensure_shortcut(port: int = 7654, desktop_dir=None):
    """Create the desktop shortcut if missing, or UPGRADE it when the on-disk
    stamp is older than SHORTCUT_VERSION (e.g. a v2 'tray' shortcut → v3 'app').
    Returns (path, created_or_upgraded: bool). Never raises — best-effort UX."""
    target = _shortcut_target(desktop_dir)
    alt = target.with_suffix(".cmd") if sys.platform == "win32" else None
    exists = target.exists() or bool(alt and alt.exists())
    if exists and _read_stamp() >= SHORTCUT_VERSION:
        return (target if target.exists() else alt), False     # up to date
    try:
        # Missing OR stale → (re)create. create_shortcut() overwrites in place,
        # stamps, and (on Windows) _win removes the sibling .lnk/.cmd so exactly
        # one shortcut artifact survives an upgrade.
        return create_shortcut(port=port, desktop_dir=desktop_dir), True
    except Exception:
        return (target if exists else None), False


def _pythonw(py: str) -> str:
    """Return the windowless interpreter (pythonw.exe) next to `py` if present,
    so a desktop double-click launches Burnmeter WITHOUT a lingering console
    window. Falls back to `py` (a visible console) when pythonw isn't found —
    a console is the safe default since it surfaces errors."""
    try:
        cand = Path(py).with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    return py


def _win(desktop: Path, py: str, port: int) -> Path:
    lnk = desktop / "Burnmeter.lnk"
    pyw = _pythonw(py)            # silent (no console) for the double-click path

    def q(s) -> str:               # single-quote-safe for a PowerShell literal
        return str(s).replace("'", "''")

    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
        f"$s.TargetPath='{q(pyw)}';"
        f"$s.Arguments='-m burnmeter app';"
        f"$s.WorkingDirectory='{q(Path.home())}';"
        f"$s.Description='Burnmeter - AI coding usage dashboard';"
        f"$s.Save()"
    )
    cmd = desktop / "Burnmeter.cmd"
    try:
        from ._proc import NO_WINDOW
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=True, capture_output=True, timeout=25,
            encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,       # no PowerShell window flash under the tray
        )
        _rm(cmd)                       # drop a stale .cmd sibling so only the .lnk remains
        return lnk
    except Exception:
        # Fallback: a .cmd batch. `start "" pythonw …` launches detached so the
        # cmd.exe console flashes and self-closes instead of lingering — the tray
        # icon is the UI, not the console.
        cmd.write_text(f'@echo off\r\nstart "" "{pyw}" -m burnmeter app\r\n',
                       encoding="utf-8")
        _rm(lnk)                       # drop a stale .lnk sibling so only the .cmd remains
        return cmd


def _mac(desktop: Path, py: str, port: int) -> Path:
    f = desktop / "Burnmeter.command"
    f.write_text(f'#!/bin/bash\nexec "{py}" -m burnmeter app\n',
                 encoding="utf-8")
    f.chmod(0o755)
    return f


def _linux(desktop: Path, py: str, port: int) -> Path:
    f = desktop / "Burnmeter.desktop"
    f.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Burnmeter\n"
        "Comment=AI coding usage dashboard\n"
        f"Exec={py} -m burnmeter app\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n",
        encoding="utf-8",
    )
    f.chmod(0o755)
    return f
