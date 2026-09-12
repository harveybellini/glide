# Reports the newest published mcp-proxy-for-aws-cli version and, with -Apply,
# rewrites the pinned version used by the AWS MCP Server. Run deliberately,
# review the resulting diff, and commit. The MCP config must never reference
# @latest or any other mutable reference.
param(
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$current = [regex]::Match(
    (Get-Content -LiteralPath (Join-Path $root 'tools/mcp/aws/mcp-section.toml') -Raw),
    'mcp-proxy-for-aws-cli@(\d+\.\d+\.\d+)'
).Groups[1].Value
if (-not $current) { throw 'Could not read the currently pinned proxy version.' }

$latest = $null
try {
    $latest = (Invoke-RestMethod -Uri 'https://pypi.org/pypi/mcp-proxy-for-aws-cli/json' -TimeoutSec 30).info.version
} catch {
    Write-Warning "Could not reach PyPI: $($_.Exception.Message)"
}

Write-Host "Pinned:  mcp-proxy-for-aws-cli@$current"
Write-Host "Latest:  mcp-proxy-for-aws-cli@$(if ($latest) { $latest } else { '<unknown>' })"

if (-not $Apply) {
    Write-Host 'Dry run. Re-run with -Apply to rewrite the pinned files.'
    exit 0
}
if (-not $latest) { throw 'Refusing to rewrite pins without a resolved latest version.' }
if ($latest -eq $current) {
    Write-Host 'Already current; nothing to rewrite.'
    exit 0
}

foreach ($relative in @('tools/mcp/aws/mcp-section.toml', 'tools/mcp/codex-config.toml')) {
    $path = Join-Path $root $relative
    $content = Get-Content -LiteralPath $path -Raw
    $updated = [regex]::Replace($content, 'mcp-proxy-for-aws-cli@\d+\.\d+\.\d+', "mcp-proxy-for-aws-cli@$latest")
    if ($updated -ne $content) {
        Set-Content -LiteralPath $path -Value $updated -NoNewline
        Write-Host "Updated $relative -> $latest"
    }
}

Write-Host 'Review the diff, re-run scripts/install-mcps.ps1, and commit.'
