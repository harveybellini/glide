<#
.SYNOPSIS
Inspect or operate the deployed Glide credit guard.

.EXAMPLE
scripts/budget-guard.ps1 -Action status

.EXAMPLE
scripts/budget-guard.ps1 -Action restore
#>
param(
    [ValidateSet("check", "status", "shutdown", "restore")]
    [string]$Action = "status",
    [string]$StackName = "glide",
    [string]$Region = "eu-west-1",
    [string]$Profile = "glide",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$functionName = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --profile $Profile `
    --query "Stacks[0].Outputs[?OutputKey=='BudgetGuardFunctionName'].OutputValue | [0]" `
    --output text
if ($LASTEXITCODE -ne 0 -or -not $functionName -or $functionName -eq "None") {
    throw "The $StackName stack does not report a BudgetGuardFunctionName output."
}

$request = @{ action = $Action }
if ($Force) { $request.force = $true }
$suffix = [guid]::NewGuid().ToString("N")
$payloadPath = Join-Path ([System.IO.Path]::GetTempPath()) "glide-budget-guard-$suffix.json"
$responsePath = Join-Path ([System.IO.Path]::GetTempPath()) "glide-budget-guard-$suffix-response.json"

try {
    [System.IO.File]::WriteAllText(
        $payloadPath,
        ($request | ConvertTo-Json -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    $metadata = aws lambda invoke `
        --function-name $functionName `
        --region $Region `
        --profile $Profile `
        --payload "fileb://$payloadPath" `
        --output json `
        $responsePath | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "The budget guard invocation failed with exit code $LASTEXITCODE."
    }
    $response = Get-Content -LiteralPath $responsePath -Raw
    if ($metadata.FunctionError) {
        throw "The budget guard returned $($metadata.FunctionError): $response"
    }
    $response | ConvertFrom-Json | ConvertTo-Json -Depth 8
}
finally {
    Remove-Item -LiteralPath $payloadPath, $responsePath -Force -ErrorAction SilentlyContinue
}
