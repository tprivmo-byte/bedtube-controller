@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permissao de Administrador para registrar regra no Firewall...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -Command ^
  "Remove-NetFirewallRule -DisplayName 'BedTube Controller (TCP 5050)' -ErrorAction SilentlyContinue; " ^
  "New-NetFirewallRule -DisplayName 'BedTube Controller (TCP 5050)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5050 -Profile Private,Public -RemoteAddress LocalSubnet | Out-Null; " ^
  "Write-Host 'Regra de Firewall do BedTube (Porta 5050 na Rede Local) criada com sucesso!' -ForegroundColor Green"
pause
