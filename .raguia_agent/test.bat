@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Erreur: python est introuvable. Installez Python 3 puis relancez.
    exit /b 1
  )
  python -m venv venv || exit /b 1
)

call venv\Scripts\activate.bat
if errorlevel 1 (
  echo Erreur: impossible d'activer le venv.
  exit /b 1
)
set RAGUIA_AGENT_CONFIG=%~dps0raguia_agent.yaml
if not exist "%RAGUIA_AGENT_CONFIG%" (
  echo Erreur: configuration introuvable ^(%RAGUIA_AGENT_CONFIG%^). Lancez install.bat.
  exit /b 1
)

python -c "import raguia_local_agent" >nul 2>&1
if errorlevel 1 (
  python -m pip --version >nul 2>&1
  if errorlevel 1 python -m ensurepip --upgrade >nul 2>&1
  python -m pip install -e ".." || exit /b 1
)

python -m raguia_local_agent --test
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
