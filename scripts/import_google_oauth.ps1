<#
.SYNOPSIS
Import a Google OAuth web-client JSON file into Glide's ignored .env file.

.EXAMPLE
.\scripts\import_google_oauth.ps1 -CredentialPath .\secrets\google-oauth-client.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CredentialPath,
    [string]$EnvPath = (Join-Path (Get-Location) ".env")
)

$requiredRedirect = "http://localhost:8000/api/auth/google/callback"
$credential = Get-Content -LiteralPath $CredentialPath -Raw | ConvertFrom-Json
if ($null -eq $credential.web) {
    throw "Google credential must be an OAuth Web application client JSON file."
}
$client = $credential.web
if ([string]::IsNullOrWhiteSpace($client.client_id) -or
    [string]::IsNullOrWhiteSpace($client.client_secret)) {
    throw "Google OAuth JSON is missing its client ID or client secret."
}
if (@($client.redirect_uris) -notcontains $requiredRedirect) {
    throw "Google OAuth client is missing the required redirect URI: $requiredRedirect"
}

$lines = @(Get-Content -LiteralPath $EnvPath)
function Set-EnvValue {
    param(
        [string[]]$CurrentLines,
        [string]$Name,
        [string]$Value
    )
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "Environment value for $Name contains a newline."
    }
    $found = $false
    $next = @(
        foreach ($line in $CurrentLines) {
            if ($line.StartsWith("$Name=")) {
                $found = $true
                "$Name=$Value"
            }
            else {
                $line
            }
        }
    )
    if (-not $found) {
        $next += "$Name=$Value"
    }
    return $next
}

$lines = Set-EnvValue $lines "GOOGLE_CLIENT_ID" $client.client_id
$lines = Set-EnvValue $lines "GOOGLE_CLIENT_SECRET" $client.client_secret
$lines = Set-EnvValue $lines "GOOGLE_REDIRECT_URI" $requiredRedirect

$sessionLine = $lines | Where-Object { $_.StartsWith("GLIDE_SESSION_SECRET=") } |
    Select-Object -First 1
if (-not $sessionLine -or $sessionLine -eq "GLIDE_SESSION_SECRET=") {
    $bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $sessionSecret = [Convert]::ToBase64String($bytes)
    $lines = Set-EnvValue $lines "GLIDE_SESSION_SECRET" $sessionSecret
    Remove-Variable bytes,sessionSecret
}

Set-Content -LiteralPath $EnvPath -Value $lines -Encoding utf8
Write-Host "Imported Google OAuth web-client settings into $EnvPath."
Write-Host "No secret values were printed. Load them with: . .\scripts\load_env.ps1"
