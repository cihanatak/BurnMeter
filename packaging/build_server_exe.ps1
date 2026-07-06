# Build the HEADLESS Burnmeter sidecar for the Electron shell (UNSIGNED).
#
# Produces dist\burnmeter-server\burnmeter-server.exe — the local web server ONLY
# (no pywebview / pystray / GUI deps → smaller + faster start than the full app exe).
# electron-builder bundles this directory as resources\server (electron/package.json).
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_server_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --quiet --upgrade pyinstaller pyinstaller-hooks-contrib

$env:PYTHONPATH = $root
$sep = ";"

python -m PyInstaller --noconfirm --clean --name burnmeter-server --windowed `
  --icon "burnmeter\assets\burnmeter.ico" `
  --paths $root `
  --collect-all cryptography `
  --collect-submodules burnmeter `
  --add-data "burnmeter\static${sep}burnmeter\static" `
  --add-data "burnmeter\assets${sep}burnmeter\assets" `
  packaging\burnmeter_server.py

Write-Host "`nBuilt: dist\burnmeter-server\burnmeter-server.exe"
