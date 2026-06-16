"""Create a desktop "Burnmeter" shortcut so users launch the dashboard with a
double-click — no terminal needed after install.

Cross-platform and dependency-free:
- Windows: a real `.lnk` written via PowerShell's WScript.Shell COM (no pywin32).
- macOS:   an executable `Burnmeter.command`.
- Linux:   an XDG `Burnmeter.desktop` entry.

The shortcut runs `<this-python> -m burnmeter serve`, which auto-opens the browser.
"""
from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path


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
    """Create the shortcut for the current OS and return its path."""
    desktop = _desktop_dir(desktop_dir)
    desktop.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    if sys.platform == "win32":
        return _win(desktop, py, port)
    if sys.platform == "darwin":
        return _mac(desktop, py, port)
    return _linux(desktop, py, port)


def _shortcut_target(desktop_dir=None) -> Path:
    d = _desktop_dir(desktop_dir)
    if sys.platform == "win32":
        return d / "Burnmeter.lnk"
    if sys.platform == "darwin":
        return d / "Burnmeter.command"
    return d / "Burnmeter.desktop"


def ensure_shortcut(port: int = 7654, desktop_dir=None):
    """Create the desktop shortcut only if it isn't already there.
    Returns (path, created: bool). Never raises — best-effort UX."""
    target = _shortcut_target(desktop_dir)
    alt = target.with_suffix(".cmd") if sys.platform == "win32" else None
    if target.exists():
        return target, False
    if alt and alt.exists():
        return alt, False
    try:
        return create_shortcut(port=port, desktop_dir=desktop_dir), True
    except Exception:
        return None, False


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
        f"$s.Arguments='-m burnmeter serve';"
        f"$s.WorkingDirectory='{q(Path.home())}';"
        f"$s.Description='Burnmeter - AI coding usage dashboard';"
        f"$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=True, capture_output=True, timeout=25,
            encoding="utf-8", errors="replace",
        )
        return lnk
    except Exception:
        # Fallback: a .cmd batch (always works; shows a console window).
        cmd = desktop / "Burnmeter.cmd"
        cmd.write_text(f'@echo off\r\n"{py}" -m burnmeter serve\r\n',
                       encoding="utf-8")
        return cmd


def _mac(desktop: Path, py: str, port: int) -> Path:
    f = desktop / "Burnmeter.command"
    f.write_text(f'#!/bin/bash\nexec "{py}" -m burnmeter serve\n',
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
        f"Exec={py} -m burnmeter serve\n"
        "Terminal=true\n"
        "Categories=Development;Utility;\n",
        encoding="utf-8",
    )
    f.chmod(0o755)
    return f
