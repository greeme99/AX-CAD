# AX-CAD restore: put a database dump (and optionally the matching var-*.zip) back.
param([Parameter(Mandatory = $true)][string]$Dump, [string]$VarZip)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
Set-AxEnv
Stop-App
Start-Db
Restore-Db (Resolve-Path $Dump).Path
if ($VarZip) { Invoke-Native "$env:SystemRoot\System32\tar.exe" @('-x', '-f', (Resolve-Path $VarZip).Path, '-C', $Data) }
Write-Host '복구 완료. AX-CAD를 다시 시작하세요.'
