# AX-CAD restore: put a database dump (and optionally the matching var-*.zip) back.
param([Parameter(Mandatory = $true)][string]$Dump, [string]$VarZip)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
Set-AxEnv
Stop-App
Start-Db
Restore-Db (Resolve-Path $Dump).Path
if ($VarZip) {
    # only what New-Backup puts in: a crafted zip must not replace axcad.env or pg\ settings
    $zip = (Resolve-Path $VarZip).Path
    $tar = "$env:SystemRoot\System32\tar.exe"
    $top = & $tar -t -f $zip | ForEach-Object { ($_ -split '/')[0] } | Sort-Object -Unique
    $members = @('var', 'supplier.json') | Where-Object { $top -contains $_ }
    if ($members) { Invoke-Native $tar (@('-x', '-f', $zip, '-C', $Data) + $members) }
}
Write-Host '복구 완료. AX-CAD를 다시 시작하세요.'
