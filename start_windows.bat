@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Once install_windows.bat dosyasini calistirin.
  pause
  exit /b 1
)
.venv\Scripts\python.exe app.py
if errorlevel 1 (
  echo Uygulama baslatilamadi. README.md dosyasindaki sorun giderme notlarini inceleyin.
  pause
)
