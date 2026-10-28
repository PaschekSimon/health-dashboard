# Google login setup

Every user registers their own OAuth client in Google Cloud. These steps are
adapted from the [google-health-mcp](https://github.com/partymola/google-health-mcp)
README (GPL-3.0-or-later), whose Google code this package is derived from.
Page names are Google's as of August 2026.

Google's own [setup page](https://developers.google.com/health/setup) builds a
*Web* client for the OAuth Playground; `health auth` refuses that file and says
so. Follow the steps below instead.

## 1. Create the Google Cloud project

1. **Project.** Create a project at [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate) and select it.
2. **API.** Enable **Google Health API** on the [API page](https://console.cloud.google.com/apis/library/health.googleapis.com).
3. **Get started.** Open **Google Auth Platform** and complete **Get started**: app name, support email, **External** audience, contact email.
4. **Audience.** Under **Test users**, add your own Google account (without it sign-in fails with `403: access_denied`).
5. **Data Access.** Click **Add or remove scopes**, search for "Google Health API" and tick these read-only scopes:
   `activity_and_fitness`, `health_metrics_and_measurements`, `sleep`, `ecg`, `profile`, `location`
   (each as `https://www.googleapis.com/auth/googlehealth.<name>.readonly`; location is for run maps).
6. **Clients.** Create an OAuth client of type **Desktop app** and download its JSON.
7. **Publish.** Back on the Audience page, click **Publish app**.

Step 7 matters: while the app is in Testing, Google's refresh tokens expire
seven days after you sign in, so syncing works for a week and then stops.
`health doctor` warns when the stored token has such an expiry. If you signed
in before publishing, run `health auth` again afterwards.

## 2. Sign in

Put the downloaded JSON where the package looks for it, unedited, and sign in:

```
mkdir ~/.config/google-health-mcp
copy the downloaded client_secret_*.json to ~/.config/google-health-mcp/google_client.json
uv run health auth
uv run health doctor
uv run health daily-sync --days 90
```

(On Windows `~` is `C:\Users\<you>`. `GOOGLE_HEALTH_MCP_CONFIG_DIR` moves the
folder; the name is kept from google-health-mcp so an existing login keeps
working.)

Your browser warns that **Google hasn't verified this app**. That is expected:
the app is your own, and verification only matters above 100 users. Click
**Advanced**, then **Go to ... (unsafe)**, and grant the scopes.

If you have more than one Google account, pick the one your health data is in.
`health auth` checks the account after saving the tokens and says so if Google
reports it isn't linked to Google Health.

The sign-in listens on `localhost:8081` for Google's answer, so that port must
be free. Tokens go to `google_tokens.json` in the same folder. Access tokens
last an hour and refresh automatically; the refresh token does not rotate, so
the file can be copied to another machine.
