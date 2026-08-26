$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildVenv = Join-Path $projectRoot ".venv-build"
$buildPython = Join-Path $buildVenv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $buildPython)) {
    py -3.12 -m venv $buildVenv
}

& $buildPython -m pip install --upgrade pip
& $buildPython -m pip install -r (Join-Path $projectRoot "requirements-build.txt")
& $buildPython -c "import tkinter; root=tkinter.Tcl(); print('Tcl/Tk OK:', root.eval('info patchlevel'))"

& $buildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "SIS-Cart-Scheduler" `
    --add-data "$(Join-Path $projectRoot 'config.example.yaml');." `
    --collect-all playwright `
    --collect-all tzdata `
    (Join-Path $projectRoot "gui.py")

$releaseDir = Join-Path $projectRoot "dist\SIS-Cart-Scheduler"
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $releaseDir -Force
Write-Host "Build complete: $releaseDir"
