# Refreshes the pinned public ECR image digests used by the AWS MCP servers.
#
# Run deliberately (Docker Desktop must be running), review the resulting
# diff, and commit the refreshed pins. The MCP config must never reference a
# mutable tag.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$tags = @(
    'public.ecr.aws/awslabs-mcp/awslabs/cloudwatch-mcp-server:latest',
    'public.ecr.aws/awslabs-mcp/awslabs/dynamodb-mcp-server:latest',
    'public.ecr.aws/awslabs-mcp/awslabs/amazon-sns-sqs-mcp-server:latest',
    'public.ecr.aws/awslabs-mcp/awslabs/lambda-tool-mcp-server:latest'
)
$files = @(
    'tools/mcp/aws/mcp-section.toml',
    'tools/mcp/codex-config.toml',
    'tools/mcp/aws/install-aws-mcps.ps1'
)

foreach ($tag in $tags) {
    $digest = docker buildx imagetools inspect $tag --format '{{.Manifest.Digest}}'
    if ($LASTEXITCODE -ne 0 -or -not $digest) {
        throw "Could not resolve a digest for $tag"
    }
    $repo = $tag -replace ':latest$', ''
    $pattern = [regex]::Escape($repo) + '@sha256:[0-9a-f]{64}'
    foreach ($relative in $files) {
        $path = Join-Path $root $relative
        $content = Get-Content -LiteralPath $path -Raw
        $updated = [regex]::Replace($content, $pattern, "$repo@$digest")
        if ($updated -ne $content) {
            Set-Content -LiteralPath $path -Value $updated -NoNewline
            Write-Host "Updated $relative -> $digest"
        }
    }
}

Write-Host 'Review the diff and commit the refreshed digests.'
