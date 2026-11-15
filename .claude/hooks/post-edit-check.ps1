$ErrorActionPreference = "Stop"

# AX-CAD Post Tool Use Check — Python 파일 편집 시에만 ruff 실행
$raw = $input | Out-String
if (-not $raw) { exit 0 }

try {
    $json = $raw | ConvertFrom-Json
    $file = $json.tool_input.file_path
} catch {
    exit 0
}

if ($file -and $file.EndsWith(".py")) {
    if (Get-Command ruff -ErrorAction SilentlyContinue) {
        ruff check --select E,F --quiet $file 2>$null
    }
}

exit 0
