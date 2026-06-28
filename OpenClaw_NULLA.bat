@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%"
set "NULLA_PROJECT_ROOT=%SCRIPT_DIR%"
if "%NULLA_HOME%"=="" set "NULLA_HOME=%USERPROFILE%\.nulla_runtime"
set "SCRIPT_DRIVE=%~d0"
if "%OLLAMA_MODELS%"=="" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if not "%OLLAMA_MODELS%"=="" if not exist "%OLLAMA_MODELS%" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if "%OLLAMA_API_KEY%"=="" set "OLLAMA_API_KEY=ollama-local"

if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  echo NULLA is not installed yet. Bootstrapping...
  call "%SCRIPT_DIR%installer\install_nulla.bat" /Y "/OPENCLAW=default" /START
  if errorlevel 1 exit /b 1
  exit /b 0
)

set "MODEL_TAG=qwen2.5:7b"
for /f "tokens=*" %%A in ('"%SCRIPT_DIR%.venv\Scripts\python.exe" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('selected_model','qwen2.5:7b'))" 2^>nul') do set "MODEL_TAG=%%A"
if "%NULLA_OLLAMA_MODEL%"=="" set "NULLA_OLLAMA_MODEL=%MODEL_TAG%"
if not "%NULLA_OLLAMA_MODEL%"=="" set "MODEL_TAG=%NULLA_OLLAMA_MODEL%"
set "DISPLAY_NAME=NULLA"
for /f "tokens=*" %%A in ('"%SCRIPT_DIR%.venv\Scripts\python.exe" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_HOME',''))/'data'/'owner_identity.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('agent_name','NULLA'))" 2^>nul') do set "DISPLAY_NAME=%%A"
"%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%installer\register_openclaw_agent.py" "%SCRIPT_DIR%" "%NULLA_HOME%" "%MODEL_TAG%" "%DISPLAY_NAME%" >"%TEMP%\nulla_openclaw_register.log" 2>&1
"%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%ops\ensure_public_hive_auth.py" --project-root "%SCRIPT_DIR%" >"%TEMP%\nulla_public_hive_auth.log" 2>&1
if errorlevel 1 type "%TEMP%\nulla_public_hive_auth.log"

powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 goto open_openclaw

echo Starting NULLA...
start "" /B "%SCRIPT_DIR%.venv\Scripts\python.exe" -m apps.nulla_api_server
set "READY=0"
for /L %%i in (1,1,30) do (
  if !READY! equ 0 (
    timeout /t 1 /nobreak >nul
    powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! equ 0 set "READY=1"
  )
)

powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! neq 0 (
  set "OPENCLAW_CMD="
  for /f "tokens=*" %%C in ('where openclaw 2^>nul') do if "!OPENCLAW_CMD!"=="" set "OPENCLAW_CMD=%%C"
  if not "!OPENCLAW_CMD!"=="" (
    start "" /B "!OPENCLAW_CMD!" gateway run --force --port 18789
    for /L %%j in (1,1,30) do (
      timeout /t 1 /nobreak >nul
      powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
      if !errorlevel! equ 0 goto open_openclaw
    )
  )
)

powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! neq 0 (
  set "OLLAMA_EXE="
  where ollama >nul 2>&1 && set "OLLAMA_EXE=ollama"
  if "!OLLAMA_EXE!"=="" if exist "%SCRIPT_DRIVE%\Ollama\ollama.exe" set "OLLAMA_EXE=%SCRIPT_DRIVE%\Ollama\ollama.exe"
  if "!OLLAMA_EXE!"=="" if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  if "!OLLAMA_EXE!"=="" if exist "%SystemDrive%\Ollama\ollama.exe" set "OLLAMA_EXE=%SystemDrive%\Ollama\ollama.exe"
  if not "!OLLAMA_EXE!"=="" (
    start "" /B "!OLLAMA_EXE!" launch openclaw --yes --model "!MODEL_TAG!"
    for /L %%j in (1,1,30) do (
      timeout /t 1 /nobreak >nul
      powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
      if !errorlevel! equ 0 goto open_openclaw
    )
  )
)

:open_openclaw
set "GW_TOKEN="
for /f "tokens=*" %%A in ('"%SCRIPT_DIR%.venv\Scripts\python.exe" -c "from core.openclaw_locator import load_gateway_token; print(load_gateway_token())" 2^>nul') do set "GW_TOKEN=%%A"
set "TRACE_URL=http://127.0.0.1:11435/trace"

if not "%GW_TOKEN%"=="" (
  start "" "http://127.0.0.1:18789/chat?token=%GW_TOKEN%"
) else (
  start "" "http://127.0.0.1:18789"
)
start "" "%TRACE_URL%"

echo NULLA is running. OpenClaw opened.
echo NULLA trace rail: %TRACE_URL%
exit /b 0
