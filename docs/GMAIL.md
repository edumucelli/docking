# Gmail integration setup and verification

Gmail is an optional unread-inbox-conversation applet. It does not embed Gmail,
read messages, edit labels, or provide email actions. Its badge aggregates
`threadsUnread` from the `INBOX` label for each connected account.

## Managed Google OAuth application

Before a production release advertises Gmail sign-in, the maintainer must:

1. Create a Docking-owned Google Cloud project and enable the Gmail API.
2. Configure the external OAuth audience, Docking branding, a verified homepage,
   and an accurate privacy disclosure. Explain local credential storage, the
   requested permissions, and account removal. Complete Google's applicable
   verification requirements and publish the application for production use.
3. Create a **Desktop app** OAuth client. Supply its client ID and client secret
   in `docking/applets/gmail/oauth_client.py` before building every release
   artifact. Desktop client metadata is public and cannot be kept confidential;
   user refresh and access tokens must remain secret.
4. Complete the live verification below before shipping. This repository does
   not contain a fabricated or shared third-party OAuth client.

Scopes are `openid`, `https://www.googleapis.com/auth/userinfo.email`, and
`https://www.googleapis.com/auth/gmail.labels`. Google classifies `gmail.labels`
as non-sensitive, but it permits label editing as well as reading. Docking only
reads label counts. No message-reading or message-modifying scope is requested.

References: [desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[label endpoint](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/get).

## Developer testing

Download a Desktop OAuth client JSON from your Google Cloud project, add the
test accounts to its audience if the application is still in testing, and run:

```sh
DOCKING_GMAIL_OAUTH_CLIENT_FILE=/absolute/path/desktop-client.json python run.py
```

Only installed/desktop client metadata is accepted. Google authorization and
token endpoints are fixed by Docking; URLs in the imported file cannot replace
them. This override is for developers, not the ordinary user setup flow.

Sign-in uses the system browser, PKCE, OAuth state validation, and a temporary
IPv4 loopback listener. It times out after five minutes and can be cancelled
from the applet menu. Never log callback URLs, authorization codes, tokens, or
raw provider authentication errors.

Refresh credentials are stored with libsecret, scoped to the Google subject,
OAuth client, and connection reference. Connection references prevent late
workers for a cancelled or replaced account from overwriting a newer login.
They are not credentials and may be saved in the applet preferences. Secret
operations are cancellable and time bounded; missing keyring support is an
applet-local error.

## Dependencies and desktop checks

Install the project Python dependencies and libsecret introspection:
`gir1.2-secret-1` on Debian/Ubuntu, `libsecret` on Fedora/Arch/BSD, or the Secret
typelib package on openSUSE. The desktop also needs an enabled Secret Service
provider, such as GNOME Keyring or KDE's Secret Service implementation.

Flatpak uses libsecret's secret-portal backend rather than unrestricted session
bus access. Snap declares `network-bind` for the localhost callback listener.
Source installs remain usable without a keyring; Gmail displays the missing
integration instead of preventing Docking startup.

## Release verification

Automated tests use synthetic tokens, mocked Google replies, and real local
callback requests. They must not contact Google or the user's keyring.

Before advertising sign-in, verify manually:

- Connect personal Gmail and an allowed Google Workspace account together.
- Compare each inbox's unread **conversation** count with the applet; multiple
  unread messages in one conversation must count once.
- Open both accounts from the menu and verify the selected inbox after restart.
- Restart Docking and confirm that accounts restore without a fresh login.
- Read mail on another device; counts update within the next successful poll.
- Disconnect one account and verify that the other remains connected.
- Cancel or deny consent; confirm that no account appears later.
- Disable network, revoke access, and lock the keyring: retain last-known counts,
  show the affected account's status, and recover through retry or reconnect.
- Exercise GNOME and KDE keyrings, Flatpak portal storage, and localhost OAuth
  callbacks in the released package formats.

Live Google authorization and desktop keyring/portal checks require configured
credentials and the corresponding desktop sessions; passing mocked tests is not
a substitute for these release gates.
