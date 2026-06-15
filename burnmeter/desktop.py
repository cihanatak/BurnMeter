"""Create a desktop "Burnmeter" shortcut so users launch the dashboard with a
double-click — no terminal needed after install.

Cross-platform and dependency-free:
- Windows: a real `.lnk` written via PowerShell's WScript.Shell COM (no pywin32).
- macOS:   an executable `Burnmeter.command`.
- Linux:   an XDG `Burnmeter.desktop` entry.

The shortcut runs `<this-python> -m burnmeter serve`, which auto-opens the browser.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _desktop_dir(override=None) -> Path:
    return Path(override) if override else (Path.home() / "Desktop")


def create_shortcut(port: int = 8765, desktop_dir=None) -> Path:
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


def ensure_shortcut(port: int = 8765, desktop_dir=None):
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


def _win(desktop: Path, py: str, port: int) -> Path:
    lnk = desktop / "Burnmeter.lnk"

    def q(s) -> str:               # single-quote-safe for a PowerShell literal
        return str(s).replace("'", "''")

    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
        f"$s.TargetPath='{q(py)}';"
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
