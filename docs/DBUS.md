# D-Bus Remote Control

Docking exposes a small session-bus API for item inspection and control.
It is meant for scripts, launchers, automation, and external tools that want to
query or manipulate dock items without poking into internal files.

Connection details:
- Bus name: `org.docking.Docking`
- Object path: `/org/docking/Docking`
- Interface: `org.docking.Docking.Items1`

Current capabilities:
- count visible dock items
- list pinned item ids
- list transient item ids
- pin a transient item
- unpin or remove a pinned item
- query the hover/preview anchor for one item

Inspect the live interface:

```bash
gdbus introspect --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking
```

Expected interface excerpt:

```text
interface org.docking.Docking.Items1 {
  methods:
    GetCount(out i count);
    ListPinnedIds(out as desktop_ids);
    ListTransientIds(out as desktop_ids);
    Pin(in s desktop_id, out b ok);
    Unpin(in s desktop_id, out b ok);
    Remove(in s desktop_id, out b ok);
    GetHoverAnchor(in s desktop_id, out b ok, out i x, out i y, out s position);
  signals:
    Changed();
}
```

#### Method Guide

##### Get visible item count

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.GetCount
```

Example response:

```text
(20,)
```

##### List pinned item ids

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.ListPinnedIds
```

Example response:

```text
(['google-chrome.desktop', 'firefox-stable.desktop', 'applet://clock', 'applet://calculator'],)
```

##### List transient item ids

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.ListTransientIds
```

Example responses:

```text
([],)
```

```text
(['slack.desktop', 'org.gnome.Nautilus.desktop'],)
```

##### Pin a transient item

Use an exact `desktop_id` returned by `ListTransientIds`.

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.Pin slack.desktop
```

Expected responses:

```text
(true,)
```

```text
(false,)
```

`true` means the item was pinned. `false` means the id was not found or was
already pinned.

##### Unpin a pinned item

Use an exact `desktop_id` returned by `ListPinnedIds`.

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.Unpin firefox-stable.desktop
```

Expected responses:

```text
(true,)
```

```text
(false,)
```

`true` means the pinned item was removed from the persistent dock. Running apps
may remain visible as transient items.

##### Remove a pinned item

For v1, `Remove` is the same practical operation as `Unpin` for dock items.
It exists so external tools can treat “remove from dock” as a direct action.

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.Remove applet://calculator
```

Expected responses:

```text
(true,)
```

```text
(false,)
```

##### Get a hover anchor

This returns whether the item was found, the absolute screen anchor position,
and the dock edge (`top`, `bottom`, `left`, or `right`). This is useful for
external overlays or helper popups that want to align with a dock item.

```bash
gdbus call --session \
  --dest org.docking.Docking \
  --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.GetHoverAnchor firefox-stable.desktop
```

Example success response:

```text
(true, 426, 1149, 'bottom')
```

Example failure response:

```text
(false, -1, -1, '')
```

The most common reason for a failure is using the wrong item id. For example,
`firefox.desktop` and `firefox-stable.desktop` are different ids; the method
only succeeds when the exact dock item id is used.

#### Practical Notes

- Item ids are exact. Use `ListPinnedIds` or `ListTransientIds` first instead of
  guessing.
- Applets use ids like `applet://clock` or `applet://calculator`.
- The API is versioned as `Items1` so future incompatible expansions can land in
  a new interface without breaking scripts.
- The service emits a `Changed()` signal when the dock item set changes.

