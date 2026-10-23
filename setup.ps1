param(
    [string]$IndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$env:PYTHONUTF8 = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {}

$SharedTcl = "C:\Users\Public\CompanionRuntime\tcl"
if (Test-Path (Join-Path $SharedTcl "tcl8.6\init.tcl")) {
    $env:TCL_LIBRARY = Join-Path $SharedTcl "tcl8.6"
    $env:TK_LIBRARY = Join-Path $SharedTcl "tk8.6"
}

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $Candidates = @()

    $PythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $PythonRoot) {
        $Candidates += Get-ChildItem $PythonRoot -Directory -Filter "Python3*" |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "python.exe" }
    }

    $Candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )

    $BasePython = $null
    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        if (-not (Test-Path $Candidate)) {
            continue
        }
        & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            $BasePython = $Candidate
            break
        }
    }

    if (-not $BasePython) {
        throw "Python 3.11 or newer was not found. Install Python 3.12, then run setup.ps1 again."
    }

    Write-Host "Creating virtual environment with $BasePython"
    & $BasePython -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv"
    }
}

Write-Host "Updating packaging tools..."
& $VenvPython -m pip install --upgrade pip setuptools wheel `
    --index-url $IndexUrl --timeout 120 --retries 5
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update packaging tools"
}

Write-Host "Installing companion dependencies..."
& $VenvPython -m pip install -r requirements.txt `
    --index-url $IndexUrl --timeout 120 --retries 5
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install requirements"
}

Write-Host "Installing automation dependencies..."
& $VenvPython -m pip install -r requirements-optional.txt `
    --index-url $IndexUrl --timeout 120 --retries 5
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install optional automation requirements"
}

Write-Host "Installing Playwright Chromium..."
& $VenvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Playwright Chromium"
}

$ConfigFile = Join-Path $Root "config\keys.json"
$ConfigTemplate = Join-Path $Root "config\keys.template.json"
if (-not (Test-Path $ConfigFile) -and (Test-Path $ConfigTemplate)) {
    Copy-Item $ConfigTemplate $ConfigFile
    Write-Host "Created config\keys.json from the template. Fill in an API key for remote LLM access."
}

& $VenvPython doctor.py
if ($LASTEXITCODE -ne 0) {
    throw "Environment verification failed"
}

Write-Host ""
Write-Host "Environment is ready."
Write-Host "Start with: .\run.ps1"
Write-Host "Or double-click: start_companion.cmd"
