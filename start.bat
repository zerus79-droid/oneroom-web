@echo off
cd /d "%~dp0"

start "Flask App" cmd /k "python app.py"
timeout /t 2 /nobreak >nul
start "Browser" "http://127.0.0.1:5000/login"
