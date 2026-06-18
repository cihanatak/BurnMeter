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

The build is `--console` (pywebview renders OFF-SCREEN under a console-less
process) and the console window is hidden at runtime — so no flash, no ghost.
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
    # Cache worker re-invocation: `Burnmeter.exe _worker` (reads stdin, prints
    # JSON, exits) — must NOT open a window.
    if len(sys.argv) >= 2 and sys.argv[1] == "_worker":
        from burnmeter._worker import main as worker_main
        return worker_main()

    if len(sys.argv) == 1:
        sys.argv.append("app")          # double-click → the window app

    if sys.argv[1] in ("app", "tray"):
        _hide_console()                 # GUI subcommands: hide the console window

    from burnmeter.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    # Guard against frozen multiprocessing re-spawning the whole app (fork bomb).
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
