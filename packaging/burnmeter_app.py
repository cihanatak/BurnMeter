"""PyInstaller entry point for the standalone Burnmeter desktop app.

A frozen build has no separate Python interpreter, so the re-exec /
`python -m burnmeter` subprocess tricks used by the pip install don't apply here.
Instead this entry:

  1. Hides the console window (the build is `--console` so pywebview gets a real
     console — it renders OFF-SCREEN under a console-less process — but the user
     never sees it).
  2. Runs the dashboard window IN-PROCESS (server on a daemon thread + the
     native window), so the whole app is one self-contained .exe with no Python,
     no pip, no terminal.

Tray / background-persistence / auto-update for the frozen app are a later phase.
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
    # The codex cache "build worker" re-invokes this exe as `Burnmeter.exe
    # _worker` (frozen has no `python -m`). Route that to the worker instead of
    # opening a 2nd window. The worker reads config on stdin, prints JSON, exits.
    if len(sys.argv) >= 2 and sys.argv[1] == "_worker":
        from burnmeter._worker import main as worker_main
        return worker_main()

    _hide_console()
    from burnmeter.window import run_window
    # In-process server (ensure_background=False): one process owns both the
    # HTTP server and the window. Closing the window exits the app.
    return run_window(open_browser=False, ensure_background=False)


if __name__ == "__main__":
    # Guard against frozen multiprocessing re-spawning the whole app (fork bomb).
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
