@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "TCL_LIBRARY=C:\Users\Public\CompanionRuntime\tcl\tcl8.6"
set "TK_LIBRARY=C:\Users\Public\CompanionRuntime\tcl\tk8.6"

rem Low-integrity (sandboxed) parents hand this script a token that Windows
rem forbids from writing to normal folders, so every file write would fail.
rem Keep this file pure ASCII: cp65001 batch parsing misreads UTF-8 lines.
"%SystemRoot%\System32\whoami.exe" /groups | findstr /C:"S-1-16-4096" >nul
if not errorlevel 1 (
    echo Launch refused: this terminal is a low-integrity sandbox context.
    echo An instance started here cannot write to normal folders; all file writes would fail.
    echo Instead: double-click start_companion.cmd in File Explorer,
    echo or press Win+R and run its full path. Do not run it from IDE or sandbox terminals.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found.
    echo Run setup.ps1 from PowerShell first.
    pause
    exit /b 1
)

rem Windows starts an image at the lower of the caller token's integrity level
rem and the executable file's own mandatory label. This repository tree carries
rem an inheritable Low label, so a venv built or copied inside it runs Low and
rem every write to a normal folder fails with "Permission denied".
"%SystemRoot%\System32\icacls.exe" ".venv\Scripts\pythonw.exe" 2>nul | findstr /C:"Low Mandatory Level" >nul
if not errorlevel 1 (
    echo Launch refused: .venv\Scripts\pythonw.exe carries a Low integrity label.
    echo Such an instance cannot write to normal folders.
    echo Repair: run setup.ps1 once. It moves the venv to
    echo   C:\Users\Public\CompanionRuntime\venv
    echo relabels it, and keeps .venv as a junction to that folder.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "companion.py" %*
exit /b 0
