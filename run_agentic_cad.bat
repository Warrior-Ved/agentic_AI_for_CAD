@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Agentic CAD Assistant - Launcher
echo ============================================
echo.

REM --- Make sure Ollama's local server is up (harmless if it's already running) ---
echo Starting Ollama (if not already running)...
start "Ollama" /min cmd /c "ollama serve >nul 2>&1"

REM --- Open the browser a few seconds after launch, once the server is up ---
start "" cmd /c "timeout /t 4 /nobreak >nul && start "" http://127.0.0.1:8000"

REM --- Launch the FastAPI backend (it also serves the frontend - one process) ---
cd backend
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: venv not found at backend\.venv
    echo See README.md "Build / setup" to create it first.
    echo.
    pause
    exit /b 1
)

echo Launching Agentic CAD at http://127.0.0.1:8000
echo Close this window to stop the server.
echo.
".venv\Scripts\python.exe" scripts\serve.py

pause
