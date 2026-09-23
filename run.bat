@echo off
setlocal
cd /d "%~dp0"

echo Checking Python installation...
where py >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
    echo Make sure "Add Python to PATH" is enabled during installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo Starting CDID Barista...
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo CDID Barista stopped with an error.
    pause
)

endlocal
