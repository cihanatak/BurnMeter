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
    # Cache worker re-invocation: `Burnmeter.exe _worker` reads its config on
    # stdin and prints the report JSON to stdout. A --windowed (no-console) build
    # nulls std streams, so restore the REAL pipe fds the parent gave this child.
    if len(sys.argv) >= 2 and sys.argv[1] == "_worker":
        import io
        if sys.stdin is None:
            sys.stdin = io.TextIOWrapper(io.FileIO(0, "r"), encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = io.TextIOWrapper(io.FileIO(1, "w"), encoding="utf-8")
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
