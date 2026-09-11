<#
.SYNOPSIS
Build and deploy the Glide SAM stack and upload the web app.

.DESCRIPTION
Prerequisites: AWS CLI and SAM CLI configured with an account that has the
Bedrock model and Amazon Location access described in docs/setup.md. Python 3.12
must be on PATH for `sam build` (on Windows: `uv python install 3.12`).

The public CloudFront domain is only known after the first deployment, so the
stack is deployed twice: once with a placeholder frontend origin, then again
with the real origin once the distribution exists. The Google OAuth callback
URI is derived from that origin; register
`https://<distribution>/api/auth/google/callback` in Google Cloud before users
connect.
#>
param(
    [Parameter(Mandatory = $true)][string]$StackName,
    [string]$Stage = "prod",
    [string]$Region = "eu-west-1",
    [Parameter(Mandatory = $true)][string]$BedrockModelId,
    [Parameter(Mandatory = $true)][string]$GoogleClientId,
    [Parameter(Mandatory = $true)][string]$GoogleClientSecretArn,
    [string]$Profile = "glide"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

# Fresh shells may not yet have the installers' PATH entries.
if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
    $sam = "C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd"
    if (Test-Path $sam) {
        $env:Path = (Split-Path $sam) + ";" + $env:Path
    }
}
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
    if (Test-Path $aws) {
        $env:Path = (Split-Path $aws) + ";" + $env:Path
    }
}

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path ([System.IO.Path]::GetTempPath()) "uv-cache-glide"
}

function Invoke-SamDeploy {
    param([string]$FrontendOrigin)

    $ErrorActionPreference = "Continue"
    sam deploy `
        --template-file (Join-Path $Root "infra/template.yaml") `
        --stack-name $StackName `
        --region $Region `
        --profile $Profile `
        --parameter-overrides `
            "Stage=$Stage" `
            "BedrockModelId=$BedrockModelId" `
            "GoogleClientId=$GoogleClientId" `
            "GoogleClientSecretArn=$GoogleClientSecretArn" `
            "FrontendOrigin=$FrontendOrigin" `
        --capabilities CAPABILITY_IAM `
        --resolve-s3 `
        --no-confirm-changeset
    if ($LASTEXITCODE -ne 0) { throw "sam deploy failed with exit code $LASTEXITCODE" }
    $ErrorActionPreference = "Stop"
}

Write-Host "1/6 Building the frontend"
Push-Location (Join-Path $Root "frontend")
try {
    $ErrorActionPreference = "Continue"
    npm ci
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed with exit code $LASTEXITCODE" }
    $ErrorActionPreference = "Stop"
}
finally {
    Pop-Location
}

Write-Host "2/6 Building the Lambda bundle"
& (Join-Path $Root "scripts/build_lambda.ps1")
if ($LASTEXITCODE -ne 0) { throw "lambda bundle build failed" }

Write-Host "3/6 Validating the SAM template"
$ErrorActionPreference = "Continue"
sam validate --lint --template-file (Join-Path $Root "infra/template.yaml")
if ($LASTEXITCODE -ne 0) { throw "sam validate failed with exit code $LASTEXITCODE" }
$ErrorActionPreference = "Stop"

Write-Host "4/6 First deployment (placeholder frontend origin)"
Invoke-SamDeploy -FrontendOrigin "https://frontend.invalid"

Write-Host "5/6 Resolving the distribution and re-deploying with the real origin"
$ErrorActionPreference = "Continue"
$outputs = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --profile $Profile `
    --query "Stacks[0].Outputs" `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "describe-stacks failed" }
$ErrorActionPreference = "Stop"
$distributionDomain = ($outputs |
    Where-Object OutputKey -eq "DistributionDomainName").OutputValue
if (-not $distributionDomain) {
    throw "stack did not report DistributionDomainName"
}
$frontendOrigin = "https://$distributionDomain"
Invoke-SamDeploy -FrontendOrigin $frontendOrigin

Write-Host "6/6 Uploading the web app and invalidating the cache"
$bucket = ($outputs | Where-Object OutputKey -eq "UiBucketName").OutputValue
$distributionId = ($outputs | Where-Object OutputKey -eq "DistributionId").OutputValue
$ErrorActionPreference = "Continue"
aws s3 sync (Join-Path $Root "frontend/dist") "s3://$bucket" --delete `
    --region $Region --profile $Profile
if ($LASTEXITCODE -ne 0) { throw "s3 sync failed with exit code $LASTEXITCODE" }
aws cloudfront create-invalidation `
    --distribution-id $distributionId --paths "/*" --profile $Profile | Out-Null
if ($LASTEXITCODE -ne 0) { throw "cloudfront invalidation failed with exit code $LASTEXITCODE" }
$ErrorActionPreference = "Stop"

Write-Host "Deployed. Distribution: $frontendOrigin"
Write-Host "Register this OAuth callback in Google Cloud: $frontendOrigin/api/auth/google/callback"
