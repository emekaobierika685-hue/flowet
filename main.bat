@echo off
setlocal EnableExtensions
title Endless Discord Nitro Gen

REM ---- Instantly jump to the folder where this .bat is located ----
cd /d "%~dp0"

REM ---- Python speed environment variables ----
set PYTHONUNBUFFERED=1
set PYTHONDONTWRITEBYTECODE=1
set PYTHONOPTIMIZE=2
set PYTHONHASHSEED=0

REM ---- Try the 'py' launcher first (fastest on Windows) ----
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -u -O main.py
    goto :end
)

REM ---- Fallback to 'python' ----
where python >nul 2>nul
if %errorlevel%==0 (
    python -u -O main.py
    goto :end
)

REM ---- Error if Python isn't found ----
echo.
echo [ERROR] Python was not found on this system.
echo Please install Python from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:end
echo.
echo Finished. Press any key to close.
pause >nul