@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher bulunamadi. Python 3.10+ kurup tekrar deneyin.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo.
echo Kurulum tamamlandi. start_windows.bat dosyasini calistirin.
pause
exit /b 0
:failed
echo Kurulum basarisiz oldu. Python ve internet baglantisini kontrol edin.
pause
exit /b 1
