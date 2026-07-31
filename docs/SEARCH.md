# Global Search

Docking Search combines local providers, high-confidence query recognition,
utility results, and configurable web fallback. Type what you are looking for;
provider names and special prefixes are not needed.

Calculations support scientific notation, exponentiation (`2^8`), modulo,
constants such as `pi`, and common functions such as `sqrt`, `sin`, and `log`.

Currency expressions lazily fetch a generic EUR-based rate table from
Frankfurter. The typed amount and currency pair are not sent. Successful rates
are cached for one hour and refreshed in the background.

Time-zone queries accept every IANA zone installed on the system. Use a full
identifier such as `time in America/New_York`, or an unambiguous location name
such as `time in Kathmandu`. Spaces, letter case, underscores, and accents are
normalized. When a short location name belongs to more than one installed zone,
use its region-qualified identifier.

Date and time queries include `date`, `time`, `in 3 days`, `2 weeks ago`,
`next Monday`, and UTC offsets such as `time in UTC+05:30`. Prefix a time-zone
conversion with an ISO date when daylight-saving rules for a specific day
matter, for example `2026-12-01 10:00 Paris to Tokyo`.

## Provider examples

Applications, dock items, windows, and recent files search discovered desktop
state together. Utility providers activate when their syntax is recognized.

| Provider | Example queries | What the result can do |
|---|---|---|
| Applications | `firefox`, `image editor` | Open or focus an application, open a new window or desktop action, pin or unpin it, close its windows, and refine into individual windows or recent documents. |
| Dock Items | `clock`, `report` | Open pinned files and folders, activate applets, or remove an item from the dock. Application launchers are handled by the Applications provider. |
| Windows | `project plan`, `terminal` | Find windows by title or application identity, activate them, close them, and preview them when the desktop backend supports capture. |
| Calculator | `2^8`, `sqrt(81) + pi`, `1e3 / 4`, `17 % 5` | Evaluate safe arithmetic and common scientific functions, then copy the result. Prefix with `=` to force calculator interpretation. |
| Converter | `10 km to miles`, `32 F to celsius`, `10 USD to EUR` | Convert supported length, weight, volume, temperature, speed, time, data, and live currency values, then copy the result. |
| Recent Files | `proposal`, `invoice` | Search the desktop's recent-file history, open a result, and preview supported local content. |
| Date & Time | `date`, `time`, `in 3 days`, `next Monday`, `time in Kathmandu`, `10:00 UTC to New York` | Show and copy dates, relative dates, local or remote times, and time-zone conversions. |
| Direct Paths | `~/Downloads`, `/etc/hosts`, `file:///home/user/report.pdf` | Open an existing file or folder, copy its path, and preview supported local content. |
| Web | `docs.python.org`, `user@example.com`, `What is a Linux dockbar?` | Open detected URLs, compose email, or offer a search using the configured engine when local results are weak. |

## Keyboard controls

- **Enter:** run the primary action.
- **Ctrl+Right:** refine the selected application into actions, individual
  windows, and recent documents.
- **Ctrl+J:** open the standard Action Panel.
- **Ctrl+P:** toggle the result preview.
- **Esc:** close the active panel, then the palette.

## Relevance learning and privacy

Docking learns from activated results and actions. Frequency, query-specific
selection, and recency provide a small bounded ranking boost that cannot
override stronger text-match tiers. The state file stores SHA-256 identifiers;
raw queries, result titles, file paths, and document names are not persisted.
These hashes are pseudonymous rather than encrypted.

```text
~/.local/state/docking/search-usage.json
```

## Previews

Press **Ctrl+P** to preview the selected result. Search loads previews lazily:

- live backend captures for preview-capable windows;
- application state, windows, actions, and recent documents;
- aspect-correct image thumbnails and metadata;
- bounded text, source-code, and readable Markdown excerpts;
- bounded ZIP and TAR member listings without extraction;
- directory listings and script source.

Large, binary, unsupported, or failed previews fall back to safe metadata.
