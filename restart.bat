@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist "app.py" (
  echo [오류] app.py 가 없습니다. 경로: %CD%
  pause
  exit /b 1
)

echo [1/2] 5000 포트 종료 중...
powershell -NoProfile -Command ^
  "Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul

echo [2/2] Flask 시작...
set "PY=C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  where python >nul 2>&1 && set "PY=python"
)
if not exist "%PY%" if /I not "%PY%"=="python" (
  echo [오류] Python 을 찾지 못했습니다.
  echo 경로 예: C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe
  pause
  exit /b 1
)

REM /D 로 작업폴더 지정 — 따옴표 중첩 깨짐 방지
start "원룸 Flask" /D "%~dp0" cmd /k ""%PY%" app.py"

echo.
echo 완료: http://127.0.0.1:5000
echo 콘솔 창이 뜨면 정상입니다.
timeout /t 2 /nobreak >nul
endlocal
