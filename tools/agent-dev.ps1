<#
.SYNOPSIS
  AX-CAD multi-agent port-isolated dev launcher (Claude Code / Antigravity).
.DESCRIPTION
  Runs FastAPI backend and Next.js frontend on agent-specific fixed ports so
  Claude Code and Antigravity worktrees can run concurrently without port clashes.
.EXAMPLE
  .\tools\agent-dev.ps1 -Agent claude
  .\tools\agent-dev.ps1 -Agent antigravity
  .\tools\agent-dev.ps1 -Agent antigravity -Stop
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("claude", "antigravity", "local")]
    [string]$Agent,

    [ValidateSet("all", "backend", "frontend")]
    [string]$Mode = "all",

    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$PortMap = @{
    "local"       = @{ Api = 8000; Web = 3000 }
    "claude"      = @{ Api = 8100; Web = 3100 }
    "antigravity" = @{ Api = 8200; Web = 3200 }
}

$ApiPort = $PortMap[$Agent].Api
$WebPort = $PortMap[$Agent].Web

function Get-ListeningPids([int]$Port) {
    $lines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    $pids = @()
    foreach ($line in $lines) {
        $parts = ($line.ToString().Trim() -split "\s+")
        if ($parts.Length -ge 5 -and $parts[-1] -match "^\d+$") {
            $pids += [int]$parts[-1]
        }
    }
    return ($pids | Sort-Object -Unique)
}

if ($Stop) {
    foreach ($p in @($ApiPort, $WebPort)) {
        $pids = Get-ListeningPids $p
        foreach ($procId in $pids) {
            Write-Host "[$Agent] Stopping PID $procId on port $p"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "[$Agent] Stopped ports $ApiPort / $WebPort."
    exit 0
}

# Strict port check: fail fast instead of silently colliding with another agent
if ($Mode -in @("all", "backend")) {
    $busyApi = Get-ListeningPids $ApiPort
    if ($busyApi.Count -gt 0) {
        throw "[$Agent] API port $ApiPort is already in use (PID: $($busyApi -join ', ')). Run with -Stop first."
    }
}
if ($Mode -in @("all", "frontend")) {
    $busyWeb = Get-ListeningPids $WebPort
    if ($busyWeb.Count -gt 0) {
        throw "[$Agent] Web port $WebPort is already in use (PID: $($busyWeb -join ', ')). Run with -Stop first."
    }
}

Write-Host "============================================"
Write-Host "  AX-CAD Dev Server [$Agent]"
Write-Host "  Worktree : $RepoRoot"
Write-Host "  Backend  : http://localhost:$ApiPort"
Write-Host "  Frontend : http://localhost:$WebPort"
Write-Host "============================================"

if ($Mode -in @("all", "backend")) {
    $apiCmd = "cd /d `"$RepoRoot`" && uv run uvicorn backend.api.main:app --port $ApiPort"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $apiCmd -WindowStyle Minimized
    Write-Host "[$Agent] Backend started on :$ApiPort"
}

if ($Mode -in @("all", "frontend")) {
    $frontendDir = Join-Path $RepoRoot "frontend"
    $webCmd = "set AXCAD_API_ORIGIN=http://localhost:$ApiPort && cd /d `"$frontendDir`" && pnpm dev -- -p $WebPort"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $webCmd -WindowStyle Minimized
    Write-Host "[$Agent] Frontend started on :$WebPort (AXCAD_API_ORIGIN=http://localhost:$ApiPort)"
}

Write-Host "To stop only this agent's servers: .\tools\agent-dev.ps1 -Agent $Agent -Stop"
