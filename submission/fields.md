# Devpost form fields

Owner-only entries are marked **[OWNER]**. Never invent these values.

| Field | Content / status |
| --- | --- |
| Title | Glide |
| Subtitle | A calendar that reserves the time to get there |
| Track | **[OWNER]** confirm "Everyday Agents" |
| Cover image | [OWNER] thumbnail from `submission/screenshots/` |
| One-liner | Turn a day of appointments into a day with time to get there. |
| Overview | Glide reads your calendar, estimates driving time, and reserves travel blocks directly in your primary calendar. When a journey cannot fit, it explains the shortfall instead of guessing. |
| The problem | Gaps between appointments hide the travel they need; one change can silently invalidate the whole day. |
| The solution | A bounded agent that inspects each changed day, requests real routes, checks feasibility, and writes only its own marked travel blocks to the primary calendar. |
| Project story | `devpost-story.md` |
| Built With | `strands-agents-sdk`, `python`, `amazon-bedrock`, `amazon-location-service`, `google-calendar-api`, `aws-lambda`, `amazon-eventbridge`, `amazon-sqs`, `amazon-dynamodb`, `amazon-s3`, `amazon-cloudfront`, `amazon-api-gateway`, `react`, `typescript`, `fastapi`, `aws-sam` |
| Public repository | https://github.com/harveybellini/glide (public, default branch `main`, MIT detected, CI green, loads signed out) |
| Video | [OWNER] public YouTube/Vimeo URL, ≤5 minutes |
| Live demo URL | https://d3tvxy281s2u11.cloudfront.net (root and `/api/health` verified; re-verify signed-out at submission) |
| AWS Builder ID | [OWNER] real identifier |
| Screenshots | 4 × 3:2 gallery from `submission/screenshots/`: `01-landing`, `02-timeline`, `03-decision`, `04-activity`; `05`-`08` hold the mobile and full-page design captures |
| Eligibility/profile | [OWNER] checked against current official rules |

## Submission evidence checklist

- [ ] Devpost shows **Submitted** with a receipt, not a saved draft.
- [x] Repo loads signed out; MIT license detected; no secrets committed (tracked-file secret scan).
- [ ] Live URL labeled with accurate synthetic/live wording.
- [ ] Optional AWS Builder article public before the deadline.
