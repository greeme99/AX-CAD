# AX-CAD per-PC install (Track 7): shared paths and helpers, dot-sourced by the other scripts.
# Layout:  <Root>\app   program (replaced by patches)   <Root>\data   this PC's data (never touched)
# Windows PowerShell 5.1 compatible; saved as UTF-8 with BOM so Korean text survives.
$ErrorActionPreference = 'Stop'
$ApiPort = 18000
$WebPort = 13000
$PgPort = 55432

function Use-Root([string]$Path) {
    $script:Root = [IO.Path]::GetFullPath($Path)
    $script:App = Join-Path $script:Root 'app'
    $script:Data = Join-Path $script:Root 'data'
    $script:Py = Join-Path $script:App 'python\python.exe'
    $script:PgBin = Join-Path $script:App 'pgsql\bin'
    $script:Node = Join-Path $script:App 'node\node.exe'
    $script:PgData = Join-Path $script:Data 'pg'
    $script:Logs = Join-Path $script:Data 'logs'
    $script:Run = Join-Path $script:Data 'run'
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($Exe)) failed (exit $LASTEXITCODE)" }
}

function New-Secret([int]$Bytes = 32) {
    $b = New-Object byte[] $Bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return -join ($b | ForEach-Object { $_.ToString('x2') })
}

function Read-Config {
    $cfg = @{}
    foreach ($line in Get-Content -Encoding UTF8 (Join-Path $Data 'axcad.env')) {
        $k, $v = $line -split '=', 2
        if ($v) { $cfg[$k.Trim()] = $v.Trim() }
    }
    return $cfg
}

function Set-AxEnv {
    # process environment only: inherited by PostgreSQL, the API and the web server we start
    $cfg = Read-Config
    $env:PGPASSWORD = $cfg['DB_PASSWORD']
    $env:DATABASE_URL = "postgresql+psycopg://axcad:$($cfg['DB_PASSWORD'])@127.0.0.1:$PgPort/axcad"
    $env:JWT_SECRET = $cfg['JWT_SECRET']
    $env:AXCAD_LOCAL_USER = 'local'
    $env:AXCAD_LOCAL_TOKEN = $cfg['LOCAL_TOKEN']  # every API request must carry it (main._local_guard)
    $env:AXCAD_VAR_DIR = Join-Path $Data 'var'
    $env:AXCAD_SUPPLIER_FILE = Join-Path $Data 'supplier.json'
    $env:PYTHONUTF8 = '1'
    $conv = Find-DwgConverter $cfg
    if ($conv) { $env:AXCAD_DWG_CONVERTER = $conv } else { Remove-Item Env:AXCAD_DWG_CONVERTER -ErrorAction SilentlyContinue }
}

function Find-DwgConverter($cfg) {
    # optional (Track 8 P1-5): DWG_CONVERTER in axcad.env, else ODA File Converter in Program Files,
    # else LibreDWG dwg2dxf on PATH. Checked at every start, so installing one later just works.
    if ($cfg['DWG_CONVERTER'] -and (Test-Path -LiteralPath $cfg['DWG_CONVERTER'] -PathType Leaf)) { return $cfg['DWG_CONVERTER'] }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        $exe = Get-ChildItem -Path (Join-Path $root 'ODA') -Filter 'ODAFileConverter.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($exe) { return $exe.FullName }
    }
    $cmd = Get-Command dwg2dxf.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

function Clear-Secrets {
    # before starting anything that is not ours (the browser): it would inherit them
    foreach ($n in 'PGPASSWORD', 'DATABASE_URL', 'JWT_SECRET', 'AXCAD_LOCAL_TOKEN') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}

function Test-Db { & (Join-Path $PgBin 'pg_ctl.exe') -D $PgData status *> $null; return $LASTEXITCODE -eq 0 }

function Start-Db {
    if (Test-Db) { return }
    $opts = "-p $PgPort -c listen_addresses=127.0.0.1"
    Invoke-Native (Join-Path $PgBin 'pg_ctl.exe') @('-D', $PgData, '-l', (Join-Path $Logs 'postgres.log'), '-w', '-t', '60', '-o', $opts, 'start')
}

function Stop-Db { if (Test-Db) { Invoke-Native (Join-Path $PgBin 'pg_ctl.exe') @('-D', $PgData, '-m', 'fast', '-w', 'stop') } }

function Invoke-Migrate {
    Push-Location (Join-Path $App 'server')
    try { Invoke-Native $Py @('-m', 'alembic', 'upgrade', 'head') } finally { Pop-Location }
}

function Start-App {
    Stop-App
    New-Item -ItemType Directory -Force $Run, $Logs | Out-Null
    $api = Start-Process -FilePath $Py -WindowStyle Hidden -PassThru -WorkingDirectory (Join-Path $App 'server') `
        -ArgumentList @('-m', 'uvicorn', 'backend.api.main:app', '--host', '127.0.0.1', '--port', "$ApiPort") `
        -RedirectStandardOutput (Join-Path $Logs 'api.log') -RedirectStandardError (Join-Path $Logs 'api.err.log')
    $env:PORT = "$WebPort"
    $env:HOSTNAME = '127.0.0.1'
    $web = Start-Process -FilePath $Node -WindowStyle Hidden -PassThru -WorkingDirectory (Join-Path $App 'web') `
        -ArgumentList @('server.js') `
        -RedirectStandardOutput (Join-Path $Logs 'web.log') -RedirectStandardError (Join-Path $Logs 'web.err.log')
    Set-Content -Encoding ASCII (Join-Path $Run 'pids.txt') "$($api.Id) $($web.Id)"
}

function Stop-App {
    $f = Join-Path $Run 'pids.txt'
    if (-not (Test-Path $f)) { return }
    foreach ($id in (Get-Content $f) -split ' ') {
        # after a reboot the id may belong to someone else's program: only kill our own executables
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($p -and $p.Path -and $p.Path.StartsWith($App + '\', [StringComparison]::OrdinalIgnoreCase)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $id /T /F *> $null  # the API's geometry workers too
        }
    }
    Remove-Item $f -Force
}

function Wait-Healthy([int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $api = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Headers @{ 'X-AXCAD-Local' = $env:AXCAD_LOCAL_TOKEN } "http://127.0.0.1:$ApiPort/api/health"
            $web = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:$WebPort/login"
            if ($api.StatusCode -eq 200 -and $web.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}

function New-Backup {
    # PostgreSQL must be running. Returns the dump path; keeps the newest 10 of each kind.
    $dir = Join-Path $Data 'backup'
    New-Item -ItemType Directory -Force $dir | Out-Null
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dump = Join-Path $dir "axcad-$ts.dump"
    Invoke-Native (Join-Path $PgBin 'pg_dump.exe') @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'axcad', '-Fc', '-f', $dump, 'axcad')
    $items = @('var', 'supplier.json') | Where-Object { Test-Path (Join-Path $Data $_) }
    if ($items) {  # uploaded drawings, metric caches, supplier settings
        Invoke-Native "$env:SystemRoot\System32\tar.exe" (@('-a', '-c', '-f', (Join-Path $dir "var-$ts.zip"), '-C', $Data) + $items)
    }
    foreach ($pattern in 'axcad-*.dump', 'var-*.zip') {
        Get-ChildItem $dir -Filter $pattern | Sort-Object Name -Descending | Select-Object -Skip 10 | Remove-Item -Force
    }
    return $dump
}

function Restore-Db([string]$Dump) {
    # a fresh database, not --clean: tables a failed migration added must not survive the restore
    $conn = @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'axcad')
    Invoke-Native (Join-Path $PgBin 'dropdb.exe') ($conn + @('--force', '--if-exists', '--maintenance-db=postgres', 'axcad'))
    Invoke-Native (Join-Path $PgBin 'createdb.exe') ($conn + @('--maintenance-db=postgres', 'axcad'))
    Invoke-Native (Join-Path $PgBin 'pg_restore.exe') ($conn + @('-d', 'axcad', '--no-owner', '--exit-on-error', $Dump))
}

function Show-Message([string]$Text, [string]$Title = 'AX-CAD') {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show($Text, $Title)
}
