# AX-CAD start: this PC's database, the API (127.0.0.1 only) and the web screen, then the browser.
param([switch]$NoBrowser)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
try {
    Set-AxEnv
    Start-Db
    Start-App
    if (-not (Wait-Healthy)) { throw "AX-CAD가 시작되지 않았습니다. 로그: $Logs" }
    $url = "http://localhost:$WebPort/#axcad=$env:AXCAD_LOCAL_TOKEN"  # the page keeps it, see lib/api.ts
    Clear-Secrets
    if (-not $NoBrowser) { Start-Process $url }
} catch {
    if ($NoBrowser) { throw }
    Show-Message "AX-CAD를 시작하지 못했습니다.`n$($_.Exception.Message)"
    exit 1
}
