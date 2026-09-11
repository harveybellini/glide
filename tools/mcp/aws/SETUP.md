# AWS MCP servers

Official awslabs MCP servers (CloudWatch, DynamoDB, SNS/SQS, Lambda) for
reading deployed Glide logs, session/run rows, and queue state without leaving
the agent. The committed config pins each image to a public ECR digest, so an
upstream tag move cannot change what runs locally.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File tools/mcp/aws/install-aws-mcps.ps1
```

Requires Docker Desktop to be running and network access to `public.ecr.aws`.
Refresh the pinned digests deliberately with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/refresh-mcp-pins.ps1
```

## Credentials

Use a dedicated, least-privilege AWS profile — ideally an SSO profile with
read-only access — and do not point these servers at an administrative
identity. The config forwards only `AWS_PROFILE` and `AWS_REGION` and mounts
the credential directory read-only:

```toml
args = ["run", "-i", "--rm", "-e", "AWS_PROFILE", "-e", "AWS_REGION", "-v", "C:/Users/<you>/.aws:/app/.aws:ro", "public.ecr.aws/...@sha256:..."]
env_vars = ["AWS_PROFILE", "AWS_REGION"]
```

Example one-time setup with IAM Identity Center (SSO):

```powershell
aws configure sso --profile glide-readonly   # pick a read-only role/permission set
aws sso login --profile glide-readonly
$env:AWS_PROFILE = "glide-readonly"
$env:AWS_REGION = "eu-west-2"                # Glide's deployment region
```

Minimum useful actions for routine Glide work (attach to the profile's
permission set instead of the managed `ReadOnlyAccess` policy if you want the
smallest surface): `logs:FilterLogEvents`, `logs:DescribeLogGroups`,
`dynamodb:Scan`, `dynamodb:Query`, `dynamodb:DescribeTable`,
`sqs:GetQueueAttributes`, `sqs:ReceiveMessage`, `sqs:ListQueues`,
`lambda:ListFunctions`, `lambda:GetFunction`.

If you cannot use SSO, export *temporary* credentials from a scoped role
(`aws configure export-credentials --profile glide-readonly --format env`)
rather than long-lived keys, and add them to the config's `env_vars` for the
session only.

## Verify

1. Restart Codex, then run `codex mcp list` and confirm the four servers
   appear.
2. Ask the agent to list recent errors from a Glide log group, or list the
   DynamoDB tables in `eu-west-2`. Mutating AWS tools require approval
   (`default_tools_approval_mode = "writes"`).

## Safety

- Set `enabled = false` in `.codex/config.toml` for any server you are not
  actively using, and keep the profile read-only.
- Never commit credentials; the config references only profile names and
  paths.
- Never replace a digest pin with `:latest`. Use
  `scripts/refresh-mcp-pins.ps1`, review the diff, and commit.
