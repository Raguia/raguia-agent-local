@echo off
setlocal EnableDelayedExpansion

REM Modes :
REM   Nouveau : install.bat prod|local <slug-client> [TOKEN] [WATCH_PARENT]
REM   Ancien  : install.bat https://... [TOKEN] [WATCH_PARENT] [prod|local]
REM Env : RAGUIA_INSTALL_ENV, RAGUIA_PORTAL_ORIGIN_PROD, RAGUIA_LOCAL_API_BASE

echo === Installation Agent RAGUIA ===

set "LEGACY_MODE=0"
set "A1=%~1"
if "%A1:~0,8%"=="https://" set "LEGACY_MODE=1"
if "%A1:~0,7%"=="http://" set "LEGACY_MODE=1"

if defined RAGUIA_PORTAL_ORIGIN_PROD (
  set "DEFAULT_API_PROD=%RAGUIA_PORTAL_ORIGIN_PROD%"
) else (
  set "DEFAULT_API_PROD=https://raguia.valentin-fiess.fr"
)
if defined RAGUIA_LOCAL_API_BASE (
  set "DEFAULT_API_LOCAL=%RAGUIA_LOCAL_API_BASE%"
) else (
  set "DEFAULT_API_LOCAL=http://localhost:5173"
)

set "API_BASE="
set "TOKEN="
set "WATCH_PARENT="
set "RUNTIME_ENV="
set "CLIENT_SLUG="

if "!LEGACY_MODE!"=="1" (
  set "API_BASE=%~1"
  set "TOKEN=%~2"
  set "WATCH_PARENT=%~3"
  set "RUNTIME_ENV=%~4"
  if "!RUNTIME_ENV!"=="" set "RUNTIME_ENV=prod"
  if /I not "!RUNTIME_ENV!"=="local" if /I not "!RUNTIME_ENV!"=="prod" set "RUNTIME_ENV=prod"
  goto parsed
)

if /I "%~1"=="prod" goto newmode
if /I "%~1"=="local" goto newmode
if "%~1"=="" goto interactive_new
echo Premier argument invalide.
echo   Nouveau : %~nx0 prod^|local ^<slug-client^> [TOKEN] [WATCH_PARENT]
echo   Ancien  : %~nx0 https://origin-api [TOKEN] [WATCH_PARENT] [prod^|local]
exit /b 1

:newmode
set "RUNTIME_ENV=%~1"
set "CLIENT_SLUG=%~2"
set "TOKEN=%~3"
set "WATCH_PARENT=%~4"
goto after_parse

:interactive_new
if defined RAGUIA_INSTALL_ENV (
  set "DEFMODE=%RAGUIA_INSTALL_ENV%"
) else (
  set "DEFMODE=prod"
)
set /p RUNTIME_ENV=Mode [prod / local] (defaut: !DEFMODE!): 
if "!RUNTIME_ENV!"=="" set "RUNTIME_ENV=!DEFMODE!"
if /I not "!RUNTIME_ENV!"=="local" set "RUNTIME_ENV=prod"
set /p CLIENT_SLUG=Slug portail / identifiant client (ex: client-acme): 

:after_parse
if /I "!RUNTIME_ENV!"=="local" (
  set "DEFAULT_API_BASE=!DEFAULT_API_LOCAL!"
) else (
  set "RUNTIME_ENV=prod"
  set "DEFAULT_API_BASE=!DEFAULT_API_PROD!"
)

if "!LEGACY_MODE!"=="0" (
  if "!CLIENT_SLUG!"=="" set /p CLIENT_SLUG=Slug portail / identifiant client (ex: client-acme): 
  if "!CLIENT_SLUG!"=="" (
    echo Le slug client est obligatoire.
    exit /b 1
  )
  set "API_BASE=!DEFAULT_API_BASE!"
)

:parsed
if "!API_BASE!"=="" (
  set /p API_BASE=URL API — api_base (defaut: !DEFAULT_API_BASE!): 
)
if "!API_BASE!"=="" set "API_BASE=!DEFAULT_API_BASE!"

if "!TOKEN!"=="" set /p TOKEN=Jeton JWT agent: 
if "!API_BASE!"=="" goto usage
if "!TOKEN!"=="" goto usage
goto okargs

:usage
echo api_base et jeton sont obligatoires.
exit /b 1

:okargs

if "!WATCH_PARENT!"=="" (
  set /p WATCH_PARENT=Dossier parent (defaut: %USERPROFILE%\Documents): 
)
if "!WATCH_PARENT!"=="" set "WATCH_PARENT=%USERPROFILE%\Documents"

set "SCRIPT_DIR=%~dp0"
set "AGENT_DIR=%SCRIPT_DIR%.raguia_agent"

set "PORTAL_HINT="
if not "!CLIENT_SLUG!"=="" set "PORTAL_HINT=!API_BASE!/portal/!CLIENT_SLUG!"

echo.
echo 1. Installation de 'uv' et Python...
where git >nul 2>&1
if errorlevel 1 (
  echo git absent: tentative d'installation automatique...
  where winget >nul 2>&1
  if not errorlevel 1 (
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
  ) else (
    where choco >nul 2>&1
    if not errorlevel 1 (
      choco install git -y
    )
  )
)
where git >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] git introuvable apres tentative automatique.
  echo Installez git: https://git-scm.com/download/win
  exit /b 1
)
where uv >nul 2>&1
if errorlevel 1 (
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
)
where uv >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] uv introuvable apres installation.
  exit /b 1
)
call uv python install 3.11
if errorlevel 1 (
  echo [ERREUR] Impossible d'installer Python 3.11 via uv.
  exit /b 1
)

echo.
echo 2. Creation de la configuration...
if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
set "CFG=%AGENT_DIR%\raguia_agent.yaml"
> "!CFG!" echo api_base: "!API_BASE!"
if not "!CLIENT_SLUG!"=="" >> "!CFG!" echo client_slug: "!CLIENT_SLUG!"
>> "!CFG!" echo agent_token: "!TOKEN!"
>> "!CFG!" echo watch_parent: "!WATCH_PARENT!"
>> "!CFG!" echo root_folder_name: "RAGUIA"
>> "!CFG!" echo runtime_env: "!RUNTIME_ENV!"

echo.
echo 3. Installation des dependances...
cd /d "%SCRIPT_DIR%"
call uv venv "%AGENT_DIR%\venv" --python 3.11
if errorlevel 1 (
  echo [ERREUR] Creation du venv impossible.
  exit /b 1
)
call "%AGENT_DIR%\venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [ERREUR] Activation du venv impossible.
  exit /b 1
)
call uv pip install -e ".[tray]"
if errorlevel 1 (
  echo [ERREUR] Installation des dependances impossible.
  exit /b 1
)

echo.
echo 4. Test de connexion...
python -c "import httpx, yaml, sys; cfg = yaml.safe_load(open(r'%AGENT_DIR%\raguia_agent.yaml')); r = httpx.get(cfg['api_base'] + '/api/portal/agent/sync-status', headers={'Authorization': f'Bearer {cfg[\"agent_token\"]}'}, timeout=10.0); sys.exit(1) if r.status_code != 200 else None; d = r.json(); sys.exit(0 if isinstance(d, dict) else 1)"
if errorlevel 1 (
  echo   [ERREUR] Connexion echouee
) else (
  echo   Connexion reussie!
)

echo.
echo 5. Raccourci Demarrage Windows...
set "RAGUIA_START_BAT=%AGENT_DIR%\start.bat"
set "RAGUIA_AGENT_DIR=%AGENT_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Raguia Agent.lnk'; ^
   $sc = $ws.CreateShortcut($startup); ^
   $sc.TargetPath = $env:RAGUIA_START_BAT; ^
   $sc.WorkingDirectory = $env:RAGUIA_AGENT_DIR.TrimEnd('\'); ^
   $sc.Save(); ^
   Write-Host ('Raccourci cree : ' + $startup)"

if not exist "%WATCH_PARENT%\RAGUIA" mkdir "%WATCH_PARENT%\RAGUIA"
echo.
echo === Installation terminee! ===
echo api_base ^(API agent^^) : !API_BASE!
if not "!PORTAL_HINT!"=="" echo Portail ^(navigateur^) : !PORTAL_HINT!
echo Dossier synchronise : %WATCH_PARENT%\RAGUIA
echo Scripts: %AGENT_DIR%\start.bat  test.bat  stop.bat
endlocal
