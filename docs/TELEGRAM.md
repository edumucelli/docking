# Telegram integration verification

The Telegram applet hosts the official [Telegram WebK](https://web.telegram.org/k/)
client in an isolated, persistent WebKitGTK session. It shares the browser,
notification, navigation, and lifecycle implementation with WhatsApp; each
service keeps a distinct origin, bridge handler, website-data directory, and
desktop notification identity.

## Badge semantics and startup

Telegram WebK reports the number of **unread unmuted chats** through the standard
`navigator.setAppBadge` API. Docking installs its narrow bridge at document start,
before Telegram captures that method. Numeric API updates take priority over
the page's blinking title. Notification activity is explicitly labelled as such
when no exact count is available. No message bodies, contacts, or private client
modules are inspected.

Telegram's production startup depends on animation frames. WebKit pauses those
in an initially unmapped window, which otherwise leaves the background session
at synchronization indefinitely. Docking realizes and allocates the hidden
viewport without mapping the window or taking focus. A Telegram-only adapter
allows pending frames to finish at most once per **100 milliseconds** while
hidden; visible windows retain native frame scheduling. It does not spoof
document visibility, mark the user active, or run hidden animation at 60 fps.
Cancellation preserves native frame IDs, callbacks cannot run twice, and page
teardown cancels pending fallback timers and native callbacks.

Sources: Telegram's [public notification implementation](https://github.com/TelegramOrg/Telegram-web-k/blob/master/src/lib/uiNotificationsManager.ts)
and its [explanation of startup frames in non-painting tabs](https://github.com/TelegramOrg/Telegram-web-k/blob/master/src/helpers/dom/previewRaf.ts).

## Reproducible browser checks

From the repository root, with GTK/WebKit introspection, Xvfb, and a session bus:

```sh
xvfb-run -a dbus-run-session -- .venv/bin/python -m tools.webapp_smoke
xvfb-run -a dbus-run-session -- .venv/bin/python -m tools.webapp_smoke --live
```

The default fixture makes no network requests. It runs the real WebKit content
manager and origin-scoped document-start script, initializes through a hidden
animation frame, creates a login landmark, and invokes the public badging API.
It verifies a nonzero viewport, a hidden top-level window, login detection, and
the exact `[0, 5]` badge sequence.

The live check loads Telegram's anonymous login page in fresh temporary website
storage. It verifies that login is detected while the top-level window remains
hidden. It neither scans a QR code nor signs in or accesses conversations.

JavaScriptCore tests additionally execute the real frame adapter with browser
scheduler fixtures, covering cancellation, native/fallback races, visibility
changes, teardown/restart, and the bounded fallback cadence. The unit suite
covers profile isolation, badge precedence, notification identity, navigation,
permission guards, and late disconnect callbacks after shutdown. Existing
WhatsApp tests remain part of the regression suite.

## Account-level release checks

Anonymous and synthetic checks do not establish authenticated behavior. Before
advertising complete account coverage, verify with an actual Telegram account:

- QR or phone-number sign-in and persistence after restarting Docking.
- Unread counts while the Telegram window is hidden, including muted chats.
- Reading a chat on another device clears or changes the dock count.
- Desktop notification delivery, dismissal, and activation of the right chat.
- File upload/download and camera/microphone request handling.
- Disconnect clears only the Telegram session, and stopping the applet during
  a pending clear cannot recreate its window.

Calls depend on the official web client and the installed WebKit/media stack;
they require separate authenticated testing on the intended desktop session.
