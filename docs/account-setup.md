# Glide account setup: your next actions

Updated 11 September 2026. The current requirement is to write Glide-owned
travel events into the user's primary Google calendar. The application and
AWS deployment are complete; only the Google browser steps below remain.

## 1. AWS: establish a local login

Done. AWS CLI v2 and SAM CLI are installed, the `glide` profile authenticates
(root login session, `eu-west-1`), Amazon Location Places/Routes work, and
Bedrock `eu.amazon.nova-2-lite-v1:0` answers real calls. The stack `glide` is
deployed and the public site is `https://d3tvxy281s2u11.cloudfront.net`.

No further AWS browser steps are required right now.

The repository has an ignored `.env` with `AWS_PROFILE=glide` and
`AWS_REGION=eu-west-1`. Load it into each new PowerShell window from the
repository root with:

```powershell
. .\scripts\load_env.ps1
```

## 2. Google: create the web OAuth client

1. Sign into [Google Cloud Console](https://console.cloud.google.com/) with an
   account you control. Create or select a project named `Glide` and note its
   project ID.
2. Under **APIs & Services > Library**, enable **Google Calendar API**.
3. Open **Google Auth platform** and configure the app branding/contact details.
   For a personal Gmail test account, use an external testing audience and add
   the designated test account under test users.
4. Under **Clients**, create a client of type **Web application** named
   `Glide local and hosted`. Register these authorized redirect URIs exactly:
   `http://localhost:8000/api/auth/google/callback` and
   `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback`.
5. Save the downloaded client configuration privately inside this repository
   at `secrets/google-oauth-client.json` (create the `secrets` folder if needed).
   If the console instead supplies individual values, save `GOOGLE_CLIENT_ID`
   and `GOOGLE_CLIENT_SECRET` in the ignored `.env` file. Do not overwrite other
   existing settings. Give the agent only the saved file path.

These steps follow Google's [Calendar setup prerequisites](https://developers.google.com/workspace/calendar/api/quickstart/python)
and [web OAuth client instructions](https://developers.google.com/workspace/guides/create-credentials).
Use the web client type for Glide even though the Python quickstart demonstrates
a desktop application.

The revised implementation should request `openid`, `email`, and
`https://www.googleapis.com/auth/calendar.events.owned`, which permits event
read/write on calendars the user owns. Google does not restrict this scope to
Glide-created events; the application must enforce that restriction itself.
The old `calendar.app.created` scope cannot authorize primary-calendar writes.
[Google Calendar scopes](https://developers.google.com/workspace/calendar/api/auth)

The client configuration is already saved locally and wired into `.env`. When
ready, tell the agent; you will click Connect and consent in your browser. No
call or screen-sharing session is required; remain available to complete the
browser steps when prompted.

Import the downloaded web-client file into the ignored `.env`; this also
generates the local cookie secret without printing either secret:

```powershell
.\scripts\import_google_oauth.ps1 -CredentialPath .\secrets\google-oauth-client.json
```

Load the values before starting Glide:

```powershell
. .\scripts\load_env.ps1
uv run uvicorn glide.api.app:app --reload
```

## 3. Submission timing

The owner confirmed submission by the deadline. The official deadline is
14 September 2026 at 17:00 PDT, equivalent to **15 September at 01:00 BST**.
Aim to submit by **14 September at 18:00 BST** for contingency. Required judging
access ends **9 October at 01:00 BST**. The requested hosting month, interpreted
from 10 September to 10 October, covers that period.
[Official rules](https://agentsforhumans.devpost.com/rules)

The available tracks are Everyday Agents, Professional Agents, and Good Neighbor
Agents. Everyday Agents is the suggested fit for Glide, pending the owner's
final selection. The owner will upload the video, confirm eligibility, and
press Submit. The agent will prepare the material and verify the public links.

Account setup can proceed while the agent fixes the application. A missing
profile or OAuth client does not block offline implementation.
