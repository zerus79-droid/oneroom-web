@echo off
setlocal
cd /d "%~dp0"

if not exist app.py (
  echo [ERROR] app.py not found: %CD%
  pause
  exit /b 1
)

echo [1/2] Stopping old Flask windows/processes...
REM Close previous console windows started by this script
taskkill /F /FI "WINDOWTITLE eq oneroom-flask*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq 원룸 Flask*" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "Get-NetTCPConnection -LocalPort 5000 -State Listen | ForEach-Object {" ^
  "  $p = $_.OwningProcess;" ^
  "  Write-Host ('  kill PID ' + $p);" ^
  "  Stop-Process -Id $p -Force;" ^
  "  Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $p } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" ^
  "};" ^
  "Get-CimInstance Win32_Process | Where-Object {" ^
  "  $_.Name -match 'python' -and $_.CommandLine -match 'app\.py'" ^
  "} | ForEach-Object {" ^
  "  Write-Host ('  kill python PID ' + $_.ProcessId);" ^
  "  Stop-Process -Id $_.ProcessId -Force" ^
  "}"

timeout /t 1 /nobreak >nul

echo [2/2] Starting Flask...
set "PY=C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

start "oneroom-flask" /D "%~dp0" cmd /k "%PY% app.py"

echo Done: http://127.0.0.1:5000
echo Old DOS window should be closed; only one Flask console remains.
timeout /t 2 /nobreak >nul
