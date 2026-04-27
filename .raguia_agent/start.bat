@echo off
cd /d "%~dp0"
for %%I in ("%~dp0..") do set "RAGUIA_AGENT_REPO=%%~fI"
call venv\Scripts\activate.bat
set RAGUIA_AGENT_CONFIG=%~dps0raguia_agent.yaml
python -m raguia_local_agent
