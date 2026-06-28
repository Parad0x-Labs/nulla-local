@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "AUTO_YES=0"
set "AUTO_START=0"
set "NULLA_HOME_OVERRIDE="
set "INSTALL_PROFILE_OVERRIDE=%NULLA_INSTALL_PROFILE%"
set "AGENT_NAME_OVERRIDE=%NULLA_AGENT_NAME%"
set "OPENCLAW_MODE=default"
set "OPENCLAW_PATH_OVERRIDE="
set "OPENCLAW_AGENT_DEFAULT=%USERPROFILE%\.openclaw\agents\main\agent\nulla"
set "DESKTOP_SHORTCUT="
set "RUNTIME_REQUIREMENTS=%PROJECT_ROOT%\requirements-runtime.txt"
set "WHEELHOUSE_DIR=%PROJECT_ROOT%\vendor\wheelhouse"
set "BUNDLED_LIQUEFY_DIR=%PROJECT_ROOT%\vendor\liquefy-openclaw-integration"
set "XSEARCH_URL=http://127.0.0.1:8080"
set "WEB_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html"
set "DEFAULT_BROWSER_ENGINE=chromium"
set "PUBLIC_HIVE_SSH_KEY_PATH=%NULLA_PUBLIC_HIVE_SSH_KEY_PATH%"
set "PUBLIC_HIVE_WATCH_HOST=%NULLA_PUBLIC_HIVE_WATCH_HOST%"
if "%PUBLIC_HIVE_WATCH_HOST%"=="" set "PUBLIC_HIVE_WATCH_HOST="

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/Y" (
  set "AUTO_YES=1"
  shift
  goto parse_args
)
if /i "%~1"=="/START" (
  set "AUTO_START=1"
  shift
  goto parse_args
)
if /i "%~1"=="/INSTALLPROFILE" (
  shift
  if "%~1"=="" (
    echo ERROR: /INSTALLPROFILE requires a value.
    goto usage
  )
  set "INSTALL_PROFILE_OVERRIDE=%~1"
  shift
  goto parse_args
)
if /i "%~1"=="/NOOPENCLAW" (
  set "OPENCLAW_MODE=skip"
  shift
  goto parse_args
)
if /i "%~1"=="/OPENCLAW" (
  shift
  if "%~1"=="" (
    echo ERROR: /OPENCLAW requires a value.
    goto usage
  )
  set "OPENCLAW_RAW=%~1"
  if /i "%OPENCLAW_RAW%"=="skip" set "OPENCLAW_MODE=skip"
  if /i "%OPENCLAW_RAW%"=="default" set "OPENCLAW_MODE=default"
  if /i "%OPENCLAW_RAW%"=="prompt" set "OPENCLAW_MODE=prompt"
  if /i not "%OPENCLAW_RAW%"=="skip" if /i not "%OPENCLAW_RAW%"=="default" if /i not "%OPENCLAW_RAW%"=="prompt" (
    set "OPENCLAW_MODE=path"
    set "OPENCLAW_PATH_OVERRIDE=%OPENCLAW_RAW%"
  )
  shift
  goto parse_args
)
if /i "%~1"=="/HELP" goto usage
if /i "%~1"=="/?" goto usage
set "ARG=%~1"
if /i "%ARG:~0,11%"=="/NULLAHOME=" (
  set "NULLA_HOME_OVERRIDE=%ARG:~11%"
  shift
  goto parse_args
)
if /i "%ARG:~0,11%"=="/AGENTNAME=" (
  set "AGENT_NAME_OVERRIDE=%ARG:~11%"
  shift
  goto parse_args
)
if /i "%ARG:~0,16%"=="/INSTALLPROFILE=" (
  set "INSTALL_PROFILE_OVERRIDE=%ARG:~16%"
  shift
  goto parse_args
)
if /i "%ARG:~0,10%"=="/OPENCLAW=" (
  set "OPENCLAW_RAW=%ARG:~10%"
  if /i "%OPENCLAW_RAW%"=="skip" set "OPENCLAW_MODE=skip"
  if /i "%OPENCLAW_RAW%"=="default" set "OPENCLAW_MODE=default"
  if /i "%OPENCLAW_RAW%"=="prompt" set "OPENCLAW_MODE=prompt"
  if /i not "%OPENCLAW_RAW%"=="skip" if /i not "%OPENCLAW_RAW%"=="default" if /i not "%OPENCLAW_RAW%"=="prompt" (
    set "OPENCLAW_MODE=path"
    set "OPENCLAW_PATH_OVERRIDE=%OPENCLAW_RAW%"
  )
  shift
  goto parse_args
)
echo ERROR: Unknown option %~1
goto usage

:usage
echo Usage: install_nulla.bat [/Y] [/START] [/NOOPENCLAW] [/NULLAHOME=PATH] [/INSTALLPROFILE=ID] [/AGENTNAME=NAME] [/OPENCLAW=skip^|default^|prompt^|PATH]
exit /b 2

:args_done
if /i not "%INSTALL_PROFILE_OVERRIDE%"=="" (
  call :validate_install_profile "%INSTALL_PROFILE_OVERRIDE%"
  if errorlevel 1 exit /b 2
)

echo ===============================================
echo NULLA Installer (Windows)
echo This will set up NULLA in the extracted folder.
echo ===============================================
echo.

where py >nul 2>&1
if %errorlevel% neq 0 (
  where python >nul 2>&1
  if %errorlevel% neq 0 (
    echo ERROR: Python was not found. Install Python 3.10+ and retry.
    exit /b 1
  )
  set "PYTHON_CMD=python"
) else (
  set "PYTHON_CMD=py -3"
)

set "NULLA_HOME_DEFAULT=%USERPROFILE%\.nulla_runtime"
set "AGENT_NAME_DEFAULT=NULLA"
if not "%NULLA_HOME_OVERRIDE%"=="" (
  set "NULLA_HOME=%NULLA_HOME_OVERRIDE%"
) else if "%AUTO_YES%"=="1" (
  set "NULLA_HOME=%NULLA_HOME_DEFAULT%"
) else (
  set /p "NULLA_HOME=NULLA runtime folder [%NULLA_HOME_DEFAULT%]: "
  if "%NULLA_HOME%"=="" set "NULLA_HOME=%NULLA_HOME_DEFAULT%"
)
if not "%AGENT_NAME_OVERRIDE%"=="" set "AGENT_NAME_DEFAULT=%AGENT_NAME_OVERRIDE%"
if "%AUTO_YES%"=="1" (
  set "AGENT_NAME=%AGENT_NAME_DEFAULT%"
) else (
  set /p "AGENT_NAME=Agent display name [%AGENT_NAME_DEFAULT%]: "
  if "%AGENT_NAME%"=="" set "AGENT_NAME=%AGENT_NAME_DEFAULT%"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Step 1/14: Creating virtual environment...
  %PYTHON_CMD% -m venv "%VENV_DIR%"
) else (
  echo Step 1/14: Virtual environment already exists.
)

echo Step 2/14: Installing dependencies (this can take a while)...
set "REQUIREMENTS_FILE=%PROJECT_ROOT%\requirements.txt"
if exist "%RUNTIME_REQUIREMENTS%" set "REQUIREMENTS_FILE=%RUNTIME_REQUIREMENTS%"
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade "pip<26" setuptools wheel
if exist "%WHEELHOUSE_DIR%\*" (
  echo Using bundled wheelhouse from %WHEELHOUSE_DIR%...
  "%VENV_DIR%\Scripts\python.exe" -m pip install --no-index --find-links "%WHEELHOUSE_DIR%" -r "%REQUIREMENTS_FILE%"
  if %errorlevel% neq 0 (
    echo WARNING: Bundled wheelhouse install failed. Falling back to online install...
    "%VENV_DIR%\Scripts\python.exe" -m pip install "%PROJECT_ROOT%[runtime,proof]"
  )
) else (
  "%VENV_DIR%\Scripts\python.exe" -m pip install "%PROJECT_ROOT%[runtime,proof]"
)
if exist "%WHEELHOUSE_DIR%\*" (
  "%VENV_DIR%\Scripts\python.exe" -m pip install --no-deps "%PROJECT_ROOT%"
)
if %errorlevel% neq 0 (
  echo ERROR: Core dependency installation failed. Cannot continue.
  exit /b 1
)

echo Step 3/14: Installing Playwright browser runtime...
"%VENV_DIR%\Scripts\python.exe" -m playwright install %DEFAULT_BROWSER_ENGINE% >nul 2>"%TEMP%\nulla_playwright_install.log"
if %errorlevel% neq 0 (
  echo WARNING: Playwright browser install failed. Browser rendering may stay unavailable until fixed manually.
) else (
  echo Playwright %DEFAULT_BROWSER_ENGINE% runtime installed.
)

echo Step 4/14: Enabling local XSEARCH ^(SearXNG^)...
where docker >nul 2>&1
if %errorlevel% neq 0 (
  echo WARNING: Docker not found. SearXNG bootstrap skipped.
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\xsearch_up.ps1" >"%TEMP%\nulla_xsearch_install.log" 2>&1
  if %errorlevel% neq 0 (
    echo WARNING: Could not start SearXNG automatically. Docker or docker compose may be unavailable.
  ) else (
    powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri '%XSEARCH_URL%/search?q=nulla^&format=json' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
    if %errorlevel% neq 0 (
      echo WARNING: SearXNG bootstrap ran but readiness check failed at %XSEARCH_URL%.
    ) else (
      echo Local XSEARCH online at %XSEARCH_URL%
    )
  )
)

REM Liquefy: clone into OpenClaw folder, patch build-backend, install into NULLA venv.
REM This keeps Liquefy scoped to the OpenClaw workspace (not global).
set "LIQUEFY_DIR="
if exist "%BUNDLED_LIQUEFY_DIR%\pyproject.toml" (
  set "LIQUEFY_DIR=%BUNDLED_LIQUEFY_DIR%"
  echo Using bundled Liquefy payload.
) else if exist "%PROJECT_ROOT%\..\liquefy-openclaw-integration\pyproject.toml" (
  set "LIQUEFY_DIR=%PROJECT_ROOT%\..\liquefy-openclaw-integration"
)
if "%LIQUEFY_DIR%"=="" (
  where git >nul 2>&1
  if %errorlevel% equ 0 (
    set "LIQUEFY_DIR=%PROJECT_ROOT%\..\liquefy-openclaw-integration"
    if not exist "%LIQUEFY_DIR%\pyproject.toml" (
      echo Cloning Liquefy into OpenClaw folder...
      git clone --depth 1 https://github.com/Parad0x-Labs/liquefy-openclaw-integration.git "%LIQUEFY_DIR%" >nul 2>&1
    )
    if not exist "%LIQUEFY_DIR%\pyproject.toml" set "LIQUEFY_DIR="
  ) else (
    echo WARNING: git not found and no bundled Liquefy payload is present. Continuing without Liquefy.
  )
)
if not "%LIQUEFY_DIR%"=="" (
  powershell -NoProfile -Command "(Get-Content '%LIQUEFY_DIR%\pyproject.toml') -replace 'setuptools\.backends\._legacy:_Backend','setuptools.build_meta' | Set-Content '%LIQUEFY_DIR%\pyproject.toml'"
  "%VENV_DIR%\Scripts\python.exe" -m pip install "%LIQUEFY_DIR%" >nul 2>&1
  if %errorlevel% equ 0 (
    echo Liquefy installed into NULLA venv from OpenClaw folder.
  ) else (
    echo WARNING: Liquefy installation failed. Continuing without it.
  )
) else (
  echo WARNING: Could not locate Liquefy. Continuing without it.
)

echo Step 5/14: Initializing runtime...
set "NULLA_HOME=%NULLA_HOME%"
"%VENV_DIR%\Scripts\python.exe" -m storage.migrations
if %errorlevel% neq 0 exit /b 1
echo Step 5b/14: Ensuring public Hive auth/bootstrap...
"%VENV_DIR%\Scripts\python.exe" -m ops.ensure_public_hive_auth --project-root "%PROJECT_ROOT%" --watch-host "%PUBLIC_HIVE_WATCH_HOST%" --json >"%TEMP%\nulla_public_hive_auth.json" 2>"%TEMP%\nulla_public_hive_auth.err"
if %errorlevel% neq 0 (
  echo WARNING: Public Hive auth/bootstrap is incomplete. Public Hive writes and watcher presence/export will stay offline until auth is configured.
  if exist "%TEMP%\nulla_public_hive_auth.json" type "%TEMP%\nulla_public_hive_auth.json"
  if exist "%TEMP%\nulla_public_hive_auth.err" type "%TEMP%\nulla_public_hive_auth.err"
) else (
  for /f "tokens=*" %%A in ('type "%TEMP%\nulla_public_hive_auth.json"') do set "PUBLIC_HIVE_AUTH_STATUS=%%A"
  echo Public Hive auth/bootstrap status: %PUBLIC_HIVE_AUTH_STATUS%
)
echo Seeding agent identity...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%seed_identity.py" --agent-name "%AGENT_NAME%" 2>nul > "%TEMP%\nulla_agent_name.txt"
if exist "%TEMP%\nulla_agent_name.txt" (
  set /p AGENT_NAME=<"%TEMP%\nulla_agent_name.txt"
  del /f /q "%TEMP%\nulla_agent_name.txt" >nul 2>&1
)

echo Step 6/14: Detecting hardware and recommended model...
set "MODEL_TAG="
"%VENV_DIR%\Scripts\python.exe" -c "from core.install_recommendations import build_install_recommendation_truth; print(build_install_recommendation_truth().primary_local_model)" 2>nul > "%TEMP%\nulla_model_tag.txt"
set /p MODEL_TAG=<"%TEMP%\nulla_model_tag.txt"
del /f /q "%TEMP%\nulla_model_tag.txt" >nul 2>&1
if "%MODEL_TAG%"=="" set "MODEL_TAG=qwen2.5:7b"
set "RECOMMENDED_BUNDLE_MODELS="
"%VENV_DIR%\Scripts\python.exe" -c "from core.install_recommendations import build_install_recommendation_truth; print(','.join(build_install_recommendation_truth().recommended_bundle_models))" 2>nul > "%TEMP%\nulla_bundle_models.txt"
if exist "%TEMP%\nulla_bundle_models.txt" (
  set /p RECOMMENDED_BUNDLE_MODELS=<"%TEMP%\nulla_bundle_models.txt"
  del /f /q "%TEMP%\nulla_bundle_models.txt" >nul 2>&1
)
if "%RECOMMENDED_BUNDLE_MODELS%"=="" set "RECOMMENDED_BUNDLE_MODELS=%MODEL_TAG%"
"%VENV_DIR%\Scripts\python.exe" -c "import json; from core.hardware_tier import tier_summary; print(json.dumps(tier_summary(), ensure_ascii=False))" 2>nul > "%TEMP%\nulla_hw.txt"
set "HARDWARE_SUMMARY="
set /p HARDWARE_SUMMARY=<"%TEMP%\nulla_hw.txt"
del /f /q "%TEMP%\nulla_hw.txt" >nul 2>&1
set "INSTALL_PROFILE=local-only"
set "RECOMMENDED_INSTALL_PROFILE=local-only"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(selected_model=r'%MODEL_TAG%', runtime_home=r'%NULLA_HOME%', provider_capability_truth=snapshot.capability_truth).profile_id)" 2>nul > "%TEMP%\nulla_install_profile.txt"
if exist "%TEMP%\nulla_install_profile.txt" (
  set /p RECOMMENDED_INSTALL_PROFILE=<"%TEMP%\nulla_install_profile.txt"
  del /f /q "%TEMP%\nulla_install_profile.txt" >nul 2>&1
)
if "%INSTALL_PROFILE_OVERRIDE%"=="" (
  if "%AUTO_YES%"=="1" (
    set "INSTALL_PROFILE_OVERRIDE=auto-recommended"
  ) else (
    set /p "INSTALL_PROFILE_OVERRIDE=Install profile [auto-recommended/local-only/local-max] [auto-recommended]: "
    if "%INSTALL_PROFILE_OVERRIDE%"=="" set "INSTALL_PROFILE_OVERRIDE=auto-recommended"
    call :validate_install_profile "%INSTALL_PROFILE_OVERRIDE%"
    if errorlevel 1 exit /b 2
  )
)
set "NULLA_INSTALL_PROFILE=%INSTALL_PROFILE_OVERRIDE%"
set "INSTALL_PROFILE_SUMMARY=%RECOMMENDED_INSTALL_PROFILE% -> %MODEL_TAG%"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(requested_profile=r'%NULLA_INSTALL_PROFILE%', selected_model=r'%MODEL_TAG%', runtime_home=r'%NULLA_HOME%', provider_capability_truth=snapshot.capability_truth).display_summary())" 2>nul > "%TEMP%\nulla_install_profile_summary.txt"
if exist "%TEMP%\nulla_install_profile_summary.txt" (
  set /p INSTALL_PROFILE_SUMMARY=<"%TEMP%\nulla_install_profile_summary.txt"
  del /f /q "%TEMP%\nulla_install_profile_summary.txt" >nul 2>&1
)
set "INSTALL_PROFILE=%RECOMMENDED_INSTALL_PROFILE%"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(requested_profile=r'%NULLA_INSTALL_PROFILE%', selected_model=r'%MODEL_TAG%', runtime_home=r'%NULLA_HOME%', provider_capability_truth=snapshot.capability_truth).profile_id)" 2>nul > "%TEMP%\nulla_selected_install_profile.txt"
if exist "%TEMP%\nulla_selected_install_profile.txt" (
  set /p INSTALL_PROFILE=<"%TEMP%\nulla_selected_install_profile.txt"
  del /f /q "%TEMP%\nulla_selected_install_profile.txt" >nul 2>&1
)
set "NULLA_INSTALL_PROFILE=%INSTALL_PROFILE%"
echo Detected: %HARDWARE_SUMMARY%
echo Selected model: %MODEL_TAG%
echo Recommended local bundle: %RECOMMENDED_BUNDLE_MODELS%
echo Recommended profile: %RECOMMENDED_INSTALL_PROFILE%
echo Install profile: %INSTALL_PROFILE%
echo Profile summary: %INSTALL_PROFILE_SUMMARY%
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%validate_install_profile.py" "%NULLA_HOME%" "%MODEL_TAG%" "%INSTALL_PROFILE%" >"%TEMP%\nulla_install_profile_validate.txt" 2>&1
if %errorlevel% neq 0 (
  type "%TEMP%\nulla_install_profile_validate.txt"
  exit /b 1
)
if exist "%TEMP%\nulla_install_profile_validate.txt" del /f /q "%TEMP%\nulla_install_profile_validate.txt" >nul 2>&1

echo Step 7/14: Creating launchers...
(
  echo @echo off
  echo set "NULLA_HOME=%NULLA_HOME%"
  echo set "NULLA_INSTALL_PROFILE=%INSTALL_PROFILE%"
  echo if "%%NULLA_OLLAMA_MODEL%%"=="" set "NULLA_OLLAMA_MODEL=%MODEL_TAG%"
  echo if "%%OLLAMA_MODELS%%"=="" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if not "%%OLLAMA_MODELS%%"=="" if not exist "%%OLLAMA_MODELS%%" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if "%%OLLAMA_API_KEY%%"=="" set "OLLAMA_API_KEY=ollama-local"
  echo set "PLAYWRIGHT_ENABLED=1"
  echo set "ALLOW_BROWSER_FALLBACK=1"
  echo set "BROWSER_ENGINE=%DEFAULT_BROWSER_ENGINE%"
  echo set "WEB_SEARCH_PROVIDER_ORDER=%WEB_PROVIDER_ORDER%"
  echo if "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%"=="" set "NULLA_PUBLIC_HIVE_WATCH_HOST=%PUBLIC_HIVE_WATCH_HOST%"
  echo "%VENV_DIR%\Scripts\python.exe" -m ops.ensure_public_hive_auth --project-root "%PROJECT_ROOT%" --watch-host "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%" ^>nul 2^>^&1
  echo if "%%SEARXNG_URL%%"=="" set "SEARXNG_URL=%XSEARCH_URL%"
  echo where docker ^>nul 2^>^&1
  echo if %%errorlevel%% equ 0 powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\xsearch_up.ps1" ^>nul 2^>^&1
  echo echo Starting NULLA ^(API + mesh daemon^)...
  echo echo OpenClaw connects to http://127.0.0.1:11435
  echo echo.
  echo "%VENV_DIR%\Scripts\python.exe" -m apps.nulla_api_server
) > "%PROJECT_ROOT%\Start_NULLA.bat"
(
  echo @echo off
  echo set "NULLA_HOME=%NULLA_HOME%"
  echo set "NULLA_INSTALL_PROFILE=%INSTALL_PROFILE%"
  echo if "%%NULLA_OLLAMA_MODEL%%"=="" set "NULLA_OLLAMA_MODEL=%MODEL_TAG%"
  echo if "%%OLLAMA_MODELS%%"=="" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if not "%%OLLAMA_MODELS%%"=="" if not exist "%%OLLAMA_MODELS%%" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if "%%OLLAMA_API_KEY%%"=="" set "OLLAMA_API_KEY=ollama-local"
  echo set "PLAYWRIGHT_ENABLED=1"
  echo set "ALLOW_BROWSER_FALLBACK=1"
  echo set "BROWSER_ENGINE=%DEFAULT_BROWSER_ENGINE%"
  echo set "WEB_SEARCH_PROVIDER_ORDER=%WEB_PROVIDER_ORDER%"
  echo if "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%"=="" set "NULLA_PUBLIC_HIVE_WATCH_HOST=%PUBLIC_HIVE_WATCH_HOST%"
  echo "%VENV_DIR%\Scripts\python.exe" -m ops.ensure_public_hive_auth --project-root "%PROJECT_ROOT%" --watch-host "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%" ^>nul 2^>^&1
  echo if "%%SEARXNG_URL%%"=="" set "SEARXNG_URL=%XSEARCH_URL%"
  echo where docker ^>nul 2^>^&1
  echo if %%errorlevel%% equ 0 powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\xsearch_up.ps1" ^>nul 2^>^&1
  echo "%VENV_DIR%\Scripts\python.exe" -m apps.nulla_chat --platform openclaw --device openclaw
) > "%PROJECT_ROOT%\Talk_To_NULLA.bat"
(
  echo @echo off
  echo setlocal enabledelayedexpansion
  echo set "NULLA_HOME=%NULLA_HOME%"
  echo set "NULLA_INSTALL_PROFILE=%INSTALL_PROFILE%"
  echo set "MODEL_TAG=%MODEL_TAG%"
  echo if "%%NULLA_OLLAMA_MODEL%%"=="" set "NULLA_OLLAMA_MODEL=%%MODEL_TAG%%"
  echo if "%%OLLAMA_MODELS%%"=="" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if not "%%OLLAMA_MODELS%%"=="" if not exist "%%OLLAMA_MODELS%%" if exist "%%~d0\Ollama\models" set "OLLAMA_MODELS=%%~d0\Ollama\models"
  echo if "%%OLLAMA_API_KEY%%"=="" set "OLLAMA_API_KEY=ollama-local"
  echo set "PLAYWRIGHT_ENABLED=1"
  echo set "ALLOW_BROWSER_FALLBACK=1"
  echo set "BROWSER_ENGINE=%DEFAULT_BROWSER_ENGINE%"
  echo set "WEB_SEARCH_PROVIDER_ORDER=%WEB_PROVIDER_ORDER%"
  echo if "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%"=="" set "NULLA_PUBLIC_HIVE_WATCH_HOST=%PUBLIC_HIVE_WATCH_HOST%"
  echo "%VENV_DIR%\Scripts\python.exe" -m ops.ensure_public_hive_auth --project-root "%PROJECT_ROOT%" --watch-host "%%NULLA_PUBLIC_HIVE_WATCH_HOST%%" ^>nul 2^>^&1
  echo if "%%SEARXNG_URL%%"=="" set "SEARXNG_URL=%XSEARCH_URL%"
  echo where docker ^>nul 2^>^&1
  echo if %%errorlevel%% equ 0 powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\xsearch_up.ps1" ^>nul 2^>^&1
  echo.
  echo REM Check if NULLA API is already running
  echo powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo if %%errorlevel%% equ 0 goto open_openclaw
  echo.
  echo echo Starting NULLA...
  echo start "" /B "%VENV_DIR%\Scripts\python.exe" -m apps.nulla_api_server
  echo set "READY=0"
  echo for /L %%%%i in ^(1,1,30^) do ^(
  echo   if ^^!READY^^! equ 0 ^(
  echo     timeout /t 1 /nobreak ^>nul
  echo     powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo     if ^^!errorlevel^^! equ 0 set "READY=1"
  echo   ^)
  echo ^)
  echo.
  echo powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo if %%errorlevel%% neq 0 ^(
  echo   set "OPENCLAW_CMD="
  echo   for /f "tokens=*" %%%%C in ^('where openclaw 2^^^>nul'^) do if "^^!OPENCLAW_CMD^^!"=="" set "OPENCLAW_CMD=%%%%C"
  echo   if not "^^!OPENCLAW_CMD^^!"=="" ^(
  echo     start "" /B "^^!OPENCLAW_CMD^^!" gateway run --force --port 18789
  echo     for /L %%%%j in ^(1,1,30^) do ^(
  echo       timeout /t 1 /nobreak ^>nul
  echo       powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo       if ^^!errorlevel^^! equ 0 goto open_openclaw
  echo     ^)
  echo   ^)
  echo ^)
  echo.
  echo powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo if %%errorlevel%% neq 0 ^(
  echo   set "OLLAMA_EXE="
  echo   where ollama ^>nul 2^>^&1 ^&^& set "OLLAMA_EXE=ollama"
  echo   if "^^!OLLAMA_EXE^^!"=="" if exist "%%~d0\Ollama\ollama.exe" set "OLLAMA_EXE=%%~d0\Ollama\ollama.exe"
  echo   if "^^!OLLAMA_EXE^^!"=="" if exist "%%LOCALAPPDATA%%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%%LOCALAPPDATA%%\Programs\Ollama\ollama.exe"
  echo   if "^^!OLLAMA_EXE^^!"=="" if exist "%%SystemDrive%%\Ollama\ollama.exe" set "OLLAMA_EXE=%%SystemDrive%%\Ollama\ollama.exe"
  echo   if not "^^!OLLAMA_EXE^^!"=="" ^(
  echo     start "" /B "^^!OLLAMA_EXE^^!" launch openclaw --yes --model "%%MODEL_TAG%%"
  echo     for /L %%%%j in ^(1,1,30^) do ^(
  echo       timeout /t 1 /nobreak ^>nul
  echo       powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" ^>nul 2^>^&1
  echo       if ^^!errorlevel^^! equ 0 goto open_openclaw
  echo     ^)
  echo   ^)
  echo ^)
  echo.
  echo :open_openclaw
  echo set "GW_TOKEN="
  echo set "TRACE_URL=http://127.0.0.1:11435/trace"
  echo for /f "tokens=*" %%%%A in ^('"%VENV_DIR%\Scripts\python.exe" -c "from core.openclaw_locator import load_gateway_token; print(load_gateway_token())" 2^>nul'^) do set "GW_TOKEN=%%%%A"
  echo if not "%%GW_TOKEN%%"=="" ^(
  echo   start "" "http://127.0.0.1:18789/#token=%%GW_TOKEN%%"
  echo ^) else ^(
  echo   start "" "http://127.0.0.1:18789"
  echo ^)
  echo start "" "%%TRACE_URL%%"
  echo echo NULLA is running. OpenClaw is open.
  echo echo NULLA trace rail: %%TRACE_URL%%
) > "%PROJECT_ROOT%\OpenClaw_NULLA.bat"

for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$desk=[Environment]::GetFolderPath('Desktop');$link=Join-Path $desk 'OpenClaw + NULLA.lnk';$w=New-Object -ComObject WScript.Shell;$s=$w.CreateShortcut($link);$s.TargetPath='%PROJECT_ROOT%\OpenClaw_NULLA.bat';$s.WorkingDirectory='%PROJECT_ROOT%';$s.IconLocation='%SystemRoot%\System32\shell32.dll,220';$s.Save();Write-Output $link"`) do set "DESKTOP_SHORTCUT=%%L"
if defined DESKTOP_SHORTCUT (
  echo Desktop shortcut created: %DESKTOP_SHORTCUT%
) else (
  echo WARNING: Could not create Desktop shortcut automatically.
)

set "OPENCLAW_ENABLED=1"
if /i "%OPENCLAW_MODE%"=="skip" set "OPENCLAW_ENABLED=0"
if "%AUTO_YES%"=="1" if /i "%OPENCLAW_MODE%"=="prompt" set "OPENCLAW_ENABLED=0"
if "%OPENCLAW_ENABLED%"=="0" goto skip_openclaw

if /i "%OPENCLAW_MODE%"=="prompt" (
  set /p "CREATE_OPENCLAW=Register NULLA in OpenClaw Agent tab? [Y/n]: "
  if /i "!CREATE_OPENCLAW!"=="n" set "OPENCLAW_ENABLED=0"
  if /i "!CREATE_OPENCLAW!"=="no" set "OPENCLAW_ENABLED=0"
)
if "%OPENCLAW_ENABLED%"=="0" goto skip_openclaw

set "OPENCLAW_AGENT_DIR="
if /i "%OPENCLAW_MODE%"=="path" set "OPENCLAW_AGENT_DIR=%OPENCLAW_PATH_OVERRIDE%"
if not "%OPENCLAW_AGENT_DIR%"=="" (
  mkdir "%OPENCLAW_AGENT_DIR%" >nul 2>&1
  copy /Y "%PROJECT_ROOT%\Start_NULLA.bat" "%OPENCLAW_AGENT_DIR%\Start_NULLA.bat" >nul 2>&1
  copy /Y "%PROJECT_ROOT%\Talk_To_NULLA.bat" "%OPENCLAW_AGENT_DIR%\Talk_To_NULLA.bat" >nul 2>&1
)

echo Step 8/14: Registering NULLA in OpenClaw...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%register_openclaw_agent.py" "%PROJECT_ROOT%" "%NULLA_HOME%" "%MODEL_TAG%" "%AGENT_NAME%"
if %errorlevel% neq 0 (
  echo WARNING: Could not register NULLA in OpenClaw config. You can register manually later.
)
goto done_openclaw

:skip_openclaw
echo Step 8/14: OpenClaw registration skipped.

:done_openclaw
echo Step 9/14: Setting up Ollama (local AI runtime)...

REM Resolve drive letter from installer location for model storage
set "INSTALL_DRIVE=%~d0"
if "%INSTALL_DRIVE%"=="" set "INSTALL_DRIVE=C:"
set "OLLAMA_MODELS_DIR="
"%VENV_DIR%\Scripts\python.exe" -c "from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan; models=[m.strip() for m in r'%RECOMMENDED_BUNDLE_MODELS%'.split(',') if m.strip()]; print(build_model_store_drive_plan(required_models=models, support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,))['recommended_model_store_path'])" 2>nul > "%TEMP%\nulla_ollama_models_dir.txt"
if exist "%TEMP%\nulla_ollama_models_dir.txt" (
  set /p OLLAMA_MODELS_DIR=<"%TEMP%\nulla_ollama_models_dir.txt"
  del /f /q "%TEMP%\nulla_ollama_models_dir.txt" >nul 2>&1
)
if "%OLLAMA_MODELS_DIR%"=="" set "OLLAMA_MODELS_DIR=%INSTALL_DRIVE%\Ollama\models"
for %%I in ("%OLLAMA_MODELS_DIR%\..") do set "OLLAMA_INSTALL_DIR=%%~fI"
echo Recommended Ollama model store: %OLLAMA_MODELS_DIR%

REM Set permanent env vars so models never land on C: unexpectedly
echo Setting OLLAMA_MODELS=%OLLAMA_MODELS_DIR% (permanent)...
setx OLLAMA_MODELS "%OLLAMA_MODELS_DIR%" >nul 2>&1
set "OLLAMA_MODELS=%OLLAMA_MODELS_DIR%"
echo Setting OLLAMA_API_KEY=ollama-local (permanent)...
setx OLLAMA_API_KEY "ollama-local" >nul 2>&1
set "OLLAMA_API_KEY=ollama-local"

REM Check if Ollama is already installed
set "OLLAMA_EXE="
where ollama >nul 2>&1 && set "OLLAMA_EXE=ollama"
if "%OLLAMA_EXE%"=="" if exist "%OLLAMA_INSTALL_DIR%\ollama.exe" set "OLLAMA_EXE=%OLLAMA_INSTALL_DIR%\ollama.exe"
if "%OLLAMA_EXE%"=="" if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if "%OLLAMA_EXE%"=="" (
  echo Ollama not found. Downloading installer...
  set "OLLAMA_SETUP=%TEMP%\OllamaSetup.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%TEMP%\OllamaSetup.exe' -UseBasicParsing"
  if not exist "%TEMP%\OllamaSetup.exe" (
    echo ERROR: Failed to download Ollama. Check your internet connection.
    echo You can install Ollama manually from https://ollama.com/download
    goto skip_ollama_model
  )
  echo Installing Ollama...
  start /wait "" "%TEMP%\OllamaSetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%TEMP%\ollama_install.log"
  timeout /t 5 /nobreak >nul
  REM Find the exe after install
  if exist "%OLLAMA_INSTALL_DIR%\ollama.exe" (
    set "OLLAMA_EXE=%OLLAMA_INSTALL_DIR%\ollama.exe"
  ) else if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  )
  del /f /q "%TEMP%\OllamaSetup.exe" >nul 2>&1
  del /f /q "%TEMP%\ollama_install.log" >nul 2>&1
)

if "%OLLAMA_EXE%"=="" (
  echo WARNING: Ollama installation could not be verified. Model pull skipped.
  goto skip_ollama_model
)

echo Step 10/14: Starting Ollama server...
REM Check if Ollama is already serving
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:11434' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 (
  start "" /B "%OLLAMA_EXE%" serve
  timeout /t 5 /nobreak >nul
)

if "%OPENCLAW_ENABLED%"=="1" (
  echo Step 11/14: Configuring OpenClaw for NULLA...
  where openclaw >nul 2>&1
  if %errorlevel% neq 0 (
    echo OpenClaw CLI not found on PATH. Trying Ollama OpenClaw bootstrap...
    "%OLLAMA_EXE%" launch openclaw --yes --config --model "%MODEL_TAG%" >nul 2>&1
    if %errorlevel% neq 0 (
      echo WARNING: OpenClaw auto-config via Ollama failed. NULLA registration will still be written locally.
    )
  )
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%register_openclaw_agent.py" "%PROJECT_ROOT%" "%NULLA_HOME%" "%MODEL_TAG%" "%AGENT_NAME%" >nul 2>&1
)

echo Step 12/14: Pulling AI model (this may take a while)...

set "MODELS_TO_PULL=%RECOMMENDED_BUNDLE_MODELS%"
if "!MODELS_TO_PULL!"=="" set "MODELS_TO_PULL=%MODEL_TAG%"
for %%M in (!MODELS_TO_PULL:,= !) do (
  set "PULL_MODEL=%%~M"
  if not "!PULL_MODEL!"=="" (
    "%OLLAMA_EXE%" list 2>nul | findstr /i /c:"!PULL_MODEL!" >nul 2>&1
    if !errorlevel! neq 0 (
      echo Downloading !PULL_MODEL! to %OLLAMA_MODELS_DIR%...
      "%OLLAMA_EXE%" pull !PULL_MODEL!
      if !errorlevel! neq 0 (
        echo WARNING: Model pull failed. You can run this manually later:
        echo   set OLLAMA_MODELS=%OLLAMA_MODELS_DIR%
        echo   "%OLLAMA_EXE%" pull !PULL_MODEL!
      )
    ) else (
      echo Model !PULL_MODEL! already available.
    )
  )
)

if "%OPENCLAW_ENABLED%"=="1" (
  set "OPENCLAW_MEMORY_MODEL=nomic-embed-text"
  "%OLLAMA_EXE%" list 2>nul | findstr /i "!OPENCLAW_MEMORY_MODEL!" >nul 2>&1
  if !errorlevel! neq 0 (
    echo Downloading OpenClaw memory embedding model !OPENCLAW_MEMORY_MODEL! to %OLLAMA_MODELS_DIR%...
    "%OLLAMA_EXE%" pull !OPENCLAW_MEMORY_MODEL!
    if !errorlevel! neq 0 (
      echo WARNING: OpenClaw memory embedding model pull failed. You can run this manually later:
      echo   set OLLAMA_MODELS=%OLLAMA_MODELS_DIR%
      echo   "%OLLAMA_EXE%" pull !OPENCLAW_MEMORY_MODEL!
    )
  ) else (
    echo OpenClaw memory embedding model !OPENCLAW_MEMORY_MODEL! already available.
  )
)

:skip_ollama_model

echo Step 13/14: Registering NULLA as startup task...
REM Create a VBS wrapper for silent background launch (no console window)
set "VBS_PATH=%PROJECT_ROOT%\nulla_background.vbs"
(
  echo Set WshShell = CreateObject^("WScript.Shell"^)
  echo WshShell.Run "cmd /c ""%PROJECT_ROOT%\Start_NULLA.bat""", 0, False
) > "%VBS_PATH%"
REM Register with Task Scheduler (runs at logon, no admin required)
schtasks /create /tn "NULLA_Daemon" /tr "wscript.exe \"%VBS_PATH%\"" /sc onlogon /rl limited /f >nul 2>&1
if %errorlevel% equ 0 (
  echo NULLA registered as startup task.
) else (
  echo WARNING: Could not register startup task. You can start NULLA manually.
)

echo Step 14/14: Configuring Liquefy...
"%VENV_DIR%\Scripts\python.exe" -c "import json; from pathlib import Path; d=Path.home()/'.liquefy'; d.mkdir(parents=True,exist_ok=True); p=d/'config.json'; c={'enabled':True,'version':'1.1.0','mode':'auto','vault_dir':str(d/'vault'),'profile':'default','policy_mode':'strict','verify_mode':'full','encrypt':False,'leak_scan':True}; p.write_text(json.dumps(c,indent=2),encoding='utf-8'); print('Liquefy config written to '+str(p))" 2>nul
if %errorlevel% neq 0 echo WARNING: Could not configure Liquefy.

echo Writing install receipt...
set "OPENCLAW_CONFIG_PATH_RESOLVED="
set "OPENCLAW_AGENT_DIR_RESOLVED="
if "%OPENCLAW_ENABLED%"=="1" (
  for /f "tokens=*" %%A in ('"%VENV_DIR%\Scripts\python.exe" -c "from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).config_path)" 2^>nul') do set "OPENCLAW_CONFIG_PATH_RESOLVED=%%A"
  for /f "tokens=*" %%A in ('"%VENV_DIR%\Scripts\python.exe" -c "from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).compat_bridge_dir)" 2^>nul') do set "OPENCLAW_AGENT_DIR_RESOLVED=%%A"
  if not "%OPENCLAW_AGENT_DIR%"=="" set "OPENCLAW_AGENT_DIR_RESOLVED=%OPENCLAW_AGENT_DIR%"
)
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%write_install_receipt.py" "%PROJECT_ROOT%" "%NULLA_HOME%" "%MODEL_TAG%" "%OPENCLAW_ENABLED%" "%OPENCLAW_CONFIG_PATH_RESOLVED%" "%OPENCLAW_AGENT_DIR_RESOLVED%" "%OLLAMA_EXE%" >nul 2>&1
if %errorlevel% neq 0 echo WARNING: Could not write install receipt.
echo Running NULLA doctor...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%doctor.py" "%PROJECT_ROOT%" "%NULLA_HOME%" "%MODEL_TAG%" "%OPENCLAW_ENABLED%" "%OPENCLAW_CONFIG_PATH_RESOLVED%" "%OPENCLAW_AGENT_DIR_RESOLVED%" "%OLLAMA_EXE%" >nul 2>&1
if %errorlevel% neq 0 (
  echo WARNING: Could not generate doctor report.
) else (
  echo Doctor report written to %PROJECT_ROOT%\install_doctor.json
)

echo.
echo Install complete.
echo.
echo ===============================================
echo NULLA is installed. It IS your OpenClaw now.
echo ===============================================
echo.
echo NULLA starts automatically at login. No manual steps.
echo.
echo Visible agent name: %AGENT_NAME%
echo Selected model: %MODEL_TAG%
echo To open now:  %PROJECT_ROOT%\OpenClaw_NULLA.bat
if defined DESKTOP_SHORTCUT echo Desktop:      %DESKTOP_SHORTCUT%
echo.
echo NULLA is the default agent, memory is automatic,
echo mesh daemon is live, starter credits are seeded, and credits are tracked.
echo Install-profile truth is persisted into launchers and support receipts.
echo Playwright browser rendering is enabled through install launchers.
echo Local SearXNG bootstrap is attempted on install and on launcher start.
echo Decentralized AI. Your machine, your node.

if "%AUTO_START%"=="1" (
  echo Launching NULLA now...
  call "%PROJECT_ROOT%\OpenClaw_NULLA.bat"
)

exit /b 0

:validate_install_profile
set "PROFILE_TO_VALIDATE=%~1"
if "%PROFILE_TO_VALIDATE%"=="" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="auto-recommended" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="ollama-only" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="local-only" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="ollama-max" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="local-max" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="ollama+kimi" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="hybrid-kimi" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="ollama+tether" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="hybrid-tether" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="hybrid-fallback" exit /b 0
if /i "%PROFILE_TO_VALIDATE%"=="full-orchestrated" exit /b 0
echo ERROR: /INSTALLPROFILE must be auto-recommended, local-only, or local-max.
exit /b 1
