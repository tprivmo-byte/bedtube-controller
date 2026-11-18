@echo off
cd /d "%~dp0"
if exist "%~dp0BedTube.exe" (
    start "" "%~dp0BedTube.exe"
) else (
    start "" pyw "%~dp0bedtube.pyw"
)
