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

$LowSid = "S-1-16-4096"
$Whoami = & "$env:SystemRoot\System32\whoami.exe" /groups 2>$null
if ($Whoami -match $LowSid) {
    Write-Error "This terminal runs at low integrity ($LowSid); writes to normal folders would fail. Start with start_companion.cmd from Explorer or Win+R instead."
    exit 1
}

$Label = & "$env:SystemRoot\System32\icacls.exe" $VenvPython 2>$null
if ($Label -match "Low Mandatory Level") {
    Write-Error "The virtual environment carries a Low integrity label; writes to normal folders would fail. Run .\setup.ps1 to repair it."
    exit 1
}

Set-Location $Root
& $VenvPython "companion.py" @CompanionArgs
exit $LASTEXITCODE
