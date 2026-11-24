@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
    echo Run Windows-Setup.cmd first.
    pause
    exit /b 1
)

".venv-win\Scripts\python.exe" -m mipt_auto.gui
if errorlevel 1 (
    echo The application stopped with an error.
    pause
    exit /b 1
)
exit /b 0
