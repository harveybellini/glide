# AWS MCP Server (Agent Toolkit for AWS)

One managed, remote MCP server, reached through AWS's official SigV4 proxy
(`mcp-proxy-for-aws-cli`). It replaces the four legacy awslabs Docker servers
(`cloudwatch-logs`, `dynamodb`, `sqs`, `lambda`) that this project used before
11 September 2026:

| | Legacy Docker servers | AWS MCP Server |
| --- | --- | --- |
| Coverage | Four fixed services, read-mostly | Every AWS API through `aws___run_script` (sandboxed Python with `call_boto3`) |
| Serverless debugging | None | `diagnose`, `search_logs`, `get_live_config`, `get_recent_changes` for a Lambda and its SQS/SNS/DynamoDB/API Gateway/EventBridge/Step Functions resources |
| AWS documentation | None | `search_documentation`, `read_documentation`, `retrieve_skill`, `list_regions`, `get_regional_availability` |
| Local runtime | Docker Desktop must be running | `uvx` only (no daemon) |
| Version pinning | OCI digest per image | exact PyPI version in the config |

Upstream reasoning: the awslabs repository now points at the Agent Toolkit for
AWS as the successor to its MCP servers, and tells clients that use the managed
server to remove the older AWS MCP servers so agents are not confused by
overlapping tools.

## Endpoints and regions

`https://aws-mcp.eu-central-1.api.aws/mcp` (only `eu-central-1` and
`us-east-1` exist; verified 11 September 2026). The endpoint region is also
the **SigV4 signing region**, which is why the config must not pass
`--region`. The *profile's* region (`eu-west-1` for Glide) becomes the
session's default AWS region, so Glide resources are addressed without
repeating the region in every call.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-mcps.ps1
```

That pre-warms the pinned `mcp-proxy-for-aws-cli` version through `uvx` and
renders `.codex/config.toml`. `uv`/`uvx` must be on PATH; Docker is no longer
required for the AWS server. Refresh the pin deliberately with
`scripts/refresh-mcp-pins.ps1` (it prints the available version and only
rewrites the pinned files with `-Apply`).

## Credentials

The config forwards `AWS_PROFILE` and `AWS_REGION` from the environment that
starts Codex; no keys or tokens are stored in the repository. The proxy reads
fresh credentials from the AWS chain on every request, so a re-login takes
effect without restarting Codex.

```powershell
aws login --profile glide            # refresh the interactive sign-in session
[Environment]::SetEnvironmentVariable('AWS_PROFILE', 'glide', 'User')
[Environment]::SetEnvironmentVariable('AWS_REGION', 'eu-west-1', 'User')
```

Prefer a dedicated, least-privilege profile (for example `glide-readonly`)
over an administrative or root identity. The read-only profile only needs the
API actions you actually want the agent to use. If you cannot use SSO, export
*temporary* credentials from a scoped role rather than long-lived keys.

Two AWS-side controls are worth knowing before pointing this at a real
account:

- `aws:CalledViaAWSMCP = "aws-mcp.amazonaws.com"` and
  `aws:ViaAWSMCPService = "true"` are condition keys that apply **only** to
  calls made through AWS MCP Server. An explicit `Deny` on destructive actions
  under those conditions leaves interactive and CLI access untouched.
- The proxy's `--read-only` flag hides every non-read-only tool, which also
  hides `aws___run_script` - that is, all AWS API access. It is not a useful
  guard here; scope the IAM identity instead.

### OAuth alternative (no proxy, no local credentials)

AWS supports connecting Codex directly with OAuth 2.1 through AWS Sign-in:

```powershell
codex mcp add aws-mcp --url https://aws-mcp.us-east-1.api.aws/mcp?oauth=initialize
```

The IAM principal needs `signin:AuthorizeOAuth2Access` and
`signin:CreateOAuth2Token` (managed policy `AWSMCPSignInOAuthAccessPolicy`),
and the browser sign-in happens on first tool use. It cannot set a default
session region, so it is a fallback rather than the primary setup.

## Tools

| Tool | Use |
| --- | --- |
| `aws___run_script` | Sandboxed Python with `call_boto3`: logs, DynamoDB, SQS, CloudFormation, CloudFront, Budgets, Secrets Manager metadata |
| `aws___get_presigned_url` | S3 upload/download URLs for the deploy scripts |
| `aws___get_tasks` | Poll long-running tool calls |
| `aws___search_documentation` / `aws___read_documentation` | Current AWS docs and API references |
| `aws___retrieve_skill` | AWS-authored workflows and troubleshooting skills |
| `aws___list_regions` / `aws___get_regional_availability` | Region and feature availability |

The serverless diagnostics capability is read-only and scoped to the caller's
own account. `aws___run_script` is *not* annotated read-only, so
`default_tools_approval_mode = "writes"` makes Codex ask for approval on every
API call it makes.

## Verify

1. Restart Codex, then run `codex mcp list` and confirm `aws-mcp` appears.
2. Ask the agent to list the `glide` CloudFormation stack's outputs or the
   recent errors in a Glide log group; approve the prompted `aws___run_script`
   call.
3. If tool calls fail with credential errors, refresh the session
   (`aws login --profile glide`) and retry; the proxy re-reads credentials on
   every request.

## Quotas

Authenticated requests: 10 per second per account per region, plus
per-account and per-user session limits. Unauthenticated requests: 5 per
second per source IP and knowledge tools only - the API and script tools
require authentication. Requests over quota return HTTP 429.

## Safety

- Keep `AWS_PROFILE` pointed at a scoped identity; the server executes AWS API
  calls with exactly those permissions, and every call is visible in
  CloudTrail.
- Set `enabled = false` in `.codex/config.toml` for any server you are not
  actively using.
- Never commit credentials; the config references only environment-variable
  names.
- Never replace the pinned proxy version with `@latest`. Run
  `scripts/refresh-mcp-pins.ps1`, review the diff, and commit.
