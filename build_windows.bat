@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher bulunamadi. Build makinesine Python 3.12 x64 kurun.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto :failed
)
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
  echo Build icin Python 3.12 x64 gerekiyor.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
  echo Build icin 64-bit Python gerekiyor.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6,<7"
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto :failed
if exist "dist\TDAArchive" rmdir /s /q "dist\TDAArchive"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name TDAArchive --collect-all tkinterdnd2 --collect-all cryptography --hidden-import archive_format app.py
if errorlevel 1 goto :failed
copy /y "PORTABLE_README.txt" "dist\TDAArchive\README.txt" >nul
if errorlevel 1 goto :failed
if not exist "dist\TDAArchive\samples" mkdir "dist\TDAArchive\samples"
copy /y "samples\ornek.tda" "dist\TDAArchive\samples\ornek.tda" >nul
if errorlevel 1 goto :failed
if not exist "release" mkdir "release"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '.\dist\TDAArchive' -DestinationPath '.\release\TDAArchive-Win10-11-x64.zip' -Force"
if errorlevel 1 goto :failed
echo.
echo Portable paket hazir: release\TDAArchive-Win10-11-x64.zip
echo Build testlerini gecti. EXE imzali degildir; SmartScreen uyari gosterebilir.
certutil -hashfile "release\TDAArchive-Win10-11-x64.zip" SHA256
pause
exit /b 0
:failed
echo Build basarisiz oldu. Python 3.12 x64, internet ve README.md adimlarini kontrol edin.
pause
exit /b 1
