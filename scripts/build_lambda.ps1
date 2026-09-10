<#
.SYNOPSIS
Build backend/glide-lambda.zip: the Glide package plus its Linux dependencies.

.DESCRIPTION
Resolves the runtime dependencies for the AWS Lambda environment
(python 3.12, x86_64, Linux) with uv, which evaluates platform markers against
the target platform. This excludes Windows-only transitive dependencies such as
pywin32 that SAM's host-side pip builder would otherwise reject. The resulting
zip is referenced directly by infra/template.yaml, so `sam deploy` uploads it
as-is.
#>
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $Root ".build-lambda"
$ZipPath = Join-Path $Root "backend/glide-lambda.zip"

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path ([System.IO.Path]::GetTempPath()) "uv-cache-glide"
}

Write-Host "1/4 Exporting Linux-pinned requirements"
Push-Location $Root
try {
    $ErrorActionPreference = "Continue"
    uv export --no-dev --no-emit-project --format requirements-txt `
        -o backend/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "uv export failed with exit code $LASTEXITCODE" }
    $ErrorActionPreference = "Stop"
    # pywin32 is a Windows-only transitive dependency of mcp. Lambda runs
    # Linux, which has no wheel for it, so drop that requirement block.
    $lines = Get-Content backend/requirements.txt
    $kept = New-Object System.Collections.Generic.List[string]
    $skipping = $false
    foreach ($line in $lines) {
        if ($line -match '^pywin32==') { $skipping = $true; continue }
        if ($skipping) {
            if ($line -match '^\s') { continue }
            $skipping = $false
        }
        $kept.Add($line)
    }
    Set-Content -Path backend/requirements.txt -Value $kept -Encoding utf8
}
finally {
    Pop-Location
}

Write-Host "2/4 Installing dependencies for Lambda (python 3.12, Linux x86_64)"
if (Test-Path $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
$ErrorActionPreference = "Continue"
uv pip install `
    --target $BuildDir `
    --python-platform x86_64-unknown-linux-gnu `
    --python-version 3.12 `
    --only-binary :all: `
    --no-compile `
    -r (Join-Path $Root "backend/requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
$ErrorActionPreference = "Stop"

Write-Host "3/4 Adding the Glide package"
Copy-Item -Path (Join-Path $Root "backend/glide") `
    -Destination $BuildDir -Recurse -Force

Write-Host "4/4 Zipping the Lambda bundle"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $BuildDir "*") -DestinationPath $ZipPath

Write-Host "Built $ZipPath"
