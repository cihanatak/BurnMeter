# Build the standalone Burnmeter.exe (PoC, UNSIGNED).
#
# Produces dist\Burnmeter\Burnmeter.exe — a self-contained Windows app that needs
# NO Python/pip installed. Built --console (pywebview needs a console allocated or
# it renders off-screen) with the console hidden at runtime (see burnmeter_app.py).
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --quiet --upgrade pyinstaller pyinstaller-hooks-contrib

# Bundle the REPO code (not site-packages) so local changes are included.
$env:PYTHONPATH = $root
$sep = ";"   # add-data path separator on Windows

python -m PyInstaller --noconfirm --clean --name Burnmeter --console `
  --icon "burnmeter\assets\burnmeter.ico" `
  --paths $root `
  --collect-all webview `
  --collect-all clr_loader `
  --collect-all pythonnet `
  --collect-all cryptography `
  --collect-all pystray `
  --collect-all PIL `
  --collect-submodules burnmeter `
  --hidden-import proxy_tools `
  --hidden-import bottle `
  --hidden-import pystray._win32 `
  --add-data "burnmeter\static${sep}burnmeter\static" `
  --add-data "burnmeter\assets${sep}burnmeter\assets" `
  packaging\burnmeter_app.py

Write-Host "`nBuilt: dist\Burnmeter\Burnmeter.exe"
