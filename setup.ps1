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

# The repository tree carries an inheritable Low mandatory label. Windows starts
# an image at the lower of the caller token and the executable file's own level,
# so a venv built or copied inside the repo runs Low and cannot write to normal
# folders. Keep the venv outside the repo and expose it inside as a junction so
# every existing script keeps using .venv.
$RuntimeRoot = Join-Path $env:PUBLIC "CompanionRuntime"
$VenvRoot = Join-Path $RuntimeRoot "venv"
$VenvLink = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

$LinkItem = Get-Item -LiteralPath $VenvLink -Force -ErrorAction SilentlyContinue
$LinkIsJunction = $LinkItem -and $LinkItem.LinkType -eq "Junction"
if ($LinkItem -and -not $LinkIsJunction -and (Test-Path $VenvRoot)) {
    throw "Both .venv (a real folder) and $VenvRoot exist. Remove .venv, then run setup.ps1 again."
}

if (-not (Test-Path $VenvPython)) {
    if ($LinkItem -and -not $LinkIsJunction -and -not (Test-Path $VenvRoot)) {
        New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
        Write-Host "Moving the virtual environment out of the repository..."
        Move-Item -LiteralPath $VenvLink -Destination $VenvRoot
    } elseif (-not (Test-Path $VenvRoot)) {
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

        New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
        Write-Host "Creating virtual environment with $BasePython"
        & $BasePython -m venv $VenvRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the virtual environment at $VenvRoot"
        }
    } else {
        throw "The virtual environment at $VenvRoot is incomplete. Delete it, then run setup.ps1 again."
    }
}

if (-not (Test-Path $VenvLink)) {
    New-Item -ItemType Junction -Path $VenvLink -Target $VenvRoot | Out-Null
}

# A venv migrated out of the repo keeps the Low labels it inherited there;
# reset the whole tree so launched interpreters run at Medium integrity.
& "$env:SystemRoot\System32\icacls.exe" $VenvRoot /setintegritylevel "(OI)(CI)M" /T /C /Q 2>$null | Out-Null

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
