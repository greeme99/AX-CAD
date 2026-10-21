# Build the AX-CAD Windows bundle (Track 7). Runs on a Windows build machine / CI runner with
# uv, pnpm, Node and PostgreSQL installed (GitHub windows runners have all of them).
#   .\build.ps1 -Version 1.2.0 -PubKey installer\patch_pub.hex [-Out dist]
# Output: dist\AX-CAD-<version>-win-x64.zip  (install.cmd + app\) - patches are made from dist\..\app
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$PubKey,
    [string]$Out = 'dist',
    [string]$PythonVersion = '3.12.11',  # exact: a different runtime makes the small patch inapplicable
    [string]$PgRoot = $env:PGROOT
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$NodeZip = 'node-v22.23.4-win-x64.zip'
$NodeSha = 'cbc74b5e0982c9dbb41525fd8fb5dcd32fbee237863f7829072f4639a1ce5f20'  # nodejs.org SHASUMS256

function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)" }
}
function Copy-Tree([string]$From, [string]$To, [string[]]$Extra = @()) {
    & robocopy.exe $From $To /E /NFL /NDL /NJH /NJS /NP @Extra | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $From -> $To failed ($LASTEXITCODE)" }
}

if ($Version -notmatch '^\d{1,4}\.\d{1,4}\.\d{1,6}$') { throw "version must be like 1.2.0" }
$pub = (Get-Content $PubKey).Trim()
if ($pub -notmatch '^[0-9a-f]{64}$') { throw "$PubKey is not an Ed25519 public key (64 hex)" }
if (-not $PgRoot -or -not (Test-Path (Join-Path $PgRoot 'bin\postgres.exe'))) { throw 'set -PgRoot to a PostgreSQL 16+ install' }
$pgMajor = [int]((& (Join-Path $PgRoot 'bin\postgres.exe') -V) -replace '^.* (\d+)(\.\d+)*.*$', '$1')
if ($pgMajor -lt 16) { throw "PostgreSQL $pgMajor at ${PgRoot}: 16 or newer required (migration 0005 uses NULLS NOT DISTINCT)" }

$Out = [IO.Path]::GetFullPath((Join-Path $Repo $Out))
$Stage = Join-Path $Out "AX-CAD-$Version"
$App = Join-Path $Stage 'app'
Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $App | Out-Null

Write-Host '[1/6] Python runtime + locked packages'
Run 'uv' @('python', 'install', $PythonVersion)
$pyHome = Split-Path -Parent ((& uv python find $PythonVersion).Trim())
Copy-Tree $pyHome (Join-Path $App 'python') @('/XD', '__pycache__')
Get-ChildItem (Join-Path $App 'python') -Recurse -Filter 'EXTERNALLY-MANAGED' | Remove-Item -Force
$req = Join-Path $Out 'requirements.txt'
Push-Location $Repo
try { Run 'uv' @('export', '--frozen', '--no-dev', '--no-emit-project', '--format', 'requirements-txt', '-o', $req) } finally { Pop-Location }
Run 'uv' @('pip', 'install', '--python', (Join-Path $App 'python\python.exe'), '--break-system-packages', '--link-mode', 'copy', '--require-hashes', '-r', $req)

Write-Host '[2/6] PostgreSQL'
foreach ($d in 'bin', 'lib', 'share') { Copy-Tree (Join-Path $PgRoot $d) (Join-Path $App "pgsql\$d") @('/XD', 'pgAdmin*', 'doc', 'include', 'symbols') }

Write-Host '[3/6] Node.js (hash-pinned)'
$zip = Join-Path $Out $NodeZip
if (-not (Test-Path $zip)) { Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/v22.23.4/$NodeZip" -OutFile $zip }
if ((Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower() -ne $NodeSha) { throw "$NodeZip hash mismatch" }
$tmp = Join-Path $Out 'node-x'
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Expand-Archive $zip $tmp
New-Item -ItemType Directory -Force (Join-Path $App 'node') | Out-Null
Copy-Item (Join-Path $tmp 'node-v22.23.4-win-x64\node.exe'), (Join-Path $tmp 'node-v22.23.4-win-x64\LICENSE') (Join-Path $App 'node')

Write-Host '[4/6] Web (Next.js standalone, API on 127.0.0.1:18000)'
Push-Location (Join-Path $Repo 'frontend')
try {
    Run 'pnpm' @('install', '--frozen-lockfile')
    $env:NEXT_OUTPUT = 'standalone'
    $env:AXCAD_API_ORIGIN = 'http://127.0.0.1:18000'
    Run 'pnpm' @('build')
} finally { Pop-Location; Remove-Item Env:NEXT_OUTPUT, Env:AXCAD_API_ORIGIN -ErrorAction SilentlyContinue }
$fe = Join-Path $Repo 'frontend'
Copy-Tree (Join-Path $fe '.next\standalone') (Join-Path $App 'web')
Copy-Tree (Join-Path $fe '.next\static') (Join-Path $App 'web\.next\static')
if (Test-Path (Join-Path $fe 'public')) { Copy-Tree (Join-Path $fe 'public') (Join-Path $App 'web\public') }

Write-Host '[5/6] Server code and scripts'
$srv = Join-Path $App 'server'
Copy-Tree (Join-Path $Repo 'backend') (Join-Path $srv 'backend') @('/XD', '__pycache__')
Copy-Tree (Join-Path $Repo 'core') (Join-Path $srv 'core') @('/XD', '__pycache__')
Copy-Item (Join-Path $Repo 'alembic.ini'), (Join-Path $Repo 'THIRD_PARTY_NOTICES.md') $srv
$scripts = Join-Path $App 'scripts'
New-Item -ItemType Directory -Force $scripts | Out-Null
Copy-Item (Join-Path $PSScriptRoot '*.ps1') $scripts
Copy-Item (Join-Path $Repo 'installer\patcher.py') $scripts
Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $scripts 'patch_pub.hex') -Value $pub
Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $App 'VERSION') -Value $Version
Copy-Item (Join-Path $PSScriptRoot 'install.cmd') $Stage
Run (Join-Path $App 'python\python.exe') @((Join-Path $scripts 'patcher.py'), 'describe', $App)

Write-Host '[6/6] Bundle zip'
$bundle = Join-Path $Out "AX-CAD-$Version-win-x64.zip"
Remove-Item $bundle -ErrorAction SilentlyContinue
Run "$env:SystemRoot\System32\tar.exe" @('-a', '-c', '-f', $bundle, '-C', $Stage, '.')
$hash = (Get-FileHash -Algorithm SHA256 $bundle).Hash.ToLower()
Set-Content -Encoding ASCII "$bundle.sha256" "$hash  $(Split-Path -Leaf $bundle)"
Write-Host "Built $bundle ($hash)"
