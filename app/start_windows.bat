@echo off
setlocal
rem Guitar Tab Extractor - Windows one-click launcher
rem (All messages here are kept in plain English on purpose: Windows' cmd.exe
rem  reads .bat files using the system's legacy codepage, not UTF-8, so Chinese
rem  text in a .bat file can get corrupted and break command parsing on some
rem  Windows locales. See README.md for the Chinese explanation of each step.)

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on this computer.
  echo Please install Python 3.9+ from https://www.python.org/downloads/
  echo During install, make sure to check "Add Python to PATH", then run this file again.
  pause
  exit /b 1
)

if not exist venv (
  echo First run: creating a virtual environment...
  python -m venv venv
)

call venv\Scripts\activate.bat

if not exist venv\.deps_installed (
  echo Installing required packages, this can take a few minutes the first time...
  python -m pip install --upgrade pip --quiet --no-cache-dir
  pip install -r requirements.txt --quiet --no-cache-dir
  type nul > venv\.deps_installed
)

rem If a server from an earlier run is still running (e.g. an old window was
rem not closed), stop it first. Otherwise the new server cannot use port 5001
rem and the browser keeps showing the OLD version of the tool.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":5001 .*LISTENING"') do (
  echo Stopping the server that is still running from before...
  taskkill /PID %%p /F >nul 2>nul
)

rem Always open the tool in Google Chrome (not Edge); fall back to the default
rem browser only when Chrome is not installed.
set "GTE_CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "GTE_CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined GTE_CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "GTE_CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined GTE_CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "GTE_CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined GTE_CHROME echo Google Chrome was not found, the default browser will be used instead.

echo.
echo Starting server...
start "" /B powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 2; if ($env:GTE_CHROME) { Start-Process -FilePath $env:GTE_CHROME -ArgumentList 'http://127.0.0.1:5001' } else { Start-Process 'http://127.0.0.1:5001' }"

python app.py

pause
