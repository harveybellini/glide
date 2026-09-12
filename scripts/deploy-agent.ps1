#Requires -Version 5.1
<#
.SYNOPSIS
  Unattended, agent-safe deployment entrypoint for the Glide SAM stack.

.DESCRIPTION
  Wraps scripts/deploy.ps1 so another agent or a CI job can deploy without a
  human at the keyboard.

  - Never prompts. The child deploy runs with -NonInteractive and missing
    configuration fails fast with exit code 2 instead of hanging on Read-Host.
  - Loads .env without overwriting variables the caller already set, so
    `$env:X = ...; scripts\deploy-agent.ps1` wins over the file.
  - Reuses the live stack's non-secret parameters (Bedrock model id, Google
    secret ARN, notification and alarm addresses) so a redeploy does not
    silently drop configuration that is already deployed. GoogleClientId is
    NoEcho in the template and therefore must come from .env or a parameter.
  - Holds temp/deploy/deploy.lock so two agents cannot build or deploy at the
    same time. -WaitForLock queues behind the other run instead of failing.
  - Streams every line to temp/deploy/deploy-<stack>-<timestamp>.log and
    writes temp/deploy/last-deploy.json with the stack outputs and the health
    result, so a supervising agent can read the outcome without parsing logs.
    Neither file ever contains a secret value.
  - Verifies https://<distribution>/ and /api/health after the upload.

  Secrets: the Google client secret is read from $env:GOOGLE_CLIENT_SECRET
  (loaded from .env) and only reaches the AWS CLI through a temp file, as in
  deploy.ps1. When the stack already has a GoogleClientSecretArn that ARN is
  reused and no secret value is needed at all.

.PARAMETER StackName
  Stack to deploy. Default: $env:GLIDE_STACK_NAME, else the existing value,
  else "glide".

.PARAMETER DryRun
  Resolve settings and run the preflight checks, then stop without building,
  uploading, or deploying anything.

.PARAMETER VerifyOnly
  Do not deploy; only run the health checks against the stack that is live.

.PARAMETER SkipFrontendBuild
  Reuse the existing frontend/dist (deploy.ps1 still uploads and invalidates).

.PARAMETER SkipLambdaBuild
  Reuse the existing backend/glide-lambda.zip. Only safe when the backend did
  not change, because the archive is gitignored and may be stale.

.PARAMETER WaitForLock
  Wait up to -LockTimeoutSeconds for another deployment to finish instead of
  failing immediately.

.PARAMETER SkipHealthCheck
  Skip the post-deploy HTTPS checks (the summary still records the outputs).

.OUTPUTS
  Exit code 0 when the stack is deployed and healthy, 1 when the deployment or
  the verification failed, 2 when configuration or a preflight check failed
  before anything was built or deployed.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-agent.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-agent.ps1 -DryRun

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-agent.ps1 -VerifyOnly
#>
[CmdletBinding()]
param(
    [string]$StackName,
    [string]$Stage,
    [string]$Region,
    [string]$BedrockModelId,
    [string]$GoogleClientId,
    [string]$GoogleClientSecretArn,
    [string]$GoogleSecretName,
    [string]$NotificationFromEmail,
    [string]$AlarmEmail,
    [string]$Profile,
    [string]$LogDirectory,
    [switch]$DryRun,
    [switch]$VerifyOnly,
    [switch]$SkipFrontendBuild,
    [switch]$SkipLambdaBuild,
    [switch]$SkipHealthCheck,
    [switch]$WaitForLock,
    [int]$LockTimeoutSeconds = 900,
    [int]$HealthCheckAttempts = 12,
    [int]$HealthCheckDelaySeconds = 10
)

$ErrorActionPreference = "Stop"
$script:Root = Split-Path -Parent $PSScriptRoot
$script:StartedAt = Get-Date
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:LogPath = $null
$script:SummaryPath = $null
$script:LockPath = $null
$script:LockStream = $null
$script:Summary = [ordered]@{
    status = "running"
    startedAt = $script:StartedAt.ToString("o")
    finishedAt = $null
    durationSeconds = $null
    exitCode = $null
    dryRun = [bool]$DryRun
    verifyOnly = [bool]$VerifyOnly
    stackName = $null
    region = $null
    stage = $null
    profile = $null
    bedrockModelId = $null
    googleClientIdMasked = $null
    googleClientSecretArn = $null
    googleClientSecretSource = $null
    notificationFromEmail = $null
    alarmEmail = $null
    gitCommit = $null
    gitDirty = $null
    distributionDomain = $null
    frontendOrigin = $null
    oauthCallbackUrl = $null
    apiUrl = $null
    distributionId = $null
    uiBucketName = $null
    stateTableName = $null
    jobQueueUrl = $null
    health = $null
    logPath = $null
    error = $null
}

function Write-Log {
    param([Parameter(ValueFromPipeline = $true)][AllowEmptyString()][string]$Message)
    process {
        $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
        Write-Host $line
        if ($script:LogPath) {
            try {
                [System.IO.File]::AppendAllText(
                    $script:LogPath, $line + [Environment]::NewLine, $script:Utf8NoBom)
            }
            catch { }
        }
    }
}

function Save-Summary {
    param([string]$Status)
    if (-not $script:SummaryPath) { return }
    $script:Summary.status = $Status
    $script:Summary.finishedAt = (Get-Date).ToString("o")
    $script:Summary.durationSeconds = [math]::Round(((Get-Date) - $script:StartedAt).TotalSeconds, 1)
    $script:Summary.logPath = $script:LogPath
    try {
        # BOM-free so other agents can json.load() it directly.
        $json = $script:Summary | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($script:SummaryPath, $json, $script:Utf8NoBom)
    }
    catch {
        Write-Host "WARNING: could not write $($script:SummaryPath): $($_.Exception.Message)"
    }
}

function Release-Lock {
    if ($script:LockStream) {
        try { $script:LockStream.Dispose() } catch { }
        $script:LockStream = $null
    }
    if ($script:LockPath -and (Test-Path -LiteralPath $script:LockPath)) {
        Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction SilentlyContinue
    }
}

function Fail {
    param([string]$Message, [int]$Code)
    Write-Log "ERROR: $Message"
    $script:Summary.error = $Message
    $script:Summary.exitCode = $Code
    Save-Summary -Status "failed"
    Release-Lock
    exit $Code
}

function Import-DotEnv {
    <# Load NAME=value lines from .env, keeping any variable the caller set. #>
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $loaded = 0
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch "^(?<name>[A-Za-z_][A-Za-z0-9_]*)=(?<value>.*)$") { continue }
        $name = $Matches.name
        if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name, "Process"))) {
            continue
        }
        Set-Item -Path "Env:$name" -Value $Matches.value
        $loaded++
    }
    return $loaded
}

function Get-EnvSetting {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) { return $null }
    return $value
}

function First-NonEmpty {
    param([string[]]$Values)
    foreach ($value in $Values) {
        if (-not [string]::IsNullOrWhiteSpace([string]$value)) { return [string]$value }
    }
    return $null
}

function Invoke-AwsJson {
    <# Run an aws CLI command and return @{ok;code;text;value} without throwing. #>
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & aws @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $text = (($output | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message }
        else { [string]$_ }
    }) -join "`n").Trim()
    $result = [ordered]@{ ok = ($code -eq 0); code = $code; text = $text; value = $null }
    if ($code -eq 0 -and $text) {
        try { $result.value = $text | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]$result
}

function Get-StackParameter {
    <# NoEcho parameters come back as "****"; treat those as unavailable. #>
    param([object]$Stack, [string]$Name)
    if (-not $Stack -or -not $Stack.Parameters) { return $null }
    foreach ($parameter in $Stack.Parameters) {
        if ($parameter.ParameterKey -ne $Name) { continue }
        $value = [string]$parameter.ParameterValue
        if ([string]::IsNullOrWhiteSpace($value) -or $value -eq "****") { return $null }
        return $value
    }
    return $null
}

function Get-StackOutput {
    param([object]$Stack, [string]$Name)
    if (-not $Stack -or -not $Stack.Outputs) { return $null }
    foreach ($output in $Stack.Outputs) {
        if ($output.OutputKey -eq $Name) { return [string]$output.OutputValue }
    }
    return $null
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    Fail "Required command '$Name' is not on PATH. $Hint" 2
}

function Get-HttpStatus {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30 `
            -MaximumRedirection 5 -ErrorAction Stop
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception -and $_.Exception.Response) {
            try { return [int]$_.Exception.Response.StatusCode } catch { }
        }
        return $null
    }
}

function Wait-ForUrl {
    param([string]$Url, [int]$Attempts, [int]$DelaySeconds)
    $last = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $last = Get-HttpStatus -Url $Url
        if ($last -ge 200 -and $last -lt 400) { return $last }
        $observed = if ($last) { "$last" } else { "no response" }
        Write-Log "  attempt $attempt/$Attempts for $Url : $observed"
        if ($attempt -lt $Attempts) { Start-Sleep -Seconds $DelaySeconds }
    }
    return $last
}

function Invoke-DeployScript {
    <#
      Run scripts/deploy.ps1 in a child PowerShell so the exit code is
      unambiguous and -NonInteractive guarantees it can never block on a
      prompt. Output is streamed line by line into the run log.
    #>
    param([string[]]$Arguments)
    $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $shell)) { $shell = "powershell.exe" }
    Write-Log "Running scripts/deploy.ps1 in a non-interactive child shell"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File (Join-Path $PSScriptRoot "deploy.ps1") @Arguments 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Log $_.Exception.Message }
                else { Write-Log ([string]$_) }
            }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return $code
}

function New-Lock {
    param([string]$Path, [bool]$Wait, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        try {
            $stream = [System.IO.File]::Open(
                $Path,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $payload = "pid=$PID started=$((Get-Date).ToString('o'))"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
            $stream.SetLength(0)
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush()
            return $stream
        }
        catch [System.IO.IOException] {
            if (-not $Wait -or (Get-Date) -gt $deadline) { return $null }
            Write-Log "Another deployment holds $Path; waiting up to $TimeoutSeconds seconds"
            Start-Sleep -Seconds 5
        }
    }
}

try {
    # ------------------------------------------------------------------ paths
    $deployScript = Join-Path $PSScriptRoot "deploy.ps1"
    if (-not (Test-Path -LiteralPath $deployScript)) {
        throw "Missing $deployScript"
    }
    if (-not $LogDirectory) {
        $LogDirectory = Join-Path $script:Root "temp/deploy"
    }
    if (-not (Test-Path -LiteralPath $LogDirectory)) {
        New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    }

    # ------------------------------------------------------------ environment
    $envFile = Join-Path $script:Root ".env"
    $loadedFromEnvFile = Import-DotEnv -Path $envFile

    # Fresh shells may not yet have the installers' PATH entries.
    if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
        $sam = "C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd"
        if (Test-Path $sam) { $env:Path = (Split-Path $sam) + ";" + $env:Path }
    }
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
        if (Test-Path $aws) { $env:Path = (Split-Path $aws) + ";" + $env:Path }
    }

    # -------------------------------------------------------------- settings
    $stackFromEnv = Get-EnvSetting "GLIDE_STACK_NAME"
    $resolvedStackName = First-NonEmpty @($StackName, $stackFromEnv, "glide")
    $deployStamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $script:LogPath = Join-Path $LogDirectory "deploy-$resolvedStackName-$deployStamp.log"
    $script:SummaryPath = Join-Path $LogDirectory "last-deploy.json"

    Write-Log "Glide agent deployment"
    Write-Log "Repository: $($script:Root)"
    Write-Log "Log: $($script:LogPath)"
    if ($loadedFromEnvFile -gt 0) {
        Write-Log "Loaded $loadedFromEnvFile setting(s) from .env (caller-provided variables kept)"
    }
    else {
        Write-Log "No .env values loaded; using parameters and the process environment"
    }

    $required = @("aws")
    if (-not $VerifyOnly) {
        $required += @("sam", "uv")
        if (-not $SkipFrontendBuild) { $required += @("node", "npm") }
    }
    foreach ($tool in $required) { Assert-Command -Name $tool -Hint "See docs/setup.md." }

    $resolvedRegion = First-NonEmpty @($Region, (Get-EnvSetting "AWS_REGION"), "eu-west-1")
    $resolvedProfile = First-NonEmpty @($Profile, (Get-EnvSetting "AWS_PROFILE"), "glide")
    $awsBase = @("--region", $resolvedRegion, "--profile", $resolvedProfile)

    Write-Log "Checking AWS identity for profile '$resolvedProfile' in $resolvedRegion"
    $identity = Invoke-AwsJson -Arguments ($awsBase + @("sts", "get-caller-identity", "--output", "json"))
    if (-not $identity.ok) {
        Fail "aws sts get-caller-identity failed. Check credentials for profile '$resolvedProfile'. $($identity.text)" 2
    }
    $accountId = [string]$identity.value.Account
    Write-Log "Authenticated as $($identity.value.Arn) (account $accountId)"

    $stackResult = Invoke-AwsJson -Arguments (
        $awsBase + @("cloudformation", "describe-stacks", "--stack-name", $resolvedStackName,
                     "--query", "Stacks[0]", "--output", "json")
    )
    $existingStack = $null
    if ($stackResult.ok) {
        $existingStack = $stackResult.value
    }
    elseif ($stackResult.text -match "does not exist") {
        Write-Log "Stack '$resolvedStackName' does not exist in $resolvedRegion yet; this is a first deployment"
    }
    else {
        Fail "aws cloudformation describe-stacks failed for '$resolvedStackName'. $($stackResult.text)" 2
    }

    if ($existingStack) {
        $stackArnMatch = [regex]::Match([string]$existingStack.StackId, "^arn:[^:]+:cloudformation:[^:]+:(?<account>\d+):stack/")
        if ($stackArnMatch.Success -and $stackArnMatch.Groups["account"].Value -ne $accountId) {
            Fail ("Stack '$resolvedStackName' belongs to account $($stackArnMatch.Groups['account'].Value) " +
                  "but the '$resolvedProfile' profile authenticates as $accountId.") 2
        }
        Write-Log "Existing stack status: $($existingStack.StackStatus)"
        if ($existingStack.StackStatus -match "IN_PROGRESS|ROLLBACK_COMPLETE|ROLLBACK_FAILED|UPDATE_ROLLBACK_FAILED") {
            Fail "Stack '$resolvedStackName' is $($existingStack.StackStatus) and cannot be updated. Resolve it in CloudFormation first." 2
        }
    }
    if ($VerifyOnly -and -not $existingStack) {
        Fail "-VerifyOnly needs an existing stack, but '$resolvedStackName' was not found in $resolvedRegion." 2
    }

    if (-not $PSBoundParameters.ContainsKey("NotificationFromEmail")) {
        $NotificationFromEmail = First-NonEmpty @(
            (Get-EnvSetting "GLIDE_NOTIFICATION_FROM"),
            (Get-StackParameter $existingStack "NotificationFromEmail")
        )
        if (-not $NotificationFromEmail) { $NotificationFromEmail = "" }
    }
    if (-not $PSBoundParameters.ContainsKey("AlarmEmail")) {
        $AlarmEmail = First-NonEmpty @(
            (Get-EnvSetting "GLIDE_ALARM_EMAIL"),
            (Get-StackParameter $existingStack "AlarmEmail")
        )
        if (-not $AlarmEmail) { $AlarmEmail = "" }
    }

    $resolvedStage = First-NonEmpty @(
        $Stage,
        (Get-EnvSetting "GLIDE_STAGE"),
        (Get-StackParameter $existingStack "Stage"),
        "prod"
    )
    $resolvedBedrock = First-NonEmpty @(
        $BedrockModelId,
        (Get-EnvSetting "BEDROCK_MODEL_ID"),
        (Get-StackParameter $existingStack "BedrockModelId")
    )
    $resolvedClientId = First-NonEmpty @(
        $GoogleClientId,
        (Get-EnvSetting "GOOGLE_CLIENT_ID"),
        (Get-StackParameter $existingStack "GoogleClientId")
    )
    $resolvedSecretArn = First-NonEmpty @(
        $GoogleClientSecretArn,
        (Get-EnvSetting "GOOGLE_CLIENT_SECRET_ARN"),
        (Get-StackParameter $existingStack "GoogleClientSecretArn")
    )
    $resolvedSecretName = First-NonEmpty @(
        $GoogleSecretName,
        (Get-EnvSetting "GLIDE_GOOGLE_SECRET_NAME"),
        "glide/google-client-secret"
    )
    $secretValue = Get-EnvSetting "GOOGLE_CLIENT_SECRET"

    if (-not $VerifyOnly) {
        if (-not $resolvedBedrock) {
            Fail "No Bedrock model id. Set BEDROCK_MODEL_ID in .env or pass -BedrockModelId." 2
        }
        if (-not $resolvedClientId) {
            Fail ("No Google client id. Set GOOGLE_CLIENT_ID in .env or pass -GoogleClientId " +
                  "(the stack parameter is NoEcho, so it cannot be read back from CloudFormation).") 2
        }
        if (-not $resolvedSecretArn -and -not $secretValue) {
            Fail ("No Google client secret available. Set GOOGLE_CLIENT_SECRET in .env, or pass " +
                  "-GoogleClientSecretArn for a secret that already exists in Secrets Manager. " +
                  "This script never prompts.") 2
        }
    }

    $script:Summary.stackName = $resolvedStackName
    $script:Summary.region = $resolvedRegion
    $script:Summary.stage = $resolvedStage
    $script:Summary.profile = $resolvedProfile
    $script:Summary.bedrockModelId = $resolvedBedrock
    $script:Summary.notificationFromEmail = $NotificationFromEmail
    $script:Summary.alarmEmail = $AlarmEmail
    $script:Summary.googleClientSecretArn = $resolvedSecretArn
    if ($resolvedClientId) {
        # Public identifier, but NoEcho in the template; log only a prefix.
        $prefixLength = [math]::Min(12, $resolvedClientId.Length)
        $script:Summary.googleClientIdMasked = $resolvedClientId.Substring(0, $prefixLength) + "..."
    }
    if ($resolvedSecretArn) { $script:Summary.googleClientSecretSource = "existing-arn" }
    elseif ($secretValue) { $script:Summary.googleClientSecretSource = "env-value-to-secrets-manager" }
    else { $script:Summary.googleClientSecretSource = "none-needed" }

    $gitCommit = "unknown"
    $gitDirty = $null
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $gitCommit = (& git -C $script:Root rev-parse --short HEAD 2>$null | Select-Object -First 1)
        $gitDirty = [bool](& git -C $script:Root status --porcelain 2>$null | Select-Object -First 1)
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($gitCommit) { $script:Summary.gitCommit = [string]$gitCommit }
    $script:Summary.gitDirty = $gitDirty

    Write-Log "Plan"
    Write-Log "  stack      $resolvedStackName ($resolvedRegion, profile $resolvedProfile)"
    Write-Log "  stage      $resolvedStage"
    Write-Log "  model      $resolvedBedrock"
    if ($resolvedSecretArn) {
        Write-Log "  secret     existing ARN $resolvedSecretArn"
    }
    elseif ($secretValue) {
        Write-Log "  secret     value from .env -> Secrets Manager '$resolvedSecretName' (never on a command line)"
    }
    else {
        Write-Log "  secret     not needed for this run"
    }
    $notificationLabel = if ($NotificationFromEmail) { $NotificationFromEmail } else { "(disabled)" }
    $alarmLabel = if ($AlarmEmail) { $AlarmEmail } else { "(no subscription)" }
    Write-Log "  notify     $notificationLabel"
    Write-Log "  alarms     $alarmLabel"
    Write-Log "  commit     $gitCommit (dirty=$gitDirty)"

    if ($DryRun) {
        Write-Log "Dry run complete: configuration and preflight checks passed; nothing was built or deployed."
        $script:Summary.exitCode = 0
        Save-Summary -Status "dry-run"
        exit 0
    }

    if (-not $VerifyOnly) {
        $script:LockPath = Join-Path $LogDirectory "deploy.lock"
        Write-Log "Acquiring $($script:LockPath)"
        $script:LockStream = New-Lock -Path $script:LockPath -Wait $WaitForLock.IsPresent `
            -TimeoutSeconds $LockTimeoutSeconds
        if (-not $script:LockStream) {
            Fail ("Another deployment is already running (lock: $($script:LockPath)). " +
                  "Wait for it to finish, or rerun with -WaitForLock.") 2
        }

        $deployArgs = @(
            "-StackName", $resolvedStackName,
            "-Stage", $resolvedStage,
            "-Region", $resolvedRegion,
            "-BedrockModelId", $resolvedBedrock,
            "-GoogleClientId", $resolvedClientId,
            "-GoogleSecretName", $resolvedSecretName,
            "-NotificationFromEmail", $NotificationFromEmail,
            "-AlarmEmail", $AlarmEmail,
            "-Profile", $resolvedProfile,
            "-NonInteractive"
        )
        if ($resolvedSecretArn) { $deployArgs += @("-GoogleClientSecretArn", $resolvedSecretArn) }
        if ($SkipFrontendBuild) { $deployArgs += "-SkipFrontendBuild" }
        if ($SkipLambdaBuild) { $deployArgs += "-SkipLambdaBuild" }

        $exitCode = Invoke-DeployScript -Arguments $deployArgs
        if ($exitCode -ne 0) {
            Fail "scripts/deploy.ps1 failed with exit code $exitCode. Full output: $($script:LogPath)" 1
        }
    }

    # ---------------------------------------------------------- verification
    Write-Log "Reading stack outputs"
    $stackResult = Invoke-AwsJson -Arguments (
        $awsBase + @("cloudformation", "describe-stacks", "--stack-name", $resolvedStackName,
                     "--query", "Stacks[0]", "--output", "json")
    )
    if (-not $stackResult.ok) {
        Fail "Could not read stack outputs after deploying. $($stackResult.text)" 1
    }
    $stack = $stackResult.value
    $domain = Get-StackOutput $stack "DistributionDomainName"
    if (-not $domain) { Fail "Stack '$resolvedStackName' did not report DistributionDomainName." 1 }
    $origin = "https://$domain"

    $script:Summary.distributionDomain = $domain
    $script:Summary.frontendOrigin = $origin
    $script:Summary.oauthCallbackUrl = "$origin/api/auth/google/callback"
    $script:Summary.distributionId = Get-StackOutput $stack "DistributionId"
    $script:Summary.uiBucketName = Get-StackOutput $stack "UiBucketName"
    $script:Summary.apiUrl = Get-StackOutput $stack "ApiUrl"
    $script:Summary.stateTableName = Get-StackOutput $stack "StateTableName"
    $script:Summary.jobQueueUrl = Get-StackOutput $stack "JobQueueUrl"

    if ($SkipHealthCheck) {
        Write-Log "Health checks skipped (-SkipHealthCheck)"
    }
    else {
        Write-Log "Verifying $origin/"
        $siteStatus = Wait-ForUrl -Url "$origin/" -Attempts $HealthCheckAttempts `
            -DelaySeconds $HealthCheckDelaySeconds
        if (-not ($siteStatus -ge 200 -and $siteStatus -lt 400)) {
            Fail "The site at $origin/ did not answer successfully (last status: $(if ($siteStatus) { $siteStatus } else { 'no response' }))." 1
        }

        $healthUrl = "$origin/api/health"
        Write-Log "Verifying $healthUrl"
        $healthStatus = Wait-ForUrl -Url $healthUrl -Attempts $HealthCheckAttempts `
            -DelaySeconds $HealthCheckDelaySeconds
        $healthBody = $null
        if ($healthStatus -eq 200) {
            try {
                $healthBody = (Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 30).Content |
                    ConvertFrom-Json
            }
            catch {
                $healthBody = $null
            }
        }
        $script:Summary.health = [ordered]@{
            url = $healthUrl
            statusCode = $healthStatus
            status = if ($healthBody) { [string]$healthBody.status } else { $null }
            mode = if ($healthBody) { [string]$healthBody.mode } else { $null }
            version = if ($healthBody) { [string]$healthBody.version } else { $null }
        }
        if ($healthStatus -ne 200) {
            Fail "The API health check at $healthUrl did not return 200 (last status: $(if ($healthStatus) { $healthStatus } else { 'no response' }))." 1
        }
        Write-Log "Health: $($healthBody.status) (mode $($healthBody.mode), version $($healthBody.version))"
    }

    $script:Summary.exitCode = 0
    $finalStatus = if ($VerifyOnly) { "verified" } else { "deployed" }
    $finalMessage = if ($VerifyOnly) { "Verified the live deployment" } else { "Deployed and verified" }
    Write-Log "$finalMessage : $origin"
    Write-Log "OAuth callback to register in Google Cloud: $origin/api/auth/google/callback"
    Write-Log "Summary: $($script:SummaryPath)"
    Save-Summary -Status $finalStatus
    Release-Lock
    exit 0
}
catch {
    $message = $_.Exception.Message
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        $message = "$message (line $($_.InvocationInfo.ScriptLineNumber))"
    }
    Fail "Deployment aborted: $message" 1
}
