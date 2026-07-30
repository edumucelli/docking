# Global Search

Docking Search combines local providers, deterministic query intents, utility
results, configurable web fallback, and explicit user script commands.

## Keywords

Search has five top-level keywords:

| Keyword | Scope |
|---|---|
| `app` | Installed applications |
| `win` | Open windows |
| `file` | Pinned and recent files |
| `web` | Web search, optionally followed by an engine such as `google`, `ddg`, `brave`, `bing`, or `gh` |
| `cmd` | User-owned script commands |

Utilities remain implicit: type calculations, conversions, dates, time-zone
queries, URLs, emails, or direct paths without a keyword.

Currency expressions lazily fetch a generic EUR-based rate table from
Frankfurter. The typed amount and currency pair are not sent. Successful rates
are cached for one hour and refreshed in the background.

## Keyboard controls

- **Enter:** run the primary action.
- **Tab:** complete an unambiguous keyword while focus remains in the query.
- **Ctrl+Right:** refine the selected application into actions, individual
  windows, and recent documents.
- **Ctrl+J:** open the standard Action Panel.
- **Ctrl+P:** toggle the result preview.
- **Esc:** close the active panel, then the palette.

## Script commands

Docking automatically discovers executable, user-owned files from
user-controlled directories already listed in the system `PATH`. Keep scripts
where you normally manage personal commands; nothing needs to be copied into a
Docking-specific directory.

Commands are only searched after the explicit `cmd` keyword. Docking executes
them directly with an argument vector and never uses `shell=True`.

Optional metadata uses comment lines near the start of the file:

```sh
#!/bin/sh
# @docking.name Deploy Project
# @docking.description Deploy the current project
# @docking.keyword deploy
# @docking.icon system-run
# @docking.mode terminal
```

Then run it with:

```text
cmd deploy --env staging
```

`mode` is `silent` by default and may be set to `terminal`.

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
