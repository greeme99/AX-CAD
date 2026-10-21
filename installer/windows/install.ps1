# AX-CAD per-PC install (Track 7). Run from the extracted bundle: install.cmd (no admin rights).
# Copies app\ to %LOCALAPPDATA%\AX-CAD, creates this PC's database and the no-login user,
# adds Start-menu shortcuts. Data stays on this PC only.
param(
    [string]$Dest = (Join-Path $env:LOCALAPPDATA 'AX-CAD'),
    [switch]$NoShortcuts,
    [switch]$NoStart
)
. (Join-Path $PSScriptRoot 'common.ps1')
$bundleApp = Split-Path -Parent $PSScriptRoot
Use-Root $Dest
if (Test-Path (Join-Path $Data 'pg')) { throw "이미 설치되어 있습니다: $Root (새 버전은 'AX-CAD 업데이트'로 패치를 적용하세요)" }

Write-Host "AX-CAD 설치: $Root"
New-Item -ItemType Directory -Force $Root, $Data, $Logs, $Run, (Join-Path $Data 'var'), (Join-Path $Data 'backup') | Out-Null
& robocopy.exe $bundleApp $App /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "프로그램 복사 실패 (robocopy $LASTEXITCODE)" }

# secrets of this PC only: the DB password and the token key never leave data\
$cfg = Join-Path $Data 'axcad.env'
Set-Content -Encoding ASCII $cfg @("DB_PASSWORD=$(New-Secret)", "JWT_SECRET=$(New-Secret 48)")
& icacls.exe $cfg /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
Copy-Item (Join-Path $App 'server\backend\config\supplier.json') (Join-Path $Data 'supplier.json')
Set-AxEnv

$pw = Join-Path $Run 'pw.tmp'
Set-Content -Encoding ASCII -NoNewline $pw $env:PGPASSWORD
try {
    Invoke-Native (Join-Path $PgBin 'initdb.exe') @('-D', $PgData, '-U', 'axcad', '-A', 'scram-sha-256', "--pwfile=$pw", '-E', 'UTF8', '--no-locale')
} finally { Remove-Item $pw -Force }
Start-Db
Invoke-Native (Join-Path $PgBin 'createdb.exe') @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'axcad', 'axcad')
Invoke-Migrate
Push-Location (Join-Path $App 'server')
try { Invoke-Native $Py @('-m', 'backend.cli', 'local-user') } finally { Pop-Location }
Stop-Db

if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'AX-CAD'
    New-Item -ItemType Directory -Force $menu | Out-Null
    $links = @(
        @('AX-CAD', 'start.ps1', 'Hidden'),
        @('AX-CAD 종료', 'stop.ps1', 'Normal'),
        @('AX-CAD 백업', 'backup.ps1', 'Normal'),
        @('AX-CAD 업데이트', 'update.ps1', 'Normal')
    )
    foreach ($l in $links) {
        $dirs = @($menu)
        if ($l[0] -eq 'AX-CAD') { $dirs += [Environment]::GetFolderPath('Desktop') }
        foreach ($dir in $dirs) {
            $s = $shell.CreateShortcut((Join-Path $dir "$($l[0]).lnk"))
            $s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
            $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle $($l[2]) -File `"$(Join-Path $App "scripts\$($l[1])")`""
            $s.WorkingDirectory = $Root
            $s.Save()
        }
    }
}
Write-Host "설치 완료 ($(Get-Content (Join-Path $App 'VERSION'))). 시작 메뉴의 'AX-CAD'로 실행하세요."
if (-not $NoStart) { & (Join-Path $App 'scripts\start.ps1') }
