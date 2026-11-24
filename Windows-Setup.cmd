@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
    py -3.12 -m venv ".venv-win"
    if errorlevel 1 goto python_missing
)

".venv-win\Scripts\python.exe" -c "import struct; assert struct.calcsize('P') == 8, '64-bit Python is required'"
if errorlevel 1 goto failed
".venv-win\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv-win\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed
".venv-win\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
".venv-win\Scripts\python.exe" -m mipt_auto.windows_setup
if errorlevel 1 goto failed
".venv-win\Scripts\python.exe" -m mipt_auto demo --headless --data-dir "data\windows-demo"
if errorlevel 1 goto failed

echo Setup complete. Run Windows-Start.cmd.
pause
exit /b 0

:python_missing
echo Python 3.12 x64 was not found. Install it from python.org, then run this file again.
pause
exit /b 1

:failed
echo Setup failed. The error is shown above.
pause
exit /b 1
