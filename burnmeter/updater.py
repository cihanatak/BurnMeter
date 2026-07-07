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
    # VALIDATE before we touch the running app. A truncated / HTML-error / non-PE body
    # can complete a 200 without raising; killing the app for an installer that can't run
    # would leave the user with NO app. A real installer is multi-MB and starts with "MZ".
    try:
        if os.path.getsize(dst) < 1_000_000:
            return False
        with open(dst, "rb") as fh:
            if fh.read(2) != b"MZ":
                return False
    except Exception:
        return False
    try:
        # The Restart Manager can't close the pywebview app, so a plain silent install
        # over the running app aborts (Inno exit 5). Instead a DETACHED cmd: (1) closes
        # the app itself (taskkill all Burnmeter.exe — geometry & section are already
        # persisted), (2) removes a possibly-stale single-instance lock so the relaunch
        # claims cleanly, (3) waits ~3s for file handles to free, (4) runs the installer
        # with nothing holding the files; its [Run] relaunches ONE window at the saved
        # position + section. `|| start` is the safety net: if the install fails AFTER the
        # kill (AV/SmartScreen/locked files), the (still-old) app is reopened so an update
        # can NEVER leave the user with no app. cmd survives the kill (detached, not a
        # Burnmeter.exe).
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW
        exe = sys.executable
        # /S first: the NSIS (electron-builder) installer honors /S and ignores the Inno
        # flags; Inno Setup honors its own flags and ignores the unknown /S. One command
        # line stays FULLY SILENT across both installer generations (pywebview-era Inno
        # and the Electron-era NSIS served under the same stable asset name).
        #
        # Electron era: (1) the SIDECAR (burnmeter-server.exe) must die too, or NSIS can't
        # replace resources/server (locked files → broken update); (2) a silent NSIS
        # install SKIPS runAfterFinish, so we relaunch EXPLICITLY on success — from the
        # install dir both eras share (%LOCALAPPDATA%\Programs\Burnmeter), falling back to
        # the old exe path if that vanished.
        app_exe = r'%LOCALAPPDATA%\Programs\Burnmeter\Burnmeter.exe'
        cmdline = (
            'taskkill /F /IM Burnmeter.exe >nul 2>&1 & '
            'taskkill /F /IM burnmeter-server.exe >nul 2>&1 & '
            'del "%USERPROFILE%\\.burnmeter\\window.lock" >nul 2>&1 & '
            'ping -n 4 127.0.0.1 >nul & '
            f'("{dst}" /S /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NORESTARTAPPLICATIONS '
            f'&& start "" "{app_exe}") '
            f'|| start "" "{exe}"'
        )
        subprocess.Popen(["cmd", "/c", cmdline], creationflags=DETACHED, close_fds=True)
        return True
    except Exception:
        return False
