@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python이 설치되어 있지 않습니다. Python 3.11 이상을 설치한 뒤 다시 실행하세요.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo 가상환경을 생성합니다...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

echo 패키지를 설치합니다...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo .env 파일을 만들었습니다. DB_PASSWORD와 SECRET_KEY를 실제 값으로 수정하세요.
) else (
    echo 기존 .env 파일을 유지합니다.
)

echo 설치가 완료되었습니다. .env 설정 후 start.bat을 실행하세요.
pause
exit /b 0

:fail
echo 설치 중 오류가 발생했습니다.
pause
exit /b 1
