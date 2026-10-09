$ErrorActionPreference = "Stop"
$raw = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

try { $event = $raw | ConvertFrom-Json } catch { exit 0 }
$command = [string]$event.tool_input.command

$patterns = @(
  '(?i)\brm\s+-rf\s+[/\\](?:\s|$)',
  '(?i)\bdel\s+/[sq]\s+[a-z]:\\',
  '(?i)\brmdir\s+/s\s+/q\s+[a-z]:\\',
  '(?i)\bformat(?:\.com)?\s+[a-z]:',
  '(?i)\bdiskpart\b',
  '(?i)\bgit\s+push\b.*(?:--force|-f)\b',
  '(?i)\bgit\s+reset\s+--hard\b',
  '(?i)\bgit\s+clean\s+-[a-z]*f[a-z]*d',
  '(?i)\b(?:drop|truncate)\s+(?:database|table)\b',
  '(?i)\bterraform\s+destroy\b',
  '(?i)\bkubectl\s+delete\s+(?:namespace|ns)\b'
)

foreach ($pattern in $patterns) {
  if ($command -match $pattern) {
    [Console]::Error.WriteLine("BLOCKED: 위험 명령은 Human-in-the-loop 승인이 필요합니다: $command")
    exit 2
  }
}
exit 0
