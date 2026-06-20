"""PyInstaller entry point for the standalone Burnmeter desktop app.

A frozen build has no separate Python interpreter, so `python -m burnmeter`
doesn't exist — the exe IS the launcher. This entry routes argv to the normal
CLI so the FULL app works (background tray server + native window + the cache
worker), with the exe re-invoking ITSELF for subprocesses
(`Burnmeter.exe tray` / `Burnmeter.exe app` / `Burnmeter.exe _worker`).

  * no args (double-click) → `app` → background tray server + native window;
    closing the window leaves the tray running (quit from the tray).
  * `tray`  → the background server + tray icon.
  * `_worker` → the short-lived codex cache build worker (stdin→stdout).

The build is `--windowed` (no console window ever — the standard pywebview
packaging). std streams are nulled by --windowed, so we restore the worker's real
pipe fds and route GUI prints to devnull (below).
"""
import sys


def _hide_console():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


def main() -> int:
    # Cache worker re-invocation: `Burnmeter.exe _worker`. Config in / report out
    # go through temp files (BURNMETER_WORKER_IN/OUT) — NOT stdin/stdout — because
    # a --windowed (no-console) process has no usable std streams (writing stdout
    # raised OSError [Errno 22]). Route any stray std writes to devnull so nothing
    # can crash the worker.
    if len(sys.argv) >= 2 and sys.argv[1] == "_worker":
        import os as _os
        if sys.stdout is None:
            sys.stdout = open(_os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(_os.devnull, "w", encoding="utf-8")
        from burnmeter._worker import main as worker_main
        return worker_main()

    # Non-worker (GUI) paths: --windowed nulls stdout/stderr → guard so the CLI's
    # print()/stderr.write() calls never crash.
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if len(sys.argv) == 1:
        sys.argv.append("app")          # double-click → the window app

    _hide_console()                     # no-op under --windowed; safe either way

    from burnmeter.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    # Guard against frozen multiprocessing re-spawning the whole app (fork bomb).
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
