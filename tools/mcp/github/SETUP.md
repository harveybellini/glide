# GitHub MCP

GitHub MCP server for issues, pull requests, and repository work. Useful once
the Glide repo has a remote, and for working on other repositories.

> The pinned npm package (`@modelcontextprotocol/server-github@2025.4.8`) is
> archived upstream. It is pinned to an exact version and reviewed here, but
> treat it as a short-term dependency and prefer GitHub's own Go server
> (`ghcr.io/github/github-mcp-server`, pinned by digest) if you extend this
> setup.

## Install

```powershell
cd tools/mcp/github
npm install
```

## Credentials

Use a **fine-grained personal access token with read-only scopes** for routine
work. Write scopes are only needed when you actually intend to edit issues or
pull requests, and should live in a separate token.

Minimum scopes for routine (read-only) work:

- `Metadata: read` (mandatory, added automatically)
- `Contents: read`
- `Issues: read`
- `Pull requests: read`

Add `Contents: write`, `Issues: write`, or `Pull requests: write` only for a
token you create for a specific editing session.

Export the token in the environment Codex starts from:

```powershell
$env:GITHUB_PERSONAL_ACCESS_TOKEN = "github_pat_..."
```

The token is forwarded via `env_vars` in [mcp-section.toml](mcp-section.toml);
it is never stored in a committed file.

## Verify

1. Restart Codex, then run `codex mcp list` and confirm `github` appears.
2. Ask the agent to list open issues in a repository you can access.
