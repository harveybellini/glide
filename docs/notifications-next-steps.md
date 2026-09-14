# Decision notifications â€” steps to finish

Live since 12 September 2026. The `slyx.uk` domain identity is verified in
`eu-west-1`, the deployed worker carries `GLIDE_NOTIFICATION_FROM` set to the
verified sending address, and the connected Google tenant has
`notification_email` set with `notify_on_decisions: true`. Delivered decisions
carry a `notified_at` stamp in DynamoDB (observed 12 September at 13:37,
14:18, and 15:25 UTC), and SES reported seven messages sent in the last 24
hours.

The account is still in the SES sandbox (`ProductionAccessEnabled: false`), so
mail can only be sent to verified recipients. The sending domain is verified,
which covers the owner's own inbox; anyone else needs SES production access.
That is fine for the demo and for judges, who use the provider-free sample.
The remaining work is to capture the delivery evidence while recording the
video.

## What already exists

- `DecisionNotifier` protocol in `backend/glide/adapters/interfaces.py`; the
  SES implementation in `backend/glide/adapters/notifications.py`.
- Once-only policy in `backend/glide/domain/notifications.py`
  (`carry_notification_state`, `deliver_open_decisions`), stamping
  `Decision.notified_at`.
- `notification_email` and `notify_on_decisions` on `UserSettings`, editable
  in Settings, defaulted to the Google sign-in address, rejected for
  anonymous sample sessions.
- `NotificationFromEmail` deploy parameter, conditional
  `AWS::SES::EmailIdentity`, worker environment, and `ses:SendEmail` scoped to
  that identity in `infra/template.yaml`.
- Deep link `/?decision=<id>` that scrolls to and outlines the card.

## 1. Refresh AWS access (owner)

The `glide` profile's short-lived login session expires often, and refreshing
it from this workspace fails (`invalid_grant`, see
`docs/completion-progress.md`).

```powershell
aws login --profile glide
aws sts get-caller-identity --profile glide
```

## 2. Verify a sending identity in SES (done 12 September)

Status: done. `slyx.uk` is verified with `SendingEnabled: true` in
`eu-west-1`; the pending address identity is redundant because the verified
domain covers sending from it.

Verify the identity in the **same region as the stack** (`eu-west-1`).

- Fastest path: verify the owner's own address and use it as both
  `-NotificationFromEmail` and the Google test account's address. In the SES
  sandbox one verification then covers the sender and the recipient.
- Do **not** use an `@gmail.com` From address: Gmail publishes DMARC
  `p=reject` and SES mail with that From will be rejected. A verified domain
  with DKIM is the most deliverable option.
- Sandbox limits: verified recipients only, 200 messages/day, 1/second.
  Emailing anyone else needs production access, which AWS reviews with a
  24-hour SLA.

## 3. Deploy with notifications enabled (done 12 September)

Status: done. The stack parameter is set and the worker reports
`GLIDE_NOTIFICATION_FROM`; the commands below are kept as the reference for a
future deploy or rollback.

```powershell
scripts/deploy.ps1 -StackName glide -Stage prod -Region eu-west-1 `
  -BedrockModelId "eu.amazon.nova-2-lite-v1:0" `
  -GoogleClientId "<client id>" -GoogleClientSecret "<client secret>" `
  -NotificationFromEmail "glide@example.com"
```

Then confirm the worker actually received the configuration (the parameter
also works as a rollback switch: redeploy with it empty to disable sending
without touching code):

```powershell
aws lambda get-function-configuration --profile glide --region eu-west-1 `
  --function-name <WorkerFunctionName> `
  --query "Environment.Variables.GLIDE_NOTIFICATION_FROM"
```

## 4. Prove delivery (owner + browser)

Follow step 5 of `docs/live-proof-runbook.md`. In short:

1. Connect the Google test account and confirm Settings shows the sign-in
   address (it is filled in automatically).
2. Force one shortfall, then save the message id, headers, a screenshot of
   the arriving email, and a screenshot of the highlighted card the link
   opens. Record delivery latency from the run's `ended_at`.
3. Re-run the check with the conflict unresolved and confirm **no second
   email** arrives (this is the `notified_at` mark).
4. Clear the address in Settings and confirm a new decision sends nothing.

## 5. Definition of done

- [x] SES identity verified; `GLIDE_NOTIFICATION_FROM` present on the worker
- [x] Real decision emails sent; the owner confirmed the inbox is live
- [ ] Message id, headers, inbox screenshot, and the deep link opening the
      highlighted card captured while recording
- [ ] Unresolved repeat and cleared-address checks produce no further mail
- [ ] `submission/release-checklist.md` notification evidence items ticked
- [ ] Video beat recorded (email arriving, deep link opening the card)
- [ ] `docs/evaluation.md` measurements updated with the captured evidence
- [ ] Story/fields still match shipped behavior (SES in "Built With", Slack
      named only as future work)

## 6. Follow-ups (deliberately not done)

- **Slack adapter.** The same `DecisionNotifier` seam, one new class: per-user
  OAuth, a direct message carrying the decision card, inline approve/skip
  actions. Needs a Slack app, per-user identity storage, and a decision
  resolution endpoint callable from Slack. Named as future work in the story,
  demo script, Builder post, and `submission/fields.md`.
- **Quiet hours** in the user's time zone, and per-channel preferences
  (email now, SMS/Slack later).
- **SES configuration set** with an event destination for bounces and
  complaints, wired to a CloudWatch alarm, so delivery failures are visible.
- **"Send a test email" button** for recording and debugging. Needs SES
  permission on the API Lambda as well as the worker.
- **At-most-once refinement.** Today a crash between the result commit and
  the send can duplicate one message. Persisting a send-intent record before
  the call would close that window if it ever matters.
- **SMS via Amazon SNS** as a second channel (verified destination numbers,
  $1/month sandbox cap, support case to leave the sandbox).

## Notes

- Notification only runs on a committed `completed`/`needs_input` result, so
  failed runs never mail.
- Decision ids include the source revision, so a genuinely new conflict on a
  changed appointment notifies again; that is intended, not a duplicate.
- Changing the notification address bumps the settings revision, which
  discards any in-flight run and requeues it with the new policy.
