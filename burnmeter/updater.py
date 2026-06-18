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
    """Download the latest installer and launch a SILENT in-place upgrade.

    Returns True once the installer process is launched (it will close, upgrade,
    and relaunch the app via the Restart Manager); False on download/launch
    failure. Windows-only (the frozen target)."""
    if sys.platform != "win32":
        return False
    try:
        dst = os.path.join(tempfile.gettempdir(), "BurnmeterSetup-latest.exe")
        with urllib.request.urlopen(url, timeout=180) as r, open(dst, "wb") as f:
            f.write(r.read())
    except Exception:
        return False
    try:
        # /SILENT = small progress window; /RESTARTAPPLICATIONS re-launches the
        # app the installer closed (paired with CloseApplications in the .iss).
        subprocess.Popen(
            [dst, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTAPPLICATIONS"],
            close_fds=True)
        return True
    except Exception:
        return False
