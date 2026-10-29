@echo off
REM Builds "dist\PDF OCR Layer Tool.exe" (single-file, no console), matching v1.0.
REM Run from this folder on Windows with Python 3.12+ installed:
REM     py -m pip install -r requirements-dev.txt
REM     build.bat
REM The tessdata folder (OCR language data for image-region mode) is bundled
REM inside the .exe; nothing extra needs copying to the target machine.

py -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "PDF OCR Layer Tool" ^
    --add-data "tessdata;tessdata" ^
    main.py
if errorlevel 1 exit /b 1

copy /y "READ ME FIRST.txt" "dist\READ ME FIRST.txt" >nul
echo.
echo Built: dist\PDF OCR Layer Tool.exe
