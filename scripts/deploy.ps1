<#
.SYNOPSIS
Build and deploy the Glide SAM stack and upload the web app.

.DESCRIPTION
Prerequisites: AWS CLI and SAM CLI configured with an account that has the
Bedrock model and Amazon Location access described in docs/setup.md. This
script has not been executed against a live account yet; run it only after
account access and a spending cap are confirmed.
#>
param(
    [Parameter(Mandatory = $true)][string]$StackName,
    [string]$Stage = "prod",
    [string]$Region = "eu-west-1",
    [Parameter(Mandatory = $true)][string]$BedrockModelId,
    [Parameter(Mandatory = $true)][string]$GoogleClientId,
    [Parameter(Mandatory = $true)][string]$GoogleClientSecret
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "1/5 Exporting Python requirements for SAM"
Push-Location $Root
try {
    uv export --no-dev --format requirements-txt -o backend/requirements.txt
}
finally {
    Pop-Location
}

Write-Host "2/5 Building the frontend"
Push-Location (Join-Path $Root "frontend")
try {
    npm ci
    npm run build
}
finally {
    Pop-Location
}

Write-Host "3/5 Building and deploying the SAM stack"
sam build --template-file (Join-Path $Root "infra/template.yaml") --region $Region
sam deploy `
    --stack-name $StackName `
    --region $Region `
    --parameter-overrides `
        "Stage=$Stage" `
        "BedrockModelId=$BedrockModelId" `
        "GoogleClientId=$GoogleClientId" `
        "GoogleClientSecret=$GoogleClientSecret" `
    --capabilities CAPABILITY_IAM `
    --no-confirm-changeset

Write-Host "4/5 Uploading the web app"
$outputs = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output json | ConvertFrom-Json
$bucket = ($outputs | Where-Object OutputKey -eq "UiBucketName").OutputValue
$distributionId = ($outputs | Where-Object OutputKey -eq "DistributionId").OutputValue
$distribution = ($outputs | Where-Object OutputKey -eq "DistributionDomainName").OutputValue
aws s3 sync (Join-Path $Root "frontend/dist") "s3://$bucket" --delete --region $Region

Write-Host "5/5 Invalidating the CloudFront cache"
aws cloudfront create-invalidation `
    --distribution-id $distributionId `
    --paths "/*" | Out-Null

Write-Host "Deployed. Distribution: https://$distribution"
