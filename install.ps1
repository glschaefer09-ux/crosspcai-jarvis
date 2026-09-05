<#
  JARVAS installer — Windows.

    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Startup
    powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall

  Installs per-user (no admin needed) and puts one JARVAS icon on the
  Desktop and in the Start Menu.
#>

[CmdletBinding()]
param(
    [switch]$Startup,     # launch JARVAS when you sign in
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Install = Join-Path $env:LOCALAPPDATA 'Programs\JARVAS'
$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\JARVAS.lnk'
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVAS.lnk'
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Say  ($m) { Write-Host "> $m" -ForegroundColor Cyan }
function Fail ($m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }

# -- uninstall ---------------------------------------------------------------
if ($Uninstall) {
    Say 'Removing JARVAS'
    Get-CimInstance Win32_Process -Filter "Name like '%python%' or Name like '%JARVAS%'" |
        Where-Object { $_.CommandLine -like '*jarvas*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item $Install -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $StartMenu, $DesktopLnk -Force -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $RunKey -Name 'JARVAS' -ErrorAction SilentlyContinue
    Say 'JARVAS removed. Your data in %USERPROFILE%\.crosspcai was left alone.'
    exit 0
}

# -- work out what we are installing -----------------------------------------
$Built = Join-Path $Root 'dist\JARVAS\JARVAS.exe'
if (Test-Path $Built) {
    $Kind = 'binary'
    $Source = Join-Path $Root 'dist\JARVAS'
} elseif (Test-Path (Join-Path $Root 'jarvas\__main__.py')) {
    $Kind = 'source'
    $Source = $Root
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $py) { Fail 'Python 3.10+ is required for a source install.' }
    $ver = & $py.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])'
    if ([version]$ver -lt [version]'3.10') { Fail "Python 3.10+ required (found $ver)" }
} else {
    Fail 'No build found. Run: python packaging\build.py'
}
Say "Installing from ${Kind}: $Source"

# -- copy ---------------------------------------------------------------------
if (Test-Path $Install) { Remove-Item $Install -Recurse -Force }
New-Item -ItemType Directory -Path $Install -Force | Out-Null

if ($Kind -eq 'binary') {
    Copy-Item "$Source\*" $Install -Recurse -Force
    $Target = Join-Path $Install 'JARVAS.exe'
    $Args = ''
} else {
    Copy-Item (Join-Path $Source 'jarvas') $Install -Recurse -Force
    # pythonw keeps the console window from flashing up behind the app.
    $pyw = $py.Source -replace 'python\.exe$', 'pythonw.exe'
    if (-not (Test-Path $pyw)) { $pyw = $py.Source }
    $Target = $pyw
    $Args = '-m jarvas'
    Say 'Installing desktop extras (native window and tray icon)'
    & $py.Source -m pip install --quiet --upgrade pywebview pystray pillow 2>$null
    if ($LASTEXITCODE -ne 0) {
        Say 'note: extras unavailable — JARVAS will open in your browser instead'
    }
}

# -- register with the desktop ------------------------------------------------
# The app owns this (jarvas/installer.py) so shortcut and autostart logic has a
# single implementation shared by the installer, the setup wizard and Settings.
$installArgs = @('--install')
if (-not $Startup) { $installArgs += '--no-autostart' }

if ($Kind -eq 'binary') {
    & $Target @installArgs | Write-Host
} else {
    & $py.Source -m jarvas @installArgs | Write-Host
}

if ($LASTEXITCODE -ne 0) {
    Say 'note: could not register the icon automatically - open JARVAS and use Settings > This machine'
}

Write-Host ''
Write-Host '  JARVAS is installed.' -ForegroundColor Green
Write-Host ''
Write-Host '    Open it        click the JARVAS icon on your Desktop'
Write-Host '    Server mode    JARVAS.exe --server'
Write-Host '    Health check   JARVAS.exe --status'
Write-Host ''
Write-Host '  First launch walks you through setup. Nothing leaves your machine'
Write-Host '  unless you switch reporting on and press Send.'
Write-Host ''
