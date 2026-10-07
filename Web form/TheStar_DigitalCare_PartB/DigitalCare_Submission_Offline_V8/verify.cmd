@echo off
cd /d "%~dp0"
if exist "%~dp0runtime\python\python.exe" (
  "%~dp0runtime\python\python.exe" -B "%~dp0verify_model.py"
) else (
  python -B "%~dp0verify_model.py"
)
pause
