@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] 5000 포트 Flask 종료 중...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
  echo   PID %%a 종료
  taskkill /F /PID %%a >nul 2>&1
)
REM debug reloader 자식 프로세스도 정리
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo [2/2] Flask 시작...
set "PY=C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

start "원룸 Flask" cmd /k "cd /d "%~dp0" && "%PY%" app.py"
echo.
echo 완료: http://127.0.0.1:5000
timeout /t 2 /nobreak >nul
