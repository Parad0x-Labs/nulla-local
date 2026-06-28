@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%"
if "%NULLA_HOME%"=="" set "NULLA_HOME=%USERPROFILE%\.nulla_runtime"
set "SCRIPT_DRIVE=%~d0"
if "%OLLAMA_MODELS%"=="" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if not "%OLLAMA_MODELS%"=="" if not exist "%OLLAMA_MODELS%" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if "%OLLAMA_API_KEY%"=="" set "OLLAMA_API_KEY=ollama-local"
if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  echo NULLA is not installed yet. Bootstrapping...
  call "%SCRIPT_DIR%installer\install_nulla.bat" /Y "/OPENCLAW=default"
  if errorlevel 1 exit /b 1
)
"%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%ops\ensure_public_hive_auth.py" --project-root "%SCRIPT_DIR%" >"%TEMP%\nulla_public_hive_auth.log" 2>&1
if errorlevel 1 type "%TEMP%\nulla_public_hive_auth.log"
echo Starting NULLA (API + mesh daemon)...
echo OpenClaw connects to http://127.0.0.1:11435
echo.
"%SCRIPT_DIR%.venv\Scripts\python.exe" -m apps.nulla_api_server
endlocal
