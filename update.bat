@echo off
setlocal EnableDelayedExpansion
REM MAJ depuis le clone git (racine du depot, meme niveau que install.bat)

cd /d "%~dp0"

echo === Mise a jour Agent Raguia (git pull + dependances) ===

if not exist ".git" (
  echo Pas de dossier .git ici. Refaites git clone puis install.bat
  exit /b 1
)

set "BRANCH=%RAGUIA_AGENT_BRANCH%"
if "!BRANCH!"=="" set "BRANCH=main"

echo git fetch / pull (!BRANCH!)...
git fetch origin !BRANCH!
git pull --ff-only origin !BRANCH!
if errorlevel 1 (
  echo Essai git pull sans ff-only...
  git pull origin !BRANCH!
)

set "AGENT_DIR=%~dp0.raguia_agent"
if not exist "!AGENT_DIR!\venv\Scripts\python.exe" (
  echo venv introuvable — lancez install.bat une fois.
  exit /b 1
)

set "VENV_PY=!AGENT_DIR!\venv\Scripts\python.exe"

where uv >nul 2>&1
if not errorlevel 1 (
  uv pip install -e ".[tray]" --python "!VENV_PY!"
  if errorlevel 1 (
    echo uv pip a echoue — ensurepip + pip dans le venv...
    "!VENV_PY!" -m ensurepip --upgrade
    "!VENV_PY!" -m pip install -e ".[tray]"
  )
) else (
  "!VENV_PY!" -m ensurepip --upgrade
  "!VENV_PY!" -m pip install -e ".[tray]"
)

echo.
echo Termine. Redemarrez l'agent : icone Raguia ^> Quitter puis relancer start.bat
endlocal
