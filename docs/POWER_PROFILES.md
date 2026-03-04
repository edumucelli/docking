# Power Profiles Applet

This document explains the Power Profiles applet architecture, backend chain,
state model, and operational tradeoffs.

## Goals

The applet provides fast profile switching from the dock with a uniform UX:

- one-click profile cycling
- right-click profile selector
- profile-specific icon
- tooltip with current profile and backend limitations

The applet should still be useful when `power-profiles-daemon` is unavailable.

## Canonical Model

Internally, all backends are normalized to three canonical profile IDs:

- `power-saver`
- `balanced`
- `performance`

Even if native backend naming differs (e.g. `throughput-performance`), UI code
only sees canonical IDs.

## Backend Chain

Auto-detection order:

1. `power-profiles-daemon` (`net.hadess.PowerProfiles`) via DBus
2. `tuned-adm` command backend
3. `tlp` command backend
4. null backend (unavailable)

This order is intentional:

- PPD is semantically exact and preferred.
- tuned can represent profile intent but requires mapping.
- TLP offers mode switching, not true profiles, so mapping is best-effort.

## Backend Mapping

### PPD backend

Direct mapping of `ActiveProfile` property:

- `power-saver` -> `power-saver`
- `balanced` -> `balanced`
- `performance` -> `performance`

### tuned backend

tuned profiles are mapped by name heuristics:

- names containing `balanced` -> `balanced`
- names containing `power` + `save` -> `power-saver`
- names containing `performance` or common performance variants
  (`throughput`, `latency`) -> `performance`

When setting a profile, preferred concrete tuned profile names are selected
first, then token-based fallback matching is used.

### TLP backend

TLP uses mode commands rather than profiles:

- `power-saver` -> `tlp bat`
- `performance` -> `tlp ac`
- `balanced` -> `tlp start`

`tlp-stat -s` output is parsed to infer current mode (`battery` vs `ac`).

## Degraded Mode Signaling

Fallback backends set `degraded_reason` so the tooltip communicates that the
behavior is mapped and not semantically identical to PPD.

Examples:

- `Fallback backend: tuned-adm`
- `Fallback backend: tlp mode mapping`

## Concurrency and UI Behavior

- Polling runs every 5 seconds.
- Backend reads/writes run in worker threads.
- GTK updates run on the main loop (`GLib.idle_add`).
- `_set_in_progress` serializes profile-change requests.

This keeps the dock responsive even when command backends are slow.

## Troubleshooting

If applet shows unavailable:

1. Confirm one backend exists:
   - `powerprofilesctl` or DBus owner for PPD
   - `tuned-adm`
   - `tlp` + `tlp-stat`
2. Verify backend is functional outside Docking.
3. Check dock logs for backend action failures.

If profile switching fails:

- On PPD: verify DBus permissions and service availability.
- On tuned/TLP: verify command permissions (some systems require elevated or
  policy-managed access).

## Test Coverage

`tests/applets/test_powerprofiles.py` covers:

- normalization/labels/order helpers
- tooltip formatting
- PPD backend property parsing
- backend detection order
- tuned and TLP fallback parsing
- applet menu/cycle behavior
- icon render smoke tests

