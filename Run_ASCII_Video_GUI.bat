@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python ascii_video_gui.py
  goto :eof
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 ascii_video_gui.py
  goto :eof
)

echo Python was not found on PATH.
echo Install Python 3, then run:
echo   python -m pip install -r requirements.txt
pause
