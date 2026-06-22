@echo off
REM ============================================================
REM Quick health check for local LLM proxy
REM ============================================================
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

if not defined JCLAW_GATEWAY_BASE_URL set "JCLAW_GATEWAY_BASE_URL=http://127.0.0.1:4000/v1/models"
if not defined JCLAW_GATEWAY_AUTH_TOKEN set "JCLAW_GATEWAY_AUTH_TOKEN=dummy-key"

echo Checking local LLM endpoint: %JCLAW_GATEWAY_BASE_URL%
python "%SCRIPT_DIR%\check-local-llm.py" --url "%JCLAW_GATEWAY_BASE_URL%" --key "%JCLAW_GATEWAY_AUTH_TOKEN%"
if errorlevel 1 (
    echo.
    echo WARNING: Local LLM is not reachable. Make sure your proxy is running:
    echo   powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\start-kobold-proxy.ps1"
    pause
    exit /b 1
)
echo.
echo Local LLM is healthy!
pause
