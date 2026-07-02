@echo off
setlocal enabledelayedexpansion
REM Open the NULLA .null (Web0) browser. A normal browser cannot open a .null site
REM directly (it lives on Arweave, behind the resolver), so this points at the page
REM NULLA serves at http://127.0.0.1:11435/web0 - the always-on API server renders it
REM and the page resolves .null names through POST /api/null on that same origin.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
set "WEB0_URL=http://127.0.0.1:11435/web0"

REM Is NULLA already healthy? If so, just open the browser.
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto open

echo Starting NULLA...
set "LAUNCH_REQUESTED=0"
schtasks /query /tn "NULLA_Daemon" >nul 2>&1
if !errorlevel! equ 0 (
  schtasks /run /tn "NULLA_Daemon" >nul 2>&1
  if !errorlevel! equ 0 set "LAUNCH_REQUESTED=1"
)
if "!LAUNCH_REQUESTED!"=="0" (
  if exist "%SCRIPT_ROOT%\nulla_background.vbs" (
    "%SystemRoot%\System32\wscript.exe" "%SCRIPT_ROOT%\nulla_background.vbs"
  ) else (
    echo ERROR: NULLA is not installed yet. Run installer\install_nulla.bat first.
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
  exit /b 1
)

:open
start "" "%WEB0_URL%"
echo Web0 browser opened at %WEB0_URL%
endlocal
