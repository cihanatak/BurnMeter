"""Process helper.

When Burnmeter runs windowless (system tray / pythonw — no console of its own),
any console child process it spawns (git, the build worker, taskkill, powershell)
would otherwise POP its own console window for the instant it runs. Across a
running session that shows up as cmd windows "flashing" every cache rebuild.
Passing CREATE_NO_WINDOW suppresses that window. On non-Windows it's 0 (no-op),
so call sites can pass `creationflags=NO_WINDOW` unconditionally.
"""
import sys

# CREATE_NO_WINDOW (0x08000000) on Windows; 0 elsewhere.
NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0
