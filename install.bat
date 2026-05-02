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
REM Invites entre guillemets : sinon "/" "(" ")" dans le libelle cassent cmd ("set etait inattendu").
set /p "RUNTIME_ENV=Mode prod ou local - defaut !DEFMODE! Entree pour garder : "
if "!RUNTIME_ENV!"=="" set "RUNTIME_ENV=!DEFMODE!"
if /I not "!RUNTIME_ENV!"=="local" set "RUNTIME_ENV=prod"
set /p "CLIENT_SLUG=Slug portail identifiant client ex. client-acme : "

:after_parse
if /I "!RUNTIME_ENV!"=="local" (
  set "DEFAULT_API_BASE=!DEFAULT_API_LOCAL!"
) else (
  set "RUNTIME_ENV=prod"
  set "DEFAULT_API_BASE=!DEFAULT_API_PROD!"
)

if "!LEGACY_MODE!"=="0" (
  if "!CLIENT_SLUG!"=="" set /p "CLIENT_SLUG=Slug portail identifiant client : "
  if "!CLIENT_SLUG!"=="" (
    echo Le slug client est obligatoire.
    exit /b 1
  )
  set "API_BASE=!DEFAULT_API_BASE!"
)

:parsed
if "!API_BASE!"=="" (
  set /p "API_BASE=URL API api_base defaut !DEFAULT_API_BASE! Entree pour garder : "
)
if "!API_BASE!"=="" set "API_BASE=!DEFAULT_API_BASE!"

if "!TOKEN!"=="" set /p "TOKEN=Jeton JWT agent : "
if "!API_BASE!"=="" goto usage
if "!TOKEN!"=="" goto usage
goto okargs

:usage
echo api_base et jeton sont obligatoires.
exit /b 1

:okargs

if "!WATCH_PARENT!"=="" (
  echo Defaut si vide: %USERPROFILE%\Documents
  set /p "WATCH_PARENT=Dossier parent ou Entree pour ce defaut : "
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
  REM -NoProfile : evite Get-ExecutionPolicy et modules casse (CI, certains PC).
  REM uv peut finir dans .local\bin ou .cargo\bin selon la version du script.
  where pwsh >nul 2>&1
  if not errorlevel 1 (
    pwsh -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  )
  set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
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
REM Sans f-string ni \" dans -c (cmd casse la ligne ; secrets peuvent etre mal masques en CI).
if defined RAGUIA_SKIP_CONNECTION_TEST (
  echo   Test HTTP ignore ^(RAGUIA_SKIP_CONNECTION_TEST defini^).
  goto after_conn_test
)
python -c "import httpx,yaml,sys;cfg=yaml.safe_load(open(r'%AGENT_DIR%\raguia_agent.yaml'));t=str(cfg['agent_token']);u=str(cfg['api_base']).rstrip('/');r=httpx.get(u+'/api/portal/agent/sync-status',headers={'Authorization':'Bearer '+t},timeout=10.0);raise SystemExit(0 if r.status_code==200 and isinstance(r.json(),dict) else 1)"
if errorlevel 1 (
  echo   [ERREUR] Connexion echouee
) else (
  echo   Connexion reussie!
)
:after_conn_test

echo.
echo 5. Scripts de controle...
echo @echo off > "%AGENT_DIR%\start.bat"
echo setlocal >> "%AGENT_DIR%\start.bat"
echo cd /d "%%~dp0" >> "%AGENT_DIR%\start.bat"
echo for %%%%I in ^("%%~dp0.."^) do set "RAGUIA_AGENT_REPO=%%%%~fI" >> "%AGENT_DIR%\start.bat"
echo set "RAGUIA_AGENT_CONFIG=%%~dp0raguia_agent.yaml" >> "%AGENT_DIR%\start.bat"
echo if not exist "%%RAGUIA_AGENT_CONFIG%%" ^(echo Erreur: configuration introuvable ^(%%RAGUIA_AGENT_CONFIG%%^). Lancez install.bat. ^& exit /b 1^) >> "%AGENT_DIR%\start.bat"
echo if not exist "venv\Scripts\python.exe" ^(echo Erreur: interprete venv introuvable. Relancez install.bat. ^& exit /b 1^) >> "%AGENT_DIR%\start.bat"
echo "venv\Scripts\python.exe" -m raguia_local_agent %%* >> "%AGENT_DIR%\start.bat"
echo set "EXIT_CODE=%%ERRORLEVEL%%" >> "%AGENT_DIR%\start.bat"
echo endlocal ^& exit /b %%EXIT_CODE%% >> "%AGENT_DIR%\start.bat"

echo @echo off > "%AGENT_DIR%\test.bat"
echo setlocal >> "%AGENT_DIR%\test.bat"
echo cd /d "%%~dp0" >> "%AGENT_DIR%\test.bat"
echo for %%%%I in ^("%%~dp0.."^) do set "RAGUIA_AGENT_REPO=%%%%~fI" >> "%AGENT_DIR%\test.bat"
echo set "RAGUIA_AGENT_CONFIG=%%~dp0raguia_agent.yaml" >> "%AGENT_DIR%\test.bat"
echo if not exist "%%RAGUIA_AGENT_CONFIG%%" ^(echo Erreur: configuration introuvable ^(%%RAGUIA_AGENT_CONFIG%%^). Lancez install.bat. ^& exit /b 1^) >> "%AGENT_DIR%\test.bat"
echo if not exist "venv\Scripts\python.exe" ^(echo Erreur: interprete venv introuvable. Relancez install.bat. ^& exit /b 1^) >> "%AGENT_DIR%\test.bat"
echo "venv\Scripts\python.exe" -m raguia_local_agent --test %%* >> "%AGENT_DIR%\test.bat"
echo set "EXIT_CODE=%%ERRORLEVEL%%" >> "%AGENT_DIR%\test.bat"
echo endlocal ^& exit /b %%EXIT_CODE%% >> "%AGENT_DIR%\test.bat"

echo @echo off > "%AGENT_DIR%\stop.bat"
echo if exist "%%USERPROFILE%%\.raguia\agent.pid" ( >> "%AGENT_DIR%\stop.bat"
echo     set /p PID=^<"%%USERPROFILE%%\.raguia\agent.pid" >> "%AGENT_DIR%\stop.bat"
echo     taskkill /PID %%PID%% /F 2^>nul >> "%AGENT_DIR%\stop.bat"
echo     del "%%USERPROFILE%%\.raguia\agent.pid" >> "%AGENT_DIR%\stop.bat"
echo ) else ( >> "%AGENT_DIR%\stop.bat"
echo     taskkill /F /IM python.exe /FI "WINDOWTITLE eq raguia_local_agent*" 2^>nul >> "%AGENT_DIR%\stop.bat"
echo ) >> "%AGENT_DIR%\stop.bat"

echo.
echo 6. Raccourci Demarrage Windows...
set "RAGUIA_START_BAT=%AGENT_DIR%\start.bat"
set "RAGUIA_AGENT_DIR=%AGENT_DIR%"
REM Une seule ligne : les ^ continuations cmd ne passent pas si install.bat est appele depuis pwsh.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell;$p=Join-Path ([Environment]::GetFolderPath('Startup')) 'Raguia Agent.lnk';$s=$ws.CreateShortcut($p);$s.TargetPath=$env:RAGUIA_START_BAT;$s.WorkingDirectory=$env:RAGUIA_AGENT_DIR.TrimEnd([char]92);$s.Save();Write-Host ('Raccourci cree : '+$p)"

if not exist "%WATCH_PARENT%\RAGUIA" mkdir "%WATCH_PARENT%\RAGUIA"
echo.
echo === Installation terminee! ===
echo api_base ^(API agent^^) : !API_BASE!
if not "!PORTAL_HINT!"=="" echo Portail ^(navigateur^) : !PORTAL_HINT!
echo Dossier synchronise : %WATCH_PARENT%\RAGUIA
echo Scripts: %AGENT_DIR%\start.bat  test.bat  stop.bat
endlocal
