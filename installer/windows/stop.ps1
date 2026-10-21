# AX-CAD stop: the web screen, the API and the database.
param([switch]$NoPause)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
Stop-App
Stop-Db
Write-Host 'AX-CAD를 종료했습니다.'
if (-not $NoPause) { Start-Sleep -Seconds 2 }
