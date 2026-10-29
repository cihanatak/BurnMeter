"""The build worker exits when the server that spawned it dies.

A killed server used to leave its worker building for minutes (1.2-1.8 GB), and
the worker's config and report files stayed in the temp folder.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Stands in for the worker: the same watch, then a sleep where the build would run.
WORKER = (
    "import sys, threading, time; sys.path.insert(0, sys.argv[1]);"
    "from burnmeter._worker import _exit_with_parent;"
    "threading.Thread(target=_exit_with_parent, kwargs={'poll': 0.1}, daemon=True).start();"
    "time.sleep(60)"
)
# Stands in for the server: spawns the worker with the env server.py passes, then waits.
SERVER = (
    "import os, subprocess, sys, time;"
    "env = dict(os.environ, BURNMETER_WORKER_PARENT=str(os.getpid()), BURNMETER_WORKER_OUT=sys.argv[3]);"
    "p = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], env=env);"
    "print(p.pid, flush=True); time.sleep(60)"
)


def _alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(0x00100000, False, pid)      # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # An exited child its new parent has not reaped yet is a zombie, not alive.
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(st.stdout.strip()) and not st.stdout.strip().startswith("Z")


def test_the_worker_exits_and_cleans_up_when_its_server_dies(tmp_path):
    report = tmp_path / "bm-wk-out-test.json"
    report.write_text("{}", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-c", SERVER, WORKER, str(ROOT), str(report)],
                              stdout=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        worker_pid = int(server.stdout.readline())
        time.sleep(1.0)
        assert _alive(worker_pid), "the worker should run while its server does"
        server.kill()
        server.wait()
        deadline = time.time() + 10
        while _alive(worker_pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not _alive(worker_pid), "the worker outlived its server"
        assert not report.exists(), "the worker left its report file behind"
    finally:
        server.kill()
        if worker_pid and _alive(worker_pid):
            os.kill(worker_pid, signal.SIGTERM)
