@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo RefCut isn't set up yet - run setup.bat first.
  pause
  exit /b 1
)
start "" http://localhost:7860
venv\Scripts\python.exe app.py
pause
