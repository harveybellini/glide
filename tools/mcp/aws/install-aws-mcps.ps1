# Installs the pinned awslabs MCP Docker images used by .codex/config.toml.
# Prerequisites: Docker Desktop running, network access to public.ecr.aws.
#
# The digests below are the immutable references committed in the MCP config.
# Refresh them deliberately with scripts/refresh-mcp-pins.ps1 (never by tag).
$ErrorActionPreference = 'Stop'

$images = @(
    'public.ecr.aws/awslabs-mcp/awslabs/cloudwatch-mcp-server@sha256:477bfd0885228340a0979ddabcca13f4e7df0fce39cbb128bf7cfa21318de62a',
    'public.ecr.aws/awslabs-mcp/awslabs/dynamodb-mcp-server@sha256:69a2ad0e28589fe0e0b8a93e27b2a87f1da6576226dca859feaafbf24c350ece',
    'public.ecr.aws/awslabs-mcp/awslabs/amazon-sns-sqs-mcp-server@sha256:d77620e821c1693c8e965db9341d75ce1c937ee5295bfec12918f675f025e8b7',
    'public.ecr.aws/awslabs-mcp/awslabs/lambda-tool-mcp-server@sha256:ed03f333e07128676235ee0e6026ff5e64f4b2e1a56c9b64bba58f47c8d79b4a'
)

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker daemon is not reachable. Start Docker Desktop and retry.'
}

foreach ($image in $images) {
    Write-Host "Pulling $image ..."
    docker pull $image
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull $image (check network access to ghcr.io)."
    }
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Image $image was not found after pulling."
    }
}

Write-Host 'All four pinned awslabs MCP images are installed.'
Write-Host 'Restart Codex to load the MCP servers, then run: codex mcp list'
