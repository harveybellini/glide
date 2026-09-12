# MCP servers for Glide development

Project-scoped Model Context Protocol (MCP) servers for Codex. The merged
config is staged at [tools/mcp/codex-config.toml](../tools/mcp/codex-config.toml)
and copied to `.codex/config.toml` by the installer (the agent sandbox treats
`.codex/` as read-only, so a normal terminal runs that final step). The project
config layer merges over `~/.codex/config.toml` and is loaded because the
project is trusted.

| Server | Purpose | Transport | Credentials |
| --- | --- | --- | --- |
| `playwright` | Drive the React UI, debug the Google OAuth flow, capture screenshots | local npm + Node | none |
| `github` | Issues, PRs, repository work | local npm + Node | `GITHUB_PERSONAL_ACCESS_TOKEN` (read-only scopes for routine work) |
| `google-calendar` | Inspect/verify real calendar blocks during provider testing | local npm + Node | OAuth desktop-client JSON (gitignored) |
| `aws-mcp` | Managed AWS MCP Server: every AWS API, Lambda/serverless diagnostics, AWS docs and skills | remote HTTPS + pinned SigV4 proxy (`uvx`) | `AWS_PROFILE` + `AWS_REGION` |

Every npm server is pinned to an exact version with a committed lockfile, and
the AWS proxy is pinned to an exact PyPI version.
`scripts/install-mcps.ps1` fails if a `latest` spec, an unpinned image
reference, or an unpinned proxy version reappears.

## Install

Network access, Node.js, and `uv`/`uvx` are required (the AWS server no longer
needs Docker). Run from the repository root in your own terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-mcps.ps1
```

The script installs the three npm servers under `tools/mcp/*`, pre-warms the
pinned `mcp-proxy-for-aws-cli` through `uvx`, copies the staged config to
`.codex/config.toml` (substituting `<REPO_ROOT>` and `<USER_HOME>`), and
validates every TOML file. Each server has its own README:

- [Playwright](../tools/mcp/playwright/SETUP.md) - browsers already installed,
  nothing else needed.
- [GitHub](../tools/mcp/github/SETUP.md) - fine-grained PAT scopes.
- [Google Calendar](../tools/mcp/google-calendar/SETUP.md) - Google Cloud
  OAuth desktop-client steps and first-run authentication.
- [AWS](../tools/mcp/aws/SETUP.md) - managed AWS MCP Server, its regions,
  least-privilege profile guidance, and the OAuth alternative.

## Credentials

Secrets are never stored in committed files. Two mechanisms are used:

- `env_vars` forwards variables (e.g. `GITHUB_PERSONAL_ACCESS_TOKEN`,
  `AWS_PROFILE`, `AWS_REGION`) from the environment Codex starts in.
- `env` sets a non-secret path to a gitignored file
  (`tools/mcp/google-calendar/credentials.json`, covered by the repo's
  `credentials*.json` ignore rule).

Set the AWS variables with the region Glide deploys to (`eu-west-1`) and a
dedicated read-only profile:

```powershell
aws configure sso --profile glide-readonly
aws sso login --profile glide-readonly
$env:AWS_PROFILE = "glide-readonly"
$env:AWS_REGION = "eu-west-1"
```

The AWS MCP Server endpoint lives in `eu-central-1`, which is also the SigV4
signing region; the proxy takes the signing region from the endpoint URL, so
`AWS_REGION` only selects the session's default working region. Raw long-lived
access keys are deliberately not part of this setup: the proxy uses whichever
least-privilege profile `AWS_PROFILE` names and re-reads credentials from the
AWS chain on every request.

## Verify

Restart Codex so the new project config loads, then:

```powershell
codex mcp list
```

All four servers should appear. In an interactive session `/mcp` shows the
connected servers. Suggested first checks:

- Playwright: open `http://localhost:5173` and take a screenshot.
- GitHub: list issues on a repository you can access.
- Google Calendar: authenticate once, then list today's events on the test
  calendar.
- AWS: list the `glide` stack's outputs, or the recent errors in a Glide log
  group. `aws___run_script` is not read-only, so Codex prompts before each
  AWS API call it makes.

## Safety

- The AWS server executes API calls with the permissions of the profile
  `AWS_PROFILE` names, and the proxy cannot make `aws___run_script` read-only.
  Use a scoped profile, consider the `aws:CalledViaAWSMCP` deny conditions, and
  set `enabled = false` for servers you are not using.
- Calendar, GitHub, and AWS tools are marked
  `default_tools_approval_mode = "writes"`, so every tool that is not marked
  read-only prompts for approval.
- GitHub tokens should be read-only for routine work; create a separate
  write-scoped token for sessions that actually need to edit.
- `required = false` everywhere, so a missing daemon, token, or package never
  blocks Codex startup.
- `tools/mcp/codex-config.toml` and the fragments under
  `tools/mcp/*/mcp-section.toml` use `<REPO_ROOT>` and `<USER_HOME>`
  placeholders. `scripts/install-mcps.ps1` substitutes the absolute paths when
  it renders `.codex/config.toml`, and `.codex/` stays untracked, so the
  published files contain no machine-specific paths.
