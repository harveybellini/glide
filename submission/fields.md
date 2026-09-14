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
| The solution | A bounded agent that inspects each changed day, requests real routes, checks feasibility, and writes only its own marked travel blocks to the primary calendar. When a journey cannot fit it sends one Amazon SES email linking to the decision card, and stays silent otherwise. |
| Roadmap (slides/video) | With more time: a Slack MCP server that delivers the same decision card as a direct message with inline approve/skip actions, so the notification is seamless and the user never has to open the app to answer. The once-only notification seam is already transport-agnostic — one decision mark, one adapter interface. |
| Project story | `devpost-story.md` |
| Built With | `strands-agents-sdk`, `python`, `amazon-bedrock`, `amazon-location-service`, `google-calendar-api`, `amazon-ses`, `aws-lambda`, `amazon-eventbridge`, `amazon-sqs`, `amazon-dynamodb`, `amazon-s3`, `amazon-cloudfront`, `amazon-api-gateway`, `react`, `typescript`, `fastapi`, `aws-sam` |
| Public repository | https://github.com/harveybellini/glide (public, default branch `main`, MIT detected, CI green, loads signed out) |
| Video | [OWNER] public YouTube/Vimeo URL, ≤5 minutes |
| Live demo URL | https://d3tvxy281s2u11.cloudfront.net (root and `/api/health` verified 11 Sep; the sample path is anonymous and simulated, the live path needs the owner's Google account - label it that way in the submission) |
| Live proof | Owner's Google test account: two `Travel / Glide` blocks written in 20.7 s, ten consecutive live runs 10.3-15.5 s, repeats `unchanged`, manual edit/deletion respected, scheduled run terminal, grant revoked on disconnect, and the decision email live on 12 Sep with durable `notified_at` stamps (`docs/evaluation.md`) |
| AWS Builder ID | [OWNER] real identifier |
| Screenshots | 4 × 3:2 gallery from `submission/screenshots/`: `01-landing`, `02-timeline`, `03-decision`, `04-activity`; `05`-`08` hold the mobile and full-page design captures |
| Eligibility/profile | [OWNER] checked against current official rules |

## Submission evidence checklist

- [ ] Video includes the decision email beat: arrival, deep link to the card,
      and an unresolved repeat sending no second email.
- [ ] Devpost shows **Submitted** with a receipt, not a saved draft.
- [x] Repo loads signed out; MIT license detected; no secrets committed (tracked-file secret scan).
- [x] Live URL labeled with accurate synthetic/live wording (sample = simulated routes; live path = owner's Google account).
- [ ] Optional AWS Builder article public before the deadline.
