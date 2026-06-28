# Deploy Q-Remote V3 to HamPi and restart service
# Usage: .\scripts\deploy-pi.ps1
# Requires: SSH key or interactive password for elsen@192.168.1.117

$ErrorActionPreference = "Stop"
$Pi = "elsen@192.168.1.117"
$Remote = "/home/elsen/q-remote-v3"
$Base = Split-Path $PSScriptRoot -Parent

Write-Host "Deploying from $Base to ${Pi}:${Remote} ..."

scp -o StrictHostKeyChecking=no `
    "$Base\backend\radio\rssi.py" `
    "$Base\backend\radio\connection.py" `
    "${Pi}:${Remote}/backend/radio/"

scp -o StrictHostKeyChecking=no `
    "$Base\backend\audio\rx_pipeline.py" `
    "${Pi}:${Remote}/backend/audio/"

scp -o StrictHostKeyChecking=no `
    "$Base\backend\control\socketio_server.py" `
    "${Pi}:${Remote}/backend/control/"

scp -o StrictHostKeyChecking=no `
    "$Base\backend\app.py" `
    "${Pi}:${Remote}/backend/"

scp -o StrictHostKeyChecking=no `
    "$Base\config.yaml" `
    "${Pi}:${Remote}/"

scp -o StrictHostKeyChecking=no `
    "$Base\frontend\templates\admin.html" `
    "${Pi}:${Remote}/frontend/templates/"

ssh -o StrictHostKeyChecking=no $Pi @"
systemctl restart q-remote-v3.service 2>/dev/null || systemctl restart q-remote.service
sleep 2
systemctl is-active q-remote-v3.service 2>/dev/null || systemctl is-active q-remote.service
journalctl -u q-remote-v3.service -n 12 --no-pager 2>/dev/null || journalctl -u q-remote.service -n 12 --no-pager
"@

Write-Host "Deploy complete. Hard-refresh browser (Ctrl+F5)."
