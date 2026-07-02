@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
set "PYTHON_EXE=%SCRIPT_ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%SCRIPT_ROOT%"
set "NULLA_PROJECT_ROOT=%SCRIPT_ROOT%"

if not exist "%PYTHON_EXE%" (
  echo NULLA is not installed yet. Bootstrapping...
  call "%SCRIPT_ROOT%\installer\install_nulla.bat" /Y "/OPENCLAW=default"
  if errorlevel 1 exit /b 1
)

set "SCRIPT_DRIVE=%~d0"
REM NOTE: `for /f "..." %%A in ('command with its own nested quotes') do ...` silently
REM breaks when the command's own path (here %PYTHON_EXE%) contains a space, which is
REM common on Windows (e.g. this project living under a folder with a space in its name).
REM The nested quoting inside the for/f command clause gets mis-parsed and the loop
REM produces no output, so receipt-derived values silently fall back to hardcoded
REM defaults. Route each python -c call's stdout through a temp file instead, then read
REM that file back with a simple, unnested `for /f ... in ('type "file"') do ...`. The
REM file name is unique per invocation so overlapping/rapid restarts never collide.
set "RECEIPT_TAG=%RANDOM%_%RANDOM%"
set "RECEIPT_HOME_FILE=%TEMP%\nulla_talk_receipt_home_%RECEIPT_TAG%.txt"
set "RECEIPT_PROFILE_FILE=%TEMP%\nulla_talk_receipt_profile_%RECEIPT_TAG%.txt"
set "RECEIPT_MODEL_FILE=%TEMP%\nulla_talk_receipt_model_%RECEIPT_TAG%.txt"
if "%NULLA_HOME%"=="" (
  "%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('runtime_home',''))" 1>"%RECEIPT_HOME_FILE%" 2>nul
  set "RECEIPT_HOME="
  for /f "tokens=*" %%A in ('type "%RECEIPT_HOME_FILE%" 2^>nul') do set "RECEIPT_HOME=%%A"
  if not "!RECEIPT_HOME!"=="" set "NULLA_HOME=!RECEIPT_HOME!"
)
if "%NULLA_HOME%"=="" for %%I in ("%SCRIPT_ROOT%\..\.nulla_runtime") do set "NULLA_HOME=%%~fI"
if "%NULLA_INSTALL_PROFILE%"=="" (
  "%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print((data.get('install_profile') or {}).get('profile_id',''))" 1>"%RECEIPT_PROFILE_FILE%" 2>nul
  set "RECEIPT_PROFILE="
  for /f "tokens=*" %%A in ('type "%RECEIPT_PROFILE_FILE%" 2^>nul') do set "RECEIPT_PROFILE=%%A"
  if not "!RECEIPT_PROFILE!"=="" set "NULLA_INSTALL_PROFILE=!RECEIPT_PROFILE!"
)
if "%NULLA_INSTALL_PROFILE%"=="" set "NULLA_INSTALL_PROFILE=local-only"
set "RECEIPT_MODEL="
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('selected_model',''))" 1>"%RECEIPT_MODEL_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%RECEIPT_MODEL_FILE%" 2^>nul') do set "RECEIPT_MODEL=%%A"
if not "!RECEIPT_MODEL!"=="" if not "%NULLA_ALLOW_MODEL_ENV_OVERRIDE%"=="1" set "NULLA_OLLAMA_MODEL=!RECEIPT_MODEL!"
if "%NULLA_OLLAMA_MODEL%"=="" if not "!RECEIPT_MODEL!"=="" set "NULLA_OLLAMA_MODEL=!RECEIPT_MODEL!"
if "%NULLA_OLLAMA_MODEL%"=="" set "NULLA_OLLAMA_MODEL=qwen2.5:7b"
del /f /q "%RECEIPT_HOME_FILE%" "%RECEIPT_PROFILE_FILE%" "%RECEIPT_MODEL_FILE%" >nul 2>&1
if "%OLLAMA_MODELS%"=="" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if not "%OLLAMA_MODELS%"=="" if not exist "%OLLAMA_MODELS%" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if "%OLLAMA_API_KEY%"=="" set "OLLAMA_API_KEY=ollama-local"
set "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS=1"
set "PLAYWRIGHT_ENABLED=1"
set "ALLOW_BROWSER_FALLBACK=1"
set "BROWSER_ENGINE=chromium"
set "WEB_SEARCH_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html"

"%PYTHON_EXE%" -m apps.nulla_chat --platform openclaw --device openclaw
pause
endlocal
