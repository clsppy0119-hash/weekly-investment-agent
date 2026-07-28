@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py
) else (
  python app.py
)
if errorlevel 1 (
  echo.
  echo 無法啟動。請先安裝 Python 3，並在安裝時勾選 Add Python to PATH。
  pause
)
