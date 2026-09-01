@echo off
setlocal
cd /d "%~dp0"
if not exist ".env.production" (
    echo [ERROR] .env.production file not found.
    echo Copy .env.production.example and enter the production bot settings.
    exit /b 1
)
set "BOT_ENV_FILE=.env.production"
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
"%PYTHON_EXE%" main.py
endlocal
