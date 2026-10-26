# AX-CAD update: apply a signed patch file (AX-CAD-<version>-patch.zip).
# Order: stop -> back up the DB -> verify + swap program components -> migrate -> start -> check.
# Any failure puts the previous program and database back and restarts it.
# -Result: started from the app (Track 8 P5); the outcome goes to that JSON file for its screen.
param([string]$Patch, [switch]$NoPause, [string]$Result)
. (Join-Path $PSScriptRoot 'common.ps1')
Use-Root (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

function Done([string]$Text, [string]$Status = 'OK') {
    Write-Host $Text
    if ($Result) {
        $out = [ordered]@{
            status = $Status; from = $before; message = $Text
            to = (Get-Content (Join-Path $App 'VERSION')).Trim()
            finished_at = (Get-Date -Format 's')
        }
        # whole file or nothing: the update screen reads it while we write
        [IO.File]::WriteAllText("$Result.tmp", ($out | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
        Move-Item -LiteralPath "$Result.tmp" -Destination $Result -Force
    }
    if (-not $NoPause) { Read-Host 'Enter 키를 누르면 닫힙니다' | Out-Null }
}

if (-not $Patch) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = 'AX-CAD 패치 (*.zip)|*.zip'
    $dlg.Title = 'AX-CAD 패치 파일 선택'
    if ($dlg.ShowDialog() -ne 'OK') { exit 0 }
    $Patch = $dlg.FileName
}
$Patch = (Resolve-Path -LiteralPath $Patch).Path
$before = (Get-Content (Join-Path $App 'VERSION')).Trim()
Write-Host "현재 버전 $before, 패치: $Patch"

try {
    # the patcher must not run from app\python while app\python may be replaced: use a copy
    $tool = Join-Path $Run 'updater'
    Remove-Item -Recurse -Force $tool -ErrorAction SilentlyContinue
    & robocopy.exe (Join-Path $App 'python') (Join-Path $tool 'python') /E /XD site-packages /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "업데이트 도구 준비 실패 (robocopy $LASTEXITCODE)" }
    Copy-Item (Join-Path $App 'scripts\patcher.py'), (Join-Path $App 'scripts\patch_pub.hex') $tool
    $toolPy = Join-Path $tool 'python\python.exe'
    $patcher = @((Join-Path $tool 'patcher.py'))

    Set-AxEnv
    Stop-App
    Start-Db
    Write-Host '데이터베이스 백업 중...'
    $dump = New-Backup
    Stop-Db
} catch {  # nothing replaced yet (e.g. the disk is full for the backup): keep the old version running
    $err = $_.Exception.Message
    try { Set-AxEnv; Start-Db; Start-App } catch { $err += " / 재시작 실패: $($_.Exception.Message)" }
    Done "업데이트를 시작하지 못했습니다 (프로그램은 그대로): $err" 'REFUSED'
    exit 1
}

& $toolPy @patcher 'apply' $Patch '--app' $App '--pub' (Join-Path $tool 'patch_pub.hex')
if ($LASTEXITCODE -ne 0) {
    Start-Db; Start-App
    Done '패치를 적용하지 않았습니다 (위 오류 참고). 기존 버전으로 계속 실행합니다.' 'REFUSED'
    exit 1
}
$migrated = $false
try {
    $pgv = (Get-Content (Join-Path $PgData 'PG_VERSION')).Trim()
    $bin = (& (Join-Path $PgBin 'postgres.exe') -V) -replace '^.* (\d+)(\.\d+)*.*$', '$1'
    if ($pgv -ne $bin) { throw "PostgreSQL 주 버전이 다릅니다 (데이터 $pgv, 프로그램 $bin): 이 패치는 적용할 수 없습니다" }
    Start-Db
    $migrated = $true
    Invoke-Migrate
    Start-App
    if (-not (Wait-Healthy)) { throw "새 버전이 시작되지 않았습니다. 로그: $Logs" }
} catch {
    $err = $_.Exception.Message
    Write-Host "실패: $err - 이전 버전으로 되돌립니다"
    Stop-App
    try {
        if ($migrated) { Start-Db; Restore-Db $dump }
    } catch {
        $err += " / DB 복구 실패: $($_.Exception.Message) - 백업 파일로 수동 복구하세요: $dump"
    } finally {
        Stop-Db
        & $toolPy @patcher 'rollback' $App  # the old program goes back whatever happened above
    }
    Start-Db; Start-App
    Done "업데이트 실패, 이전 버전 $before 로 되돌렸습니다: $err" 'ROLLED_BACK'
    exit 1
}
& $toolPy @patcher 'commit' $App
Done "업데이트 완료: $before -> $((Get-Content (Join-Path $App 'VERSION')).Trim())"
