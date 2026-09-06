@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist app.py (
  echo [ERROR] app.py not found: %CD%
  pause
  exit /b 1
)

echo Stopping old Flask...
taskkill /F /FI "WINDOWTITLE eq oneroom-flask*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq oneroom-flask" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "Get-NetTCPConnection -LocalPort 5000 -State Listen | ForEach-Object {" ^
  "  $pid=$_.OwningProcess; Stop-Process -Id $pid -Force;" ^
  "  Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $pid } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" ^
  "};" ^
  "Get-CimInstance Win32_Process | Where-Object {" ^
  "  ($_.Name -match 'python') -and ($_.CommandLine -match 'app\.py')" ^
  "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force };" ^
  "Get-CimInstance Win32_Process | Where-Object {" ^
  "  ($_.Name -eq 'cmd.exe') -and ($_.CommandLine -match 'app\.py')" ^
  "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

timeout /t 1 /nobreak >nul

echo Starting Flask...
set "PY=C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

start "oneroom-flask" /D "%~dp0" cmd /k "%PY% app.py"

echo Done: http://127.0.0.1:5000
timeout /t 2 /nobreak >nul
