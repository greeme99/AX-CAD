[CmdletBinding()]
param(
  [string]$WorkspaceRoot = "C:\Users\user\Documents\Claude",
  [int]$StaleDays = 30,
  [int]$ArchiveDays = 90
)

$now = Get-Date
$projectsRoot = Join-Path $WorkspaceRoot "Projects"

foreach ($project in Get-ChildItem $projectsRoot -Directory) {
  $usageFile = Join-Path $project.FullName ".claude\skills\.usage.json"
  if (-not (Test-Path $usageFile)) { continue }

  $usage = Get-Content $usageFile -Raw | ConvertFrom-Json
  foreach ($prop in $usage.PSObject.Properties) {
    $skillName = $prop.Name
    $entry = $prop.Value

    if ($entry.created_by -ne "agent") { continue }
    if ($entry.pinned) { continue }
    if (-not $entry.last_used_at) { continue }

    $lastUsed = [datetime]$entry.last_used_at
    $days = ($now - $lastUsed).Days

    if ($days -ge $ArchiveDays) {
      Write-Host "[$($project.Name)] $skillName : ${days}d idle -> ARCHIVE"
    } elseif ($days -ge $StaleDays) {
      Write-Host "[$($project.Name)] $skillName : ${days}d idle -> STALE"
    }
  }
}
