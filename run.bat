@echo off
setlocal
cd /d "%~dp0"

echo Checking Python installation...
where py >nul 2>&1
if errorlevel 1 (
    echo The Python launcher was not found. Install Python 3.11 from https://www.python.org/downloads/
    echo Make sure "Add Python to PATH" is enabled during installation.
    pause
    exit /b 1
)

py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 was not found. Install Python 3.11 from https://www.python.org/downloads/
    pause
    exit /b 1
)

rem Discard a virtual environment built with a different Python version.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Existing virtual environment is not Python 3.11, recreating...
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment with Python 3.11...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

rem Only run pip when requirements.txt changed since the last successful install.
set "STAMP=.venv\requirements.installed.txt"
fc /b requirements.txt "%STAMP%" >nul 2>&1
if not errorlevel 1 goto deps_ok

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)
copy /y requirements.txt "%STAMP%" >nul

:deps_ok

rem Match UI_SCALE in config.py to the Windows display scaling (96 DPI = 100%).
set "DPI=96"
for /f "tokens=3" %%A in ('reg query "HKCU\Control Panel\Desktop\WindowMetrics" /v AppliedDPI 2^>nul ^| find "AppliedDPI"') do set /a DPI=%%A
if "%DPI%"=="96" (
    for /f "tokens=3" %%A in ('reg query "HKCU\Control Panel\Desktop" /v LogPixels 2^>nul ^| find "LogPixels"') do set /a DPI=%%A
)
powershell -NoProfile -Command "$s=[math]::Round([int]$env:DPI/96,2).ToString([cultureinfo]::InvariantCulture); $p=Join-Path (Get-Location) 'config.py'; $t=[IO.File]::ReadAllText($p); [IO.File]::WriteAllText($p, [regex]::Replace($t,'(?m)^UI_SCALE = [^\r\n]*','UI_SCALE = '+$s))"

echo Starting CDID Barista...
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo CDID Barista stopped with an error.
    pause
)

endlocal
