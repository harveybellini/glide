# Glide account setup: your next actions

Prepared 10 September 2026. The current requirement is to write Glide-owned
travel events into the user's primary Google calendar. The application still
needs changes before testing that behavior; the older separate-calendar setup
instructions do not describe the requested release.

## 1. AWS: establish a local login

AWS CLI was not available on the command path during this review. Install the
Windows AWS CLI v2 package using the [official installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
then open a fresh PowerShell window. This session cannot install programs outside
the project because its filesystem permissions are restricted.

For an ordinary AWS console login, run:

```powershell
aws --version
aws login --profile glide
```

Complete the browser sign-in yourself. Start with `eu-west-1` if prompted for
a region; model and routing availability still need verification. The login
requires CLI v2.32.0 or newer and appropriate identity permissions. If you use
IAM Identity Center, use its SSO setup instead. If a permission error appears,
share its message without credentials. [AWS login instructions](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)

Tell the agent: **"AWS profile glide is signed in."** Do not paste keys or
cached tokens. The agent can verify the account and SDK access, select the
model, configure cost monitoring, and prepare SAM deployment.

The repository now has an ignored `.env` with `AWS_PROFILE=glide` and
`AWS_REGION=eu-west-1`. After signing in, load it into each new PowerShell
window from the repository root with:

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
   `Glide local and hosted`. Register this authorized redirect URI exactly:
   `http://localhost:8000/api/auth/google/callback`.
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

Tell the agent the project ID and saved configuration path. The agent will wire
the runtime settings and give you the hosted callback to register after the
hosting address is known. Later, you will click Connect and consent in your
browser. No call or screen-sharing session is required; remain available to
complete these browser steps when prompted.

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
