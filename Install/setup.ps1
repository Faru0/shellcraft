# Offline setup + launcher for shellcraft (Windows PowerShell).
# Needs Python 3.11+ installed first (Install\windows\python-3.14.7-amd64.exe). No internet needed.
#
#   .\Install\setup.ps1                 set up (first run) and start the interactive shell
#   .\Install\setup.ps1 -c "a | b"      any arguments are passed through to main.py
#   $env:PYTHON="C:\Python312\python.exe"; .\Install\setup.ps1   use a specific interpreter
#
# If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\Install\setup.ps1
$ErrorActionPreference = "Stop"

$InstallDir = $PSScriptRoot
$RepoDir    = Split-Path -Parent $InstallDir
$Wheels     = Join-Path $InstallDir "wheels"
$Venv       = Join-Path $RepoDir ".venv"
$VenvPy     = Join-Path $Venv "Scripts\python.exe"

# Pick an interpreter: $env:PYTHON, else the py launcher (newest 3.x), else python on PATH.
function Find-Python {
    if ($env:PYTHON) { return ,@($env:PYTHON) }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.14", "3.13", "3.12", "3.11") {
            # try/catch: PowerShell 5.1 turns native stderr into a terminating error under "Stop".
            try { & py "-$v" -c "pass" 2>$null } catch { continue }
            if ($LASTEXITCODE -eq 0) { return ,@("py", "-$v") }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { return ,@("python") }
    return $null
}

if (-not (Test-Path $VenvPy)) {
    $Py = Find-Python
    if (-not $Py) {
        Write-Error "Python 3.11+ not found. Run Install\windows\python-3.14.7-amd64.exe first (tick 'Add python.exe to PATH')."
    }
    $PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
    & $PyExe @PyArgs -c "import sys; sys.exit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -ne 0) { Write-Error "$($Py -join ' ') is older than 3.11; shellcraft needs Python 3.11+." }

    Write-Host ">> Creating virtual environment in $Venv"
    & $PyExe @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed." }
}

Write-Host ">> Installing dependencies from $Wheels (offline)"
& $VenvPy -m pip install -q --disable-pip-version-check --no-index --find-links $Wheels `
    -r (Join-Path $RepoDir "requirements.txt") "censys-platform>=0.16" "pytest>=8"
if ($LASTEXITCODE -ne 0) { Write-Error "Dependency install failed." }

Write-Host ">> Starting shellcraft"
Push-Location $RepoDir
try {
    & $VenvPy .\main.py @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
