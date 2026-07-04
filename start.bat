@echo off
chcp 65001 >nul
title Evomon Auto-Hunter

REM === Request Administrator rights (required: keys won't reach the game otherwise) ===
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Requesting Administrator rights...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

REM === Go to this script's folder ===
cd /d "%~dp0"

REM === Use bundled portable Python if present (portable zip = no installs needed) ===
if exist "%~dp0python\python.exe" (
    set "PYCMD="%~dp0python\python.exe""
) else (
    set "PYCMD=py -3.12"
)

REM === Auto-update from GitHub (skips quietly when offline) ===
if exist update.py %PYCMD% update.py

REM === update.py can't overwrite this running script -> swap and restart ===
if exist "start.bat.new" (
    echo [*] start.bat updated, restarting...
    move /y "start.bat.new" "start.bat" >nul
    start "" "%~f0"
    exit /b
)

REM === Install dependencies on first run (if any lib missing) ===
%PYCMD% -c "import rapidocr_onnxruntime, customtkinter" 2>nul
if %errorlevel% neq 0 (
    echo [*] First run: installing dependencies...
    %PYCMD% -m pip install -r requirements.txt
)

%PYCMD% gui.py
if %errorlevel% neq 0 (
    echo.
    echo [!] failed, trying system python instead...
    python gui.py
)

echo.
pause
