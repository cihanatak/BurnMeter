"""PyInstaller entry point for the HEADLESS Burnmeter sidecar (Electron shell).

The Electron app owns the window + tray; this binary is ONLY the local web server
(parser + analytics + HTTP). No pywebview, no pystray — slimmer and simpler than the
full app exe. Argv routes to the normal CLI so `burnmeter-server serve --port …` works,
and the exe re-invokes ITSELF for the cache worker (`burnmeter-server _worker`).

Build: packaging/build_server_exe.ps1  →  dist/burnmeter-server/burnmeter-server.exe
Bundled by electron-builder as resources/server/ (see electron/package.json).
"""
import sys


def main() -> int:
    # Cache worker re-invocation — same file-based IO contract as burnmeter_app.py
    # (a --windowed/no-console process has no usable std streams).
    if len(sys.argv) >= 2 and sys.argv[1] == "_worker":
        import os as _os
        if sys.stdout is None:
            sys.stdout = open(_os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(_os.devnull, "w", encoding="utf-8")
        from burnmeter._worker import main as worker_main
        return worker_main()

    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if len(sys.argv) == 1:
        # Bare launch → serve on the standard port without opening a browser.
        sys.argv += ["serve", "--no-browser", "--no-shortcut"]

    from burnmeter.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # frozen multiprocessing guard (fork-bomb)
    sys.exit(main())
