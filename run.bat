@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b %errorlevel%
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m harvest_assistant
exit /b %errorlevel%
