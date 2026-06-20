# Build the Windows installer (BurnmeterSetup-<ver>.exe) with Inno Setup.
# Requires Inno Setup 6 (ISCC.exe). Install it once with:
#   winget install --id JRSoftware.InnoSetup -e
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Version from the package (single source of truth).
$ver = (Select-String -Path "burnmeter\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Version: $ver"

# Ensure the frozen app is built.
if (-not (Test-Path "dist\Burnmeter\Burnmeter.exe")) {
  Write-Host "dist\Burnmeter missing - building the exe first..."
  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
}

# Locate ISCC (winget installs per-user under LOCALAPPDATA; the `innosetup` npm
# package also bundles a working ISCC, e.g. inside Antigravity's node_modules).
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Antigravity\resources\app\node_modules\innosetup\bin\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  # last resort: any ISCC.exe under common npm innosetup installs
  $iscc = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs","$env:APPDATA\npm" -Recurse -Filter ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $iscc) {
  $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
  throw "Inno Setup (ISCC.exe) not found. Install it: winget install --id JRSoftware.InnoSetup -e"
}

& $iscc "/DMyAppVersion=$ver" "packaging\installer.iss"
Write-Host "`nInstaller: dist\installer\BurnmeterSetup-$ver.exe"
