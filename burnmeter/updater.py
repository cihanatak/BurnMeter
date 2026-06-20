"""Frozen-app self-update: download the latest installer and run it.

The pip install updates via `pip install git+…`; a frozen .exe can't (no pip, no
Python), so this downloads BurnmeterSetup.exe from the GitHub "latest" release and
runs it silently. installer.iss sets CloseApplications/RestartApplications so the
silent upgrade closes the running app, replaces it in place, and relaunches it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.request

INSTALLER_URL = (
    "https://github.com/cihanatak/BurnMeter/releases/latest/download/BurnmeterSetup.exe"
)


def run_installer_update(url: str = INSTALLER_URL) -> bool:
    """Download the latest installer and run a clean in-place upgrade: close the app,
    replace it, relaunch ONE window (which restores the last position + section).

    Returns True once the updater is launched; False on download/launch failure.
    Windows-only (the frozen target)."""
    if sys.platform != "win32":
        return False
    try:
        dst = os.path.join(tempfile.gettempdir(), "BurnmeterSetup-latest.exe")
        with urllib.request.urlopen(url, timeout=180) as r, open(dst, "wb") as f:
            f.write(r.read())
    except Exception:
        return False
    try:
        # The Restart Manager can't close the pywebview app, so a plain silent install
        # over the running app aborts (Inno exit 5) — the update silently fails. Instead
        # a DETACHED cmd CLOSES the app itself (taskkill all Burnmeter.exe — geometry &
        # section are already persisted, so nothing is lost), waits for the file handles
        # to free, then runs the installer with nothing holding the files. The installer's
        # [Run] entry relaunches EXACTLY ONE window, which reopens at the saved position +
        # section. cmd survives the kill (detached, and it isn't a Burnmeter.exe).
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW
        cmdline = (
            'taskkill /F /IM Burnmeter.exe >nul 2>&1 & '
            'ping -n 2 127.0.0.1 >nul & '
            f'"{dst}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NORESTARTAPPLICATIONS'
        )
        subprocess.Popen(["cmd", "/c", cmdline], creationflags=DETACHED, close_fds=True)
        return True
    except Exception:
        return False
