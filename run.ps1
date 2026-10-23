param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CompanionArgs
)

$env:PYTHONUTF8 = "1"
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
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found. Run .\setup.ps1 first."
    exit 1
}

Set-Location $Root
& $VenvPython "companion.py" @CompanionArgs
exit $LASTEXITCODE
