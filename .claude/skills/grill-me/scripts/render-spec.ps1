param(
  [Parameter(Mandatory=$true)][string]$Goal,
  [string]$Scope = "",
  [string]$Constraints = "",
  [string]$Success = ""
)
@"
# Implementation Brief
- Goal: $Goal
- Scope: $Scope
- Constraints: $Constraints
- Success criteria: $Success
"@
