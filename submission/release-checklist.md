# Release checklist

Checked items require captured evidence; leave unchecked rather than claim.

## P0 functional gates

- [x] Canonical sample: feasible block + quantified conflict
- [x] Move appointment → blocks update, conflict resolves
- [x] Repeat run → no duplicates (`unchanged` receipts)
- [x] Delete appointment → orphan removed, direct journey recomputed
- [x] Skip decision persists across runs
- [x] Decision answers trigger a fresh bounded run
- [x] Manual edit of a managed block → decision, no overwrite
- [x] Manual deletion → skip, never immediately recreated
- [x] ETag conflict fails safely for retry
- [x] Source change before a write requeues with fresh plans
- [x] Unresolved journey suspends the downstream chain
- [x] Disconnect pauses, cleans up owned blocks, and revokes credentials
- [x] Durable sample sessions across worker instances
- [x] Decision notification policy: once-only send, retry on failure, and a
      signed-in-only settings guard covered by tests
- [ ] Real Google read/write with evidence (needs credentials)
- [ ] Real SES decision email delivered once; an unresolved repeat sends
      nothing (needs a verified sending identity)
- [x] Real Amazon Location place + route (live smoke, 513 s driving estimate)
- [x] Real Bedrock Strands tool call (live smoke, `create feasible destination`)
- [x] Ten consecutive canonical runs (fixture providers, deterministic runner)
- [ ] Ten consecutive runs against live providers, identified

## Quality gates

- [x] `uv run pytest` green
- [x] `uv run ruff check .` clean
- [x] Frontend `npm ci`, `typecheck`, `build` green
- [x] Playwright judge-path e2e journey green (plus accessibility smoke)
- [x] `scripts/validate_template.py` green
- [x] Clean-copy setup trial (`scripts/clean_setup_trial.ps1`) green
- [x] `sam validate --lint` green; Linux Lambda bundle built
- [ ] Two unfamiliar testers resolve a conflict unassisted
- [x] AWS stack deployed (`glide`, eu-west-1); one deployed sample check
      completed through SQS/worker/Bedrock (one block, one decision)
- [ ] Deployed move/recheck idempotency and scheduled browser-closed run observed

## Release artifacts

- [x] `docs/architecture.svg` + `docs/architecture.png` exported
- [x] Git repository initialized with an initial commit (secrets audited out)
- [ ] Public remote created and pushed; repo loads signed out
- [x] Deployed URL serves and `/api/health` returns ok (re-verify signed-out at
      submission)
- [x] Four 3:2 gallery screenshots (landing, timeline, decision, activity)
- [ ] Public video ≤ 5 minutes, public URL verified
- [ ] `submission/fields.md` owner values filled, no invented identifiers
- [ ] Video shows the decision email arriving and the deep link opening the
      highlighted card
- [ ] Roadmap names the Slack direct-message adapter as future work
- [ ] Devpost shows **Submitted** with receipt
- [ ] Optional AWS Builder article public (with "Agents for Humans" in title)
- [ ] Judge access maintained through 9 October 2026, 01:00 BST

## Security and privacy

- [x] No tokens/credentials in source or `.env.example`
- [x] Source appointments never written by the executor
- [x] Malicious event titles remain inert data
- [x] OAuth scopes limited to `openid`, `email`, `calendar.events.owned`
- [x] Receipts carry TTL; sample snapshots expire after 24 hours
- [x] Bedrock IAM scoped to the tested foundation model and inference profiles
      (streaming and non-streaming)
- [x] Spending limit agreed at USD 75; September usage observed at USD 0.00
