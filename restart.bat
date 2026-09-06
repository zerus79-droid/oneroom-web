@echo off
setlocal
cd /d "%~dp0"

if not exist app.py (
  echo [ERROR] app.py not found: %CD%
  pause
  exit /b 1
)

echo [1/2] Stopping port 5000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul

echo [2/2] Starting Flask...
set "PY=C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

start "oneroom-flask" /D "%~dp0" cmd /k "%PY% app.py"

echo Done: http://127.0.0.1:5000
timeout /t 2 /nobreak >nul
