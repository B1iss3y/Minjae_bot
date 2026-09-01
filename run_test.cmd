@echo off
setlocal
cd /d "%~dp0"
if not exist ".env.test" (
    echo [ERROR] .env.test file not found.
    echo Copy .env.test.example and enter the test bot settings.
    exit /b 1
)
set "BOT_ENV_FILE=.env.test"
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
"%PYTHON_EXE%" main.py
endlocal
