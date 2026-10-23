@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "TCL_LIBRARY=C:\Users\Public\CompanionRuntime\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\Public\CompanionRuntime\tcl\tk8.6"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found.
    echo Run setup.ps1 from PowerShell first.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "companion.py" %*
exit /b 0
