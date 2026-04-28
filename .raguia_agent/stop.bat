@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PID_FILE=%USERPROFILE%\.raguia\agent.pid"
set "STOPPED=0"

if exist "%PID_FILE%" (
  set /p AGENT_PID=<"%PID_FILE%"
  if not "!AGENT_PID!"=="" (
    taskkill /PID !AGENT_PID! /F >nul 2>&1
    if not errorlevel 1 set "STOPPED=1"
  )
  del /q "%PID_FILE%" >nul 2>&1
)

REM Fallback defensif : arrete uniquement les process dont la ligne de commande
REM contient raguia_local_agent (et pas tous les python.exe).
for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match ''raguia_local_agent'' }; $procs | ForEach-Object { $_.ProcessId }"') do (
  taskkill /PID %%P /F >nul 2>&1
  if not errorlevel 1 set "STOPPED=1"
)

if "!STOPPED!"=="1" (
  echo Agent arrete
) else (
  echo Aucun processus agent detecte
)
endlocal
