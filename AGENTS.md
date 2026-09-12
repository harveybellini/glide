# Working in this repository

Repo-specific rules for every agent (and human) working in this checkout. They
sit on top of whatever global guidance you already follow.

## Version and changelog before every push

Glide deploys as two halves - a static bundle on CloudFront and a separate
Lambda API - and the page reports both in its footer. That footer is the
version monitor: it names the build the tab is running, the build that is
deployed, and the API version, and it offers a reload when they drift. The
monitor is only honest if the repository moves the version deliberately.

Before `git push`:

1. Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) - under
   `### Added`, `### Changed`, `### Fixed`, or `### Removed` - that says what
   changed in the user's words. One sentence is enough.
2. When the change is deployable, release those entries:
   `uv run python scripts/version.py bump minor` (or `patch` / `major`). The
   script rewrites `VERSION`, `pyproject.toml`, `backend/glide/__init__.py`,
   `uv.lock`, `frontend/package.json`, and its lockfile, and drains
   `[Unreleased]` into a dated section.
3. Run `uv run python scripts/version.py check`. It must report no problems.

Three gates enforce this: `.git/hooks/pre-push` (install once with
`powershell -ExecutionPolicy Bypass -File scripts/install-git-hooks.ps1`), the
version step in CI, and
`tests/unit/test_version_system.py`. Never hand-edit one version declaration on
its own; `scripts/version.py` exists so the seven cannot drift. `git push
--no-verify` is an emergency hatch only - say why in the commit message.

## Definition of done

- `uv run pytest -q` and `uv run ruff check .` pass.
- Frontend changes: `npm run typecheck` and `npm run build` pass in
  `frontend/`; run `npm run e2e` when behaviour the Playwright specs cover has
  changed.
- Docs that quote behaviour (`README.md`, `docs/`, `submission/`) are updated in
  the same change, not afterwards.

## Where things live

- `backend/glide/` - API, domain logic, adapters, jobs, and the agent runner.
- `frontend/` - the React interface; `src/styles.css` holds the design tokens
  described in `docs/frontend-style-guide.md`.
- `infra/template.yaml` - the SAM stack; `scripts/deploy.ps1` deploys it.
- `scripts/` - setup, deployment, and verification tooling, including
  `version.py` and the git hook in `scripts/hooks/`.
- `docs/` - architecture, setup, decisions, and progress notes.
- `submission/` - the Devpost artifacts and screenshots.

## Never

- Commit credentials, tokens, `secrets/`, `.env`, or `private.md`.
- Claim a result the repository cannot reproduce; include the command that
  proves it or say it is unverified.
