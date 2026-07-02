@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
set "PYTHON_EXE=%SCRIPT_ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%SCRIPT_ROOT%"
set "NULLA_PROJECT_ROOT=%SCRIPT_ROOT%"
if exist "%USERPROFILE%\.local\bin" set "PATH=%USERPROFILE%\.local\bin;%PATH%"

if not exist "%PYTHON_EXE%" (
  echo NULLA is not installed yet. Bootstrapping...
  call "%SCRIPT_ROOT%\installer\install_nulla.bat" /Y "/OPENCLAW=default" /START
  if errorlevel 1 exit /b 1
  exit /b 0
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
set "RECEIPT_HOME_FILE=%TEMP%\nulla_oc_receipt_home_%RECEIPT_TAG%.txt"
set "RECEIPT_PROFILE_FILE=%TEMP%\nulla_oc_receipt_profile_%RECEIPT_TAG%.txt"
set "RECEIPT_MODEL_FILE=%TEMP%\nulla_oc_receipt_model_%RECEIPT_TAG%.txt"
set "OWNER_IDENTITY_FILE=%TEMP%\nulla_oc_owner_identity_%RECEIPT_TAG%.txt"
if "%NULLA_HOME%"=="" (
  "%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('runtime_home',''))" 1>"%RECEIPT_HOME_FILE%" 2>nul
  set "RECEIPT_HOME="
  for /f "tokens=*" %%A in ('type "%RECEIPT_HOME_FILE%" 2^>nul') do set "RECEIPT_HOME=%%A"
  if not "!RECEIPT_HOME!"=="" set "NULLA_HOME=!RECEIPT_HOME!"
)
if "%NULLA_HOME%"=="" for %%I in ("%SCRIPT_ROOT%\..\.nulla_runtime") do set "NULLA_HOME=%%~fI"
set "RECEIPT_INSTALL_PROFILE="
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print((data.get('install_profile') or {}).get('profile_id',''))" 1>"%RECEIPT_PROFILE_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%RECEIPT_PROFILE_FILE%" 2^>nul') do set "RECEIPT_INSTALL_PROFILE=%%A"
if "%NULLA_INSTALL_PROFILE%"=="" if not "!RECEIPT_INSTALL_PROFILE!"=="" set "NULLA_INSTALL_PROFILE=!RECEIPT_INSTALL_PROFILE!"
if "%NULLA_INSTALL_PROFILE%"=="" set "NULLA_INSTALL_PROFILE=local-only"
set "MODEL_TAG=qwen2.5:7b"
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('selected_model',''))" 1>"%RECEIPT_MODEL_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%RECEIPT_MODEL_FILE%" 2^>nul') do if not "%%A"=="" set "MODEL_TAG=%%A"
if not "%NULLA_ALLOW_MODEL_ENV_OVERRIDE%"=="1" set "NULLA_OLLAMA_MODEL=%MODEL_TAG%"
if "%NULLA_OLLAMA_MODEL%"=="" set "NULLA_OLLAMA_MODEL=%MODEL_TAG%"
if not "%NULLA_OLLAMA_MODEL%"=="" set "MODEL_TAG=%NULLA_OLLAMA_MODEL%"
set "DISPLAY_NAME=NULLA"
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('NULLA_HOME',''))/'data'/'owner_identity.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('agent_name',''))" 1>"%OWNER_IDENTITY_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%OWNER_IDENTITY_FILE%" 2^>nul') do if not "%%A"=="" set "DISPLAY_NAME=%%A"
del /f /q "%RECEIPT_HOME_FILE%" "%RECEIPT_PROFILE_FILE%" "%RECEIPT_MODEL_FILE%" "%OWNER_IDENTITY_FILE%" >nul 2>&1
if "%OLLAMA_MODELS%"=="" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if not "%OLLAMA_MODELS%"=="" if not exist "%OLLAMA_MODELS%" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if "%OLLAMA_API_KEY%"=="" set "OLLAMA_API_KEY=ollama-local"
set "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS=1"
set "PLAYWRIGHT_ENABLED=1"
set "ALLOW_BROWSER_FALLBACK=1"
set "BROWSER_ENGINE=chromium"
set "WEB_SEARCH_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html"
if "%NULLA_PUBLIC_HIVE_WATCH_HOST%"=="" set "NULLA_PUBLIC_HIVE_WATCH_HOST="
if "%SEARXNG_URL%"=="" set "SEARXNG_URL=http://127.0.0.1:8080"

"%PYTHON_EXE%" -m ops.ensure_public_hive_auth --project-root "%SCRIPT_ROOT%" --watch-host "%NULLA_PUBLIC_HIVE_WATCH_HOST%" >nul 2>&1
where docker >nul 2>&1
if %errorlevel% equ 0 powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_ROOT%\scripts\xsearch_up.ps1" >nul 2>&1
"%PYTHON_EXE%" "%SCRIPT_ROOT%\installer\register_openclaw_agent.py" "%SCRIPT_ROOT%" "%NULLA_HOME%" "%MODEL_TAG%" "%DISPLAY_NAME%" >nul 2>&1
REM Re-inject the native Web0 pill into OpenClaw's Control UI every launch. OpenClaw's
REM UI is a compiled bundle, so `npm install -g openclaw` upgrades wipe the injection;
REM re-applying here (idempotent) keeps the pill present across updates.
"%PYTHON_EXE%" "%SCRIPT_ROOT%\installer\inject_openclaw_web0_pill.py" >nul 2>&1
REM Patch OpenClaw's dashboard reply path to retry a conflicted session commit with
REM backoff (its Telegram path already does this) so rapid/overlapping turns stop
REM surfacing "reply session initialization conflicted". Idempotent; survives upgrades.
"%PYTHON_EXE%" "%SCRIPT_ROOT%\installer\patch_openclaw_session_retry.py" >nul 2>&1

REM Check if NULLA API is already running
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto ensure_gateway

echo Starting NULLA...
set "LAUNCH_REQUESTED=0"
schtasks /query /tn "NULLA_Daemon" >nul 2>&1
if !errorlevel! equ 0 (
  schtasks /run /tn "NULLA_Daemon" >nul 2>&1
  if !errorlevel! equ 0 set "LAUNCH_REQUESTED=1"
)
if "!LAUNCH_REQUESTED!"=="0" (
  "%SystemRoot%\System32\wscript.exe" "%SCRIPT_ROOT%\nulla_background.vbs"
  if !errorlevel! neq 0 (
    echo ERROR: Could not start NULLA API.
    powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:TEMP 'nulla_api.err.log') -Tail 80 -ErrorAction SilentlyContinue"
    powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:TEMP 'nulla_api_child.err.log') -Tail 80 -ErrorAction SilentlyContinue"
    exit /b 1
  )
)
set "READY=0"
for /L %%i in (1,1,120) do (
  if !READY! equ 0 (
    powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
    powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! equ 0 set "READY=1"
  )
)
if !READY! neq 1 (
  echo ERROR: NULLA API did not become healthy on http://127.0.0.1:11435/healthz.
  powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:TEMP 'nulla_api.err.log') -Tail 80 -ErrorAction SilentlyContinue"
  powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:TEMP 'nulla_api_child.err.log') -Tail 80 -ErrorAction SilentlyContinue"
  exit /b 1
)

:ensure_gateway
powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 18789 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 (
  set "OPENCLAW_CMD="
  for /f "tokens=*" %%C in ('where openclaw.cmd 2^>nul') do if "!OPENCLAW_CMD!"=="" set "OPENCLAW_CMD=%%C"
  for /f "tokens=*" %%C in ('where openclaw.exe 2^>nul') do if "!OPENCLAW_CMD!"=="" set "OPENCLAW_CMD=%%C"
  if "!OPENCLAW_CMD!"=="" if exist "%USERPROFILE%\.local\bin\openclaw.cmd" set "OPENCLAW_CMD=%USERPROFILE%\.local\bin\openclaw.cmd"
  if not "!OPENCLAW_CMD!"=="" (
    "%PYTHON_EXE%" "%SCRIPT_ROOT%\installer\start_windows_detached.py" --cwd "%SCRIPT_ROOT%" --stdout "%TEMP%\nulla_gateway.log" --stderr "%TEMP%\nulla_gateway.err.log" -- "!OPENCLAW_CMD!" gateway run --force --port 18789 >nul
    for /L %%j in (1,1,90) do (
      powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
      powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 18789 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
      if !errorlevel! equ 0 goto open_openclaw
    )
  )
)

powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 18789 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 (
  set "OLLAMA_EXE="
  where ollama >nul 2>&1 && set "OLLAMA_EXE=ollama"
  if "!OLLAMA_EXE!"=="" if exist "%SCRIPT_DRIVE%\Ollama\ollama.exe" set "OLLAMA_EXE=%SCRIPT_DRIVE%\Ollama\ollama.exe"
  if "!OLLAMA_EXE!"=="" if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  if "!OLLAMA_EXE!"=="" if exist "%SystemDrive%\Ollama\ollama.exe" set "OLLAMA_EXE=%SystemDrive%\Ollama\ollama.exe"
  if not "!OLLAMA_EXE!"=="" (
    "%PYTHON_EXE%" "%SCRIPT_ROOT%\installer\start_windows_detached.py" --cwd "%SCRIPT_ROOT%" --stdout "%TEMP%\nulla_gateway.log" --stderr "%TEMP%\nulla_gateway.err.log" -- "!OLLAMA_EXE!" launch openclaw --yes --model "!MODEL_TAG!" >nul
    for /L %%j in (1,1,90) do (
      powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
      powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 18789 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
      if !errorlevel! equ 0 goto open_openclaw
    )
  )
)

powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 18789 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 (
  echo ERROR: OpenClaw gateway did not become reachable on 127.0.0.1:18789.
  powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:TEMP 'nulla_gateway.err.log') -Tail 80 -ErrorAction SilentlyContinue"
  exit /b 1
)

:open_openclaw
set "GW_TOKEN="
set "TRACE_URL=http://127.0.0.1:11435/trace"
set "GW_TOKEN_FILE=%TEMP%\nulla_oc_gw_token_%RANDOM%_%RANDOM%.txt"
"%PYTHON_EXE%" -c "from core.openclaw_locator import load_gateway_token; print(load_gateway_token())" 1>"%GW_TOKEN_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%GW_TOKEN_FILE%" 2^>nul') do set "GW_TOKEN=%%A"
del /f /q "%GW_TOKEN_FILE%" >nul 2>&1
if not "!GW_TOKEN!"=="" (
  start "" "http://127.0.0.1:18789/#token=!GW_TOKEN!"
) else (
  start "" "http://127.0.0.1:18789"
)
start "" "%TRACE_URL%"
echo NULLA is running. OpenClaw is open.
echo NULLA trace rail: %TRACE_URL%
endlocal
