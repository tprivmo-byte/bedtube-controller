@echo off
cd /d "%~dp0"
if exist "%~dp0BedTube.exe" (
    start "" "%~dp0BedTube.exe" --show-qr
) else (
    start "" pyw "%~dp0bedtube.pyw" --show-qr
)
