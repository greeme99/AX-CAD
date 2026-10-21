# AX-CAD backup: database dump + uploaded files into data\backup (newest 10 kept).
# Copy data\backup to another disk or a USB drive regularly: this PC holds the only copy.
param([switch]$NoPause)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
Set-AxEnv
$wasRunning = Test-Db
Start-Db
try { $dump = New-Backup } finally { if (-not $wasRunning) { Stop-Db } }
Write-Host "백업 완료: $dump"
if (-not $NoPause) { Read-Host 'Enter 키를 누르면 닫힙니다' | Out-Null }
