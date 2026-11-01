# Two-phase implementation handoff for platform integration

## Purpose and baselines

Reorganize the existing Linux/BSD implementation into explicit platform interfaces before adapting the Windows implementation in [PR #477](https://github.com/edumucelli/docking/pull/477).

Use these inspected revisions as reference points:

- **Merged Phase 1:** `094e50a4540d2432476a3691f7399407204b09a5` (PR #480).
- **Unix prerequisite completion base:** `25649297791f40959c76af888fc2a808b106387a`.
- **Pre-refactor master reference:** `39a066846b8f719c343c2cccc645d7d504f0db52`.
- **Windows PR:** `2b0bbd9462987678a781905c5fde7d41103e5386`.

The latest review covers the ten PR commits after `8f53eb5d`, through `2b0bbd94`. The previous master reference was `96605f07`; the new base additionally preserves zero-percent peripheral battery readings in `39a06684`. These are reference revisions, not instructions to reset the implementation checkout or discard a newer master.

Before implementation, record the actual revisions being used and inspect subsequent changes. Do not overwrite fixes added after these reference points.

This document supersedes the earlier plans. Module groups written as `directory/{a.py,b.py}` apply to every named file. Paths without a `docking/` prefix in the runtime inventories are relative to `docking/`.

The behavior inventories identify source/reference modules, including extraction names from the earlier plan and PR. The final organization and the source-to-destination manifests below are authoritative for destination paths. An unchanged shared module keeps its current path. Do not create intermediate flat implementation modules such as `unix_catalog.py` when the manifest specifies a package destination. Reuse existing implementations rather than rewriting them.

### Governing requirements

1. **Phase 1 preserves existing Linux/BSD behavior.** Extract implementations, introduce interfaces, and change dependency wiring. Preserve command precedence, polling cadence, settings, defaults, application identities, supported capabilities, and visual behavior.
2. **Phase 2 preserves both the completed Phase 1 behavior and the Windows PR’s behavior.** Resolve conflicts by adapting Windows implementations to the Phase 1 contracts.
3. **Keep platform selection at composition boundaries.** Shared consumers receive typed dependencies. Native implementations may validate their operating system, API version, or external compatibility boundary.
4. **Separate three facts:** host OS, GTK display backend, and window-management session. Cinnamon’s XWayland surface with native shell tracking must continue working.
5. **Preserve ownership.** Factories create independent stateful clients; consumers stop before their providers; native resources close on their owning threads.
6. **Keep selection lightweight.** Configuration, metadata discovery, and headless helpers must not import GTK or initialize native GUI resources.
7. **Do not treat a mechanical move as permission to change policy.** Any necessary behavioral correction needs a focused regression test and a separately identifiable change.
8. **Keep persisted schemas unchanged unless explicitly required by the existing Windows feature.** Never place providers, factories, native handles, or service objects in serialized dataclasses.
9. **Do not weaken tests to accommodate the extraction.** Update construction and patch locations while retaining the original assertions.

### Adjustments resulting from the module review

- Do not create one large provider that every consumer can query. The provider belongs to composition; consumers receive application, applet, host, or session interfaces.
- Do not inject a single backend instance where restart requires a fresh instance. Inject a factory.
- Do not make low-level path helpers depend on runtime composition.
- Do not move every native feature hook into Phase 1. Introduce interfaces around existing behavior first; add Windows-specific extensions in Phase 2 with explicit Unix defaults.
- Preserve existing mixed-session menu behavior when extracting its policy. Do not reinterpret that behavior merely because a different backend fact seems more appropriate.
- Use one trash-change subscription interface rather than making the applet choose between filesystem and shell observers.
- The previous `psutil` development-dependency recommendation is obsolete. PR commit `2334e7b2` fixes Linux test collection through dependency isolation. Preserve that fix rather than adding an unnecessary Linux runtime dependency.
- Association results are now an explicit shared contract: pending, ready (including ready-empty), and unavailable are different states. Phase 1 introduces the result types around synchronous Unix behavior; Phase 2 carries over Windows worker resolution and deferred consumer actions.
- Windows catalog enumeration and association acquisition share one existing worker. The change monitor has bounded Explorer/cache reconciliation and must not repeatedly cancel a slow resolver.
- Installed input acceptance and release evidence are bound to the exact candidate wheel and source commit. Server API coverage and Windows 11 workstation composition coverage are separate required evidence profiles, not interchangeable test results.

### Latest PR commits accounted for

| Commits since the previous review | Requirements carried into this plan |
|---|---|
| `0a35f8e7`, `0629eb9b`, `1435e949` | Owned input fixtures, candidate-wheel/source identities, separate Server/workstation coverage, and complete client release evidence. |
| `77fd0c5e`, `0470b869`, `9c81bd25` | Fixture lifecycle/activation/argument corrections, binary-wheel and short-path upgrade fixtures, strict before/after payload validation, and nonfatal cache-export configuration without weakening test failures. |
| `d2cb7cca` | Worker-owned application and association discovery, pending first-run defaults/media launch, exact directory associations, and complete selected-application file/protocol activation. |
| `5e168bc8`, `2b0bbd94` | Explorer subscription recovery, finite cache-convergence reconciliation, captured resolver/monitor lifetimes, passive diagnostics, and opt-in verified hosted Terminal containment. |
| `84d66deb` | Isolated no-GI smoke fixtures use the association/readiness interfaces correctly and run separately from full-suite collection. |

## Final module organization

Use a **domain-first organization with explicit implementation packages**. Shared contracts, models, and orchestration stay at the domain root. Concrete code goes under that domain's `implementations/` package. Keep desktop-session backends in their existing separate hierarchy.

### Directory conventions

| Location | Meaning and permitted contents |
|---|---|
| Domain root, such as `platform/applications/` or `applets/battery/` | Contracts, neutral values, shared policy, and consumer-facing orchestration. Applet roots also contain GTK wiring and rendering. No OS detection or concrete implementation selection. |
| `implementations/shared/` | A concrete adapter usable on more than one OS family, such as a portable vendor command. Create it only for an existing cross-platform implementation; it is not a general utilities directory. |
| `implementations/unix/` | Concrete behavior shared by Linux/BSD, such as desktop files, MPRIS, PulseAudio, SNI, and Unix desktop tools. Unix does not mean Linux. |
| `implementations/linux/` | Linux-specific behavior, including procfs, sysfs, ALSA, and BlueZ integration. |
| `implementations/bsd/` | Existing FreeBSD/OpenBSD behavior. Keep their small internal differences local; do not add per-BSD packages unless distinct implementations are actually needed. |
| `implementations/windows/` | Windows implementation of the domain contract plus its domain-specific helpers. |
| `platform/backends/{x11,wayland,cinnamon,gnome,kwin,reduced,windows}/` | Session/window-management implementations, not the general OS implementation tree. X11 and Wayland remain available to the supported Unix OSes. |
| `platform/native/windows/` | Reusable Windows ABI, input, shell, graphics, and process primitives. This is lower-level code, not a session backend or application registry. |
| `platform/composition/` | Explicit platform selection and assembly. This is the only general runtime layer allowed to know the concrete implementation set. |

All new package `__init__.py` files are lightweight. Implementation package initializers do not select a backend, import all sibling implementations, start workers, or load native libraries. Do not create empty OS packages or placeholder Windows implementations in Phase 1.

### Final tree after Phase 2

The tree shows package ownership; the phase manifests enumerate the files to move. Existing shared files not shown keep their current paths.

```text
docking/
├── core/                            # Config, persistence policy, pure logic
├── platform/
│   ├── composition/
│   │   ├── base.py                  # Composition contracts
│   │   ├── selection.py             # Select using the resolved host context
│   │   └── {unix,linux,bsd,windows}.py
│   ├── host/
│   │   ├── {base,selection}.py      # Lightweight host API and OS facts
│   │   └── implementations/
│   │       ├── unix/
│   │       │   └── {backend,paths,storage,locking,process}.py
│   │       ├── {linux,bsd}/
│   │       │   └── {backend,mounts}.py
│   │       └── windows/             # Added in Phase 2
│   │           └── {backend,paths,storage,locking,process}.py
│   ├── desktop/
│   │   ├── {base,selection}.py      # File, target, input, lifecycle adapters
│   │   └── implementations/
│   │       ├── unix/
│   │       │   └── {backend,files,targets,lifecycle}.py
│   │       └── windows/
│   │           └── {files,targets,lifecycle,keyboard,icons,menus,geometry}.py
│   ├── applications/
│   │   ├── {backend,types,associations,handle_adapters}.py
│   │   ├── {registry,launcher,identity,matcher,listing,recents}.py
│   │   └── implementations/
│   │       ├── unix/
│   │       │   └── {catalog,associations,discovery,entries,
│   │       │        launch,desktop_launch,registration,flatpak,steam}.py
│   │       ├── shared/monitor.py  # Gio catalog observer
│   │       ├── linux/process.py
│   │       ├── bsd/process.py
│   │       └── windows/
│   │           └── {catalog,discovery,associations,native_catalog,
│   │                catalog_monitor,catalog_shortcuts,launch,process,
│   │                registration,export,documents,drag_source}.py
│   ├── tray/
│   │   ├── {backend,state,menu,service,notifications}.py
│   │   └── implementations/
│   │       ├── unix/{backend,status_notifier,dbusmenu}.py
│   │       └── windows/{backend,host,appbar,icons,messages}.py
│   ├── native/
│   │   └── windows/                # Shared low-level primitives; Phase 2
│   ├── backends/
│   │   ├── {base,selection,window_projection,gtk_geometry,gtk_surface}.py
│   │   ├── unix/processes.py       # Shared POSIX window-action primitive
│   │   ├── x11/
│   │   ├── wayland/
│   │   ├── {cinnamon,gnome,kwin,reduced}/
│   │   └── windows/                # Session services only
│   └── {files,targets,icons,keyboard,application_icons,icon_source}.py
├── applets/
│   ├── {base,platform,services,worker,popup}.py
│   ├── battery/                    # Representative applet organization
│   │   ├── {__init__,applet,state,render,backend,peripherals}.py
│   │   └── implementations/
│   │       ├── unix/{desktop_settings,upower}.py
│   │       ├── linux/backend.py
│   │       ├── bsd/backend.py
│   │       └── windows/{backend,peripherals}.py
│   ├── <other native applets>/     # Exact packages in the applet manifest
│   ├── systemtray/
│   │   ├── {applet,state,render,presentation}.py
│   │   └── implementations/unix/{presentation,xembed}.py
│   ├── webapp/
│   │   ├── {applet,backend,profile,state,bridge}.py
│   │   └── implementations/
│   │       ├── unix/{backend,notifications}.py
│   │       └── windows/{backend,notifications,companion/}
│   └── aiusage/
│       ├── {hook,hook_runner,store,state,render}.py
│       └── backends/               # Claude/Codex/etc. provider axis, not OS
├── search/
│   ├── {controller,icons,intents,preview}.py
│   ├── providers/                  # Search domains, not OS implementations
│   ├── services/
│   │   ├── {platform,global_shortcuts,window_focus}.py
│   │   └── implementations/
│   │       ├── unix/{platform,shortcuts,x11_shortcuts}.py
│   │       └── windows/{shortcuts,focus}.py
│   └── ui/
└── ui/                             # GTK UI consuming the selected contracts
```

The `desktop` package owns runtime adapters for file/target/input/lifecycle operations that may use Gio, GTK, or the main loop. The `host` package remains usable without GI. This separation prevents a path or writer-lock helper from importing the GUI through an OS provider.

The existing public facades `platform/files.py`, `targets.py`, `keyboard.py`, and `icons.py` stay at their current paths. Classes receive narrow dependencies from composition. Existing module-level helper APIs may delegate to a narrow domain adapter obtained through the dedicated selector; they must not fetch a general runtime provider or depend on a mutable application-wide service container. Selectors reuse the resolved host facts and do not rediscover the OS on each call.

### Dependency boundaries

| Consumer | Allowed dependency direction | Forbidden dependency direction |
|---|---|---|
| `core/` | Pure/shared values and the lightweight host facade already required for persistence/path behavior | Runtime composition, concrete OS implementations, GTK session services |
| Shared application/tray modules | Their contracts, neutral values, injected adapters, shared infrastructure | Concrete OS implementation imports or selection; UI/model imports from application services |
| Applet `applet.py` / `render.py` | GTK, shared UI helpers, applet contracts and injected services | Imports of `implementations/` or native OS selection |
| Applet `state.py` | Values, parsing, calculations, preference policy | Filesystem/process/device operations, factory selection, GTK |
| Domain implementation packages | Their domain contracts/shared values and appropriate low-level helpers | Another OS's implementation, composition, or application UI construction |
| Session backends | Session contracts, injected application/process services, native primitives | Applet implementation code or Search controller/UI |
| `platform/native/windows/` | Standard library, logging, other native primitives, binding-free value/contract modules | Registry/model instances, concrete application strategies, applet implementations, GTK widgets/UI construction |
| `platform/composition/` | Selected implementations and contracts needed for assembly | Import by ordinary consumers to locate services |
| Headless helpers | Host primitives and their own domain logic | GUI runtime initialization or eager GI imports |

Keep OS implementation selection in selectors/composition. Capability/tool/compositor selection remains in the appropriate concrete implementation or session selector. For example, the Unix Music backend may choose MPRIS/playerctl without an OS branch, and the Unix session selector may choose Sway/X11 without being moved under `implementations/linux/`.

`platform/backends/windows/` contains only the Windows session implementation. Reusable COM, input, shell, and runtime-loading code moves to `platform/native/windows/`; catalog strategies move to the application domain; icon/GTK integration moves to the desktop domain. This prevents a battery or browser backend from depending on a window-management implementation just to obtain a COM helper.

### Move and compatibility rules

1. For each move, update imports, runtime import strings, tests/patch targets, entry-point strings, package-data/resource lookups, and native build inputs in the same focused change.
2. Preserve function/class names and behavior unless a rename is explicitly listed. A file move does not authorize a feature rewrite.
3. Use one canonical implementation. Do not copy code into the new tree while retaining a second production implementation at the old path.
4. Keep compatibility wrappers only for established public entry points/imports: launcher commands, headless hook/helper commands, and documented facades. Remove obsolete internal import-only wrappers, including the former StatusNotifier package. Internal callers and tests use the canonical paths; do not retain a general set of old OS-module shims.
5. All implementation packages use lightweight `__init__.py` files. Native resource lookup starts from an owning package/resource, not a guessed checkout-relative path.
6. The manifests below cover moves, not only additions. Modules absent from a move manifest keep their path and retain the behavior work specified in their phase inventory.

---

# Phase 1 — Reorganize master around platform interfaces

## 1.1 Deliverable and implementation order

Create a branch from the actual current master. Complete the following stages in order, keeping each stage reviewable and passing its affected checks:

1. Record Linux/BSD behavior and establish the test baseline.
2. Introduce host and runtime composition boundaries.
3. Extract application discovery, association, registration, launching, and process interfaces.
4. Introduce construction-time applet dependencies and migrate existing native applets.
5. Extract browser, tray, Search, and shared UI boundaries.
6. Correct dependency teardown ordering and add architecture guards.
7. Run the complete Phase 1 acceptance checks before merging.

Do not import Windows implementations, native input helpers, Windows packaging, or Windows-only state fields during this phase.

## 1.2 Composition and dependency model

Introduce these narrow responsibilities:

| Interface or component | Responsibility |
|---|---|
| `HostServices` | Existing path/URI semantics, user-directory resolution, directory flushing, and headless process/locking primitives. |
| `PlatformProvider` | Composition-only factories for application services, applet backends, Search integration, and session creation. |
| `ApplicationCatalogBackend` | Source discovery and conversion into neutral catalog results. Unix source callbacks remain inside its implementation. |
| `ApplicationAssociationBackend` | Default/recommended/all handler acquisition and directory-handler names through ordered association reads; native conversion stays behind the adapter. |
| Shared `associations.py` values | `AssociationKey`, `AssociationStatus`, `AssociationRead`, `AssociationReads`, and `AssociationResolution`. Preserve pending, ready-empty, ready-with-handler, and unavailable outcomes. |
| `ExecutableRegistrationBackend` | Stable executable ID calculation, launcher registration, and registered-launcher enumeration. |
| `ApplicationLaunchBackend` | Platform launch operations and launch outcomes. |
| `ProcessBackend` | Executable, script, sandbox, and Steam process evidence. |
| `AppletBackendFactories` | Explicit typed factory methods returning applet-domain backends. No string-keyed service lookup. |
| `AppletPlatformServices` | Construction-time applet dependencies: backend factories and the shared tray service. |
| Existing `AppletServices` | Late-bound services requiring session/model/UI completion. |
| Existing `SessionBackend` | Window, surface, workspace, preview, capture, idle, and visibility services. |
| Search integration factories | Primary shortcut, optional fallback, and later native focus integration. |
| `BrowserBackend` | Browser lifecycle and actions independent of WebKitGTK. |
| `TrayBackend` | Tray inventory and actions independent of SNI transport. |

Use standard-library-only contract modules and postponed/type-checking-only imports where necessary. Owned platform/backend contracts use `ABC` and `@abstractmethod`, with explicit implementation inheritance. Keep callable factories, external native handles, and consumer views as structural protocols. Do not add runtime validation or permissive fallback implementations to shared consumers.

### Bootstrap sequence

Preserve these dependency constraints:

1. Resolve lightweight host selection.
2. Perform required process/display preparation before opening the GTK display.
3. Initialize translations before importing modules that evaluate translated labels.
4. Initialize/import the toolkit through the established launcher path.
5. Construct application services and obtain the initial registry generation.
6. Load configuration and construct stable applet dependencies.
7. Construct the model, session, and UI.
8. Attach late-bound services and start runtime activity.

Platform detection is retained once per process. Directory environment variables must still be evaluated with the same timing as their existing helper functions; selecting a host must not prematurely freeze their values.

## 1.3 Bootstrap, host, configuration, and environment modules

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `docking/platform/host/{__init__.py,base.py,selection.py,posix.py}` — new | Define the lightweight host contract and Linux/BSD POSIX implementation. Keep imports independent of application discovery, applets, GTK, and runtime composition. Provide explicit methods rather than a generic service registry. |
| `docking/platform/composition/{__init__.py,base.py,selection.py,unix.py,linux.py,bsd.py}` — new | Assemble typed application, applet, Search, and session factories. Share Unix implementations by composition. Keep Linux/BSD-specific choices here and retain implementation-local desktop/tool selection where appropriate. |
| `docking/launcher.py` | Own host preparation and pass the selected runtime provider into application assembly. Preserve `prepare_display()` because tooling calls it. Keep the source and installed launchers on the same path. |
| `docking/app.py` | Replace hidden dependency selection with explicit construction. Keep registry-before-config ordering and shared provenance objects. Create the shared tray service before model/applets. Move Unix GUI signal registration into runtime lifecycle composition. Register cleanup when ownership begins. |
| `docking/platform/environment/{environment.py,__init__.py}` | Preserve desktop/compositor detection and public diagnostic helpers. Keep session selection distinct from OS selection. Delegate host facts to the selected host context where needed; do not put runtime-provider construction in this package. |
| `docking/platform/environment/xdg.py` | Delegate directory resolution to POSIX host services while preserving current XDG overrides and fallback directories. Preserve the public helper names. |
| `docking/core/paths.py` | Preserve its helper surface and delegate OS-dependent parsing to the host path implementation. Keep directory-creation helpers simple. Preserve POSIX authority restrictions, decoding, expansion, rejection behavior, and exception handling. |
| `docking/core/config.py` | Delegate directory flushing to host services. Preserve atomic replacement, file flushing, symlink handling, backup selection/recovery, validation, and first-run pin creation. Do not add service objects as dataclass fields. |
| `docking/core/theme/migration.py` | Reuse the same directory-flush primitive. Preserve backup creation, candidate validation, replacement order, and cleanup on failure. |
| `docking/core/theme/theme.py`, `docking/core/{greeting.py,tips.py,updates.py}` | Audit directory-helper usage and import timing. Change imports only where required by the host boundary. Preserve existing path constants and persistence formats. |
| `docking/platform/libraries.py` | Keep library-name/platform compatibility handling local to this external boundary. Do not make general consumers perform library selection. No unrelated loader rewrite. |
| `docking/platform/gamescope.py`, `docking/platform/backends/cinnamon/startup.py` | Retain existing preparation behavior and ordering constraints. Provider extraction must not open the wrong display before environment preparation. |
| `docking/i18n.py`, `docking/log.py` | No feature changes. Verify that the new bootstrap does not evaluate translations too early or pull in GTK through logging. |
| `docking/applets/{__init__.py,identity.py}` | Preserve cheap metadata discovery, lazy class loading, instance IDs, and availability filtering. Host availability checks may use lightweight host facts; they must not construct native backends. Preserve OpenBSD Bluetooth exclusion. |

**Regression checks:** fresh isolated imports without GI, XDG overrides, configuration round trips, symlinked config and backups, theme migration failures, Cinnamon startup, GameScope environment preparation, source/installed launcher equivalence.

## 1.4 Application modules

All paths below are under `docking/platform/applications/` unless otherwise stated.

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `backend.py` — new | Collect the independent catalog, association, launch, executable-registration, and process ABCs, plus `CatalogMonitor`, `DiscoveryResult`, `GeneratedDesktopEntry`, `LaunchedProcess`, and `LaunchResult`. Preserve their signatures, fields, and defaults. Native implementations inherit the relevant ABC explicitly. Keep imports binding-free; service/Steam annotations use type-checking-only imports. Move OS selection to composition and keep concrete construction parameters on implementations. Each capability remains independently injected. |
| `unix_catalog.py` — new | Own Gio enumeration, desktop directories, `DesktopAppInfo` construction, and existing source injection points. Preserve source precedence, hidden-entry rules, presentation order, and diagnostics. |
| `associations.py`, `backend.py`, `unix_associations.py` — new | Reuse the PR's binding-free association keys/status/read/resolution values in `associations.py`; the adapter contract belongs in `backend.py`. Unix acquisition remains synchronous, returning ready or unavailable as appropriate; a valid empty result is ready-empty. Adapt `default_for_content_type()`, `preferred_listing_for_content_types()`, and `directory_application_name()` consumers to the explicit resolution type. Preserve defaults-first, recommended-next, all-handlers-last behavior, handler visibility, ID-less listings, and retained-handle limits. |
| `registry.py` | Require explicit catalog, association, and registration dependencies. Retain immutable publication, indexes, generations, handle epochs, subscriptions, debouncing, and failed-refresh retention. Move native monitor acquisition behind an adapter while keeping refresh scheduling shared. |
| Catalog observer implementation extracted from `registry.py` | Place it in `platform/applications/implementations/unix/monitor.py`. Own Gio app and directory monitors. Preserve monitoring of application, generated-launcher, and Steam directories. Reconcile watched paths after refresh and stop every subscription exactly once. |
| `discovery.py` | Retain Unix parsing/source-merging implementation. Remove the neutral type definition after moving it. Shared catalog consumers must not import this module merely to obtain result types. |
| `entries.py` | Retain desktop-entry parsing and Unix launcher writing. Common consumers must use registration rather than call desktop-entry creation directly. Preserve generated launcher content, permissions, ID algorithm, and database refresh behavior. |
| `backend.py`, `unix_registration.py` — new | Define and implement executable ID/registration operations. Keep executable recognition and filesystem details in the Unix implementation. Preserve the distinction between a rejected target and a launcher that was saved but cannot be resolved. |
| `launcher.py` | Keep registry selection, action policy, and provenance recording. Require an `ApplicationLaunchBackend`; remove embedded Unix execution machinery after extraction. |
| Launch contracts and outcomes in `backend.py` | Preserve process/executable evidence required by provenance. Native handles remain internal. |
| `unix_launch.py`, `desktop_launch.py` — new | Move current Gio, desktop `Exec`, terminal, working-directory, Flatpak host, action, and URI launch mechanics here. Preserve quoting, failure behavior, and fallback precedence exactly. |
| `identity.py` | Require one `ProcessBackend` and use it for executable resolution as well as other evidence. Remove backend recreation during PID queries. Preserve custom resolver injection and shared provenance ownership. |
| `linux.py`, `bsd.py` | Preserve process evidence implementations. `/proc` roots belong to the Linux constructor; explicit test roots must no longer influence OS selection. Keep FreeBSD/OpenBSD differences local. |
| `matcher.py` | Use injected registration/identity operations for generated runtime identities. Preserve existing matching precedence and ambiguity handling. Do not add Windows-style unlisted-executable matching yet. |
| `listing.py` | Preserve listing and icon behavior. Route filesystem URI construction through the file adapter introduced below. Do not add native export metadata yet. |
| `types.py` | Preserve canonical records and existing field meanings. Keep this module binding-free. Do not add Windows launch/export fields in Phase 1. |
| `steam.py`, `flatpak.py`, `projections.py`, `running.py`, `recents.py`, `recent_documents.py` | Retain current behavior and public consumer queries. Change dependencies only where extraction requires it. Do not introduce asynchronous catalog-readiness behavior yet. |
| `docking/platform/model.py` | Pass construction-time applet services and delegate executable registration to the registry. Preserve pin promotion, identity, order, recent-app ownership, and running-window continuity. Do not add pending-catalog placeholders in this phase. |

**Required checks:**

- Failed discovery retains the previous generation and handles.
- A successful empty initial discovery still permits configuration loading.
- Initial discovery failure does not create or overwrite configuration.
- Unchanged metadata does not spuriously increment the generation.
- Default-handler ordering and ID-less handlers remain equivalent.
- Ready-empty and failed/unavailable association reads remain distinguishable; canonical Unix defaults, including valid NoDisplay handlers, retain their existing semantics.
- Generated executable pinning preserves the running item’s identity.
- Steam/Flatpak discovery, hidden entries, and host launching remain unchanged.
- Executable resolution uses the injected process backend exactly as intended.

## 1.5 File, icon, window, and surface modules

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `docking/platform/files.py` — new | Provide the existing Gio URI construction behavior behind a stable adapter. POSIX implementation continues using Gio’s URI constructor. |
| `docking/platform/targets.py` | Extract default-target opening behind a target-opening interface. Preserve scheme validation, normalization, Flatpak host handling, Gio fallback, and boolean result behavior. Require `application_registry` in `TargetService` and obtain folder-handler names through `directory_application_name()` rather than a direct Gio lookup. On synchronous Unix adapters, the existing label and lookup behavior must be unchanged. |
| `docking/platform/icons.py` | Preserve theme, GIcon, file, thumbnail, and override precedence. Use the file adapter where appropriate. Do not add an unused native pixel-worker abstraction during Phase 1. |
| `docking/platform/backends/base.py` | Preserve window/session contracts. Add a narrowly named menu-update policy so shared UI no longer branches on window backend identity. Do not add the full set of Windows popup/drag/input hooks yet. |
| `docking/platform/backends/selection.py` | Keep current compositor selection and fallback policy. Invoke it through Unix runtime composition. Preserve overrides and diagnostic explanations. |
| `docking/platform/backends/native_windows.py` → `window_projection.py` | Move the shared snapshot-based implementation and rename `NativeWindowService` to `SnapshotWindowService`, as already demonstrated by the PR. This is a shared projection, not a Windows OS implementation. Preserve behavior. |
| `docking/platform/backends/kwin/bridge.py`, `docking/platform/backends/wayland/sway_ipc.py` | Update the projection import/base class only. Preserve IDs, event processing, matching, and snapshot publication. |
| `docking/applets/windowkiller/state.py` → `docking/platform/backends/unix/processes.py` | Move POSIX process termination to the platform layer so backends no longer import applet implementation code. Preserve PID validation and result behavior. |
| `docking/platform/backends/x11/services/picking.py`, `docking/platform/backends/wayland/{niri_picking.py,wayfire_ipc.py}` | Update termination imports and tests. Preserve target/PID validation and accepted action results. |
| Session/surface implementations under `backends/{x11,wayland,cinnamon,gnome,kwin,reduced}/` | Supply explicit menu-update policy matching current behavior. Include mixed Cinnamon/XWayland coverage. Do not change placement, reservation, focus, workspaces, or visibility as part of this extraction. |

The menu policy must preserve the existing distinction between deferred in-place removal and immediate removal/reopening. Moving the decision behind an API must not silently change which sessions receive each behavior.

## 1.6 Applet construction and workers

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `docking/applets/platform.py` — new | Define `AppletBackendFactories` and `AppletPlatformServices`. Explicit methods create the supported applet-domain backends; the latter also carries the shared tray service. |
| `docking/applets/base.py` | Add a construction-time base for native-backed applets. Extend the existing `ApplicationServicesApplet` to require application services and `platform_services` together for Applications, Run Command, and Music; do not add a separate combined base. Keep ordinary `Applet` and `TargetServicesApplet` contracts available for unaffected applets. Avoid adding unused dependencies to pure applets. |
| `docking/platform/model.py` | Extend existing typed construction dispatch for these bases. Music receives both application dependencies and applet platform services. Keep late `set_services()` for dependencies that cannot exist until session/UI assembly. |
| `docking/applets/services.py` | Distinguish stable construction dependencies from late session/UI dependencies. The shared tray reference must not be repeatedly replaced during production startup. |
| `docking/applets/worker.py` | Preserve `BackgroundWorker` behavior. Introduce the PR’s serial-worker capability with explicit finalization, lazy thread startup, and cancellation-safe ownership tests. Do not migrate every stateless reader merely because the worker exists. |
| `docking/applets/popup.py` | Preserve current popup behavior and anchors. Add explicit surface-policy wiring needed by existing consumers; do not discover internal attributes through permissive `getattr` defaults. Windows-specific focus/drag/capture extensions are added in Phase 2. |

A factory must return a fresh stateful backend for an independent applet or restarted lifetime. Shared services, such as the tray poll service, are created once by composition and are not owned by individual applets.

## 1.7 Native applet modules

For each row, `state.py` retains value types, parsing, calculations, preference handling, and presentation policy. Platform I/O moves into concrete implementations. Reuse existing implementations and helpers.

| Source/reference applet modules under `docking/applets/` | Required Phase 1 changes and regression requirements |
|---|---|
| `battery/{applet.py,backend.py,state.py,linux.py,bsd.py,peripherals.py}`; new `desktop_settings.py`, `upower.py` as needed | Make reads, power readings, peripheral batteries, and settings actions backend operations. Move battery name/sysfs root into the Linux constructor. Keep existing Linux/BSD readings, charging visibility, percentage/power overlays, UPower behavior, and 60-second/5-second cadences. Preserve existing rendering. |
| `bluetooth/{applet.py,backend.py,state.py,linux.py,bsd.py}`; new `unix_desktop.py` | Inject backend factories, including restart. Move desktop-tool actions out of shared state. Preserve adapter selection, discovery ownership, suppression periods, pair timeouts, trust/removal, recent connections, and command precedence. |
| `brightness/{applet.py,state.py}`; new `backend.py`, `linux.py` | Extract detection/read/write operations. Preserve X11 versus Wayland preference between xrandr, brightnessctl, and sysfs, output selection, range clamping, and unavailable behavior. |
| `caffeine/{applet.py,inhibit.py}`; new `caffeine/implementations/unix/backend.py` | Retain `Inhibitor` and composite “attempt every part” behavior. Move screensaver/systemd implementations behind the factory. Preserve toggle/timer semantics and release ownership. Test stop while acquisition is pending before adopting shared request coordination. |
| `camshield/{applet.py,backend.py,state.py,helper.py,linux.py,bsd.py}`; new `unix_client.py`, `unix_control.py`, `unix_devices.py` | Separate shared state from Unix device inspection and privileged helper control. Preserve allowed-device validation, ACL backups/restoration, helper authorization, status interpretation, and BSD differences. |
| `capslock/{applet.py,state.py}`; new `backend.py`, `linux.py` | Extract `xset` execution behind a lock-key backend. Preserve parser, missing-command behavior, polling, and labels. |
| `keyboardlayout/{applet.py,state.py}`; new contract `backend.py` and `keyboardlayout/implementations/unix/backend.py` | Extract GNOME, IBus, Fcitx5, MATE, and XKB implementations behind the existing layout contract. Preserve discovery order, layout cycling, settings/viewer commands, and fallback behavior. |
| `micshield/{applet.py,state.py}`; new contract `backend.py` and `micshield/implementations/unix/backend.py` | Extract pactl/device/stream operations. Preserve muting all intended sources and capture streams, unavailable results, settings actions, and stale-request protection. |
| `music/{applet.py,state.py,artwork.py}`; new contract `backend.py` and `music/implementations/unix/backend.py` | Keep Music’s application dependencies. Extract MPRIS/playerctl/Rhythmbox operations and media-app identity candidates. Preserve player selection, targeted controls, scroll behavior, artwork, volume interpretation, and fallback order. |
| `network/{applet.py,backend.py,state.py,linux.py,bsd.py}`; new `desktop_settings.py` | Keep state/counter reads, lifecycle, optional connection manager, and settings behind interfaces. Preserve configured interface selection, traffic-rate calculations, NetworkManager actions, Wi-Fi/VPN handling, and BSD capabilities. |
| `notifications/{applet.py,state.py}`; new contract `backend.py` and `notifications/implementations/unix/{backend,activity}.py` | Extract Dunst/GNOME/null implementations and Unix notification activity observation. Preserve backend detection order, pause state, controls, retries, polling, unavailable behavior, Flatpak host-monitor ownership, and history callbacks. |
| `powerprofiles/{applet.py,state.py}`; new contract `backend.py` and `powerprofiles/implementations/unix/backend.py` | Retain the existing control API as an ABC. Extract power-profiles-daemon, tuned, TLP, and null implementations with explicit inheritance. Preserve priority, profile names, active-state interpretation, and unavailable behavior. |
| `screenshot/{applet.py,state.py}`; new contract `backend.py` and `screenshot/implementations/unix/backend.py` | Extract capture discovery/execution. Preserve tool ordering, Wayland portal preference, Flatpak host handling, delay, output naming, and cancellation. Do not introduce Windows selection/preview UI yet. |
| `session/{applet.py,backend.py,state.py,linux.py,bsd.py}`; new `unix.py` | Model available actions and `perform(action)` rather than requiring every OS to expose shell commands. Keep Unix command lists private. Preserve lock fallbacks, action ordering, and accepted-versus-completed semantics. |
| `trash/{applet.py,backend.py}`; new `unix.py` | Separate the contract from Gio/GNOME/MATE/Cinnamon/KDE implementations. Add one `watch_changes(changed)` subscription returning cleanup. Unix implementation owns existing file/mount observers. Preserve trash counting, confirmation preferences, delegated file-manager confirmation, partial failures, per-volume locations, and dropped-file handling. Do not invoke GTK confirmation on a worker. |
| `volume/{applet.py,backend.py,state.py,linux.py,bsd.py,pulseaudio.py}`; new `desktop_settings.py` | Separate optional mixer controls from settings availability. Preserve PulseAudio/BSD command selection, mute and scroll behavior, limits, settings precedence, and lack-of-mixer presentation. |
| `systemmonitor/{applet.py,backend.py,state.py,temperature.py,linux.py,bsd.py}`, `pet/applet.py` | Route existing factory selection through composition. Preserve CPU deltas, memory/disk/temperature readers, Pet behavior, and fake filesystem roots. Do not add Windows telemetry in Phase 1. |
| `thermals/{applet.py,backend.py,state.py,linux.py,bsd.py}` | Inject the existing reader. Remove backend selection from pure state helpers. Preserve sensors/sysfs/BSD fallback, startup delay, cadence, freshness, units, and error presentation. |

The renderer modules remain unchanged during extraction unless a signature adjustment is necessary. A signature adjustment must retain identical output for existing values.

## 1.8 Browser and headless hook modules

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `applets/webapp/browser.py` | Move the existing WebKitGTK implementation to `implementations/unix/backend.py` and remove the old module. Import browser contracts from `backend.py` and profile values from `profile.py` directly. Preserve lazy WebKit loading. |
| `applets/webapp/{backend.py,profile.py}` — new | Define the browser contract and profile values. Keep private session-directory and download-path helpers in the concrete Unix backend, preserving directories, permissions, collision handling, and errors. Do not introduce a shared `storage.py` for backend implementation details. |
| `applets/webapp/{applet.py,state.py,bridge.py}` | Construct a browser through an injected factory. Preserve existing navigation, bridge validation, badge state, recovery, timers, and external opening. Do not broaden accepted bridge messages in Phase 1. |
| `applets/webapp/{linux.py,notification_backend.py,linux_notifications.py,notifications.py}` | Place existing WebKitGTK and desktop notification behavior in concrete adapters. Preserve current Unix messages and permission behavior. Compatibility imports should be narrow; production uses canonical implementations. |
| `applets/telegram/{browser.py,applet.py,state.py}`, `applets/whatsapp/{browser.py,applet.py,state.py}` | Replace inheritance from a concrete browser host with profile-based construction. Define each applet's `PROFILE` in its existing `state.py` beside its URL metadata and remove the obsolete `browser.py`. Remove the WhatsApp `notifications.py` re-export; native tests import the shared Unix notifier directly. Preserve distinct sessions, URLs, callbacks, bridge scripts, tooltips, and disconnect behavior, with profile/state imports remaining free of GTK. |
| `applets/aiusage/store.py` | Call the headless host writer-lock API directly; do not retain an applet-local forwarding module. Preserve the existing lock lifetime and session-update semantics. Do not combine this extraction with a new configuration write protocol. |
| `applets/aiusage/{hook_runner.py,hook.py}` | Keep the established executable entry points headless. Provider registration backends call the host hook-command/argv API directly, without an applet-local `hook_command.py` facade. Preserve existing Unix invocation and recognition of installed/legacy hooks. Do not initialize the GUI provider. |
| `applets/aiusage/backends/{claude.py,codex.py}` | Delegate command representation to the host adapter. Preserve unrelated settings/TOML content, existing hook ownership checks, legacy cleanup, and protection of independently managed hooks. |

## 1.9 Tray, Search, and UI modules

| Source/reference module | Required Phase 1 changes and preservation requirements |
|---|---|
| `platform/tray/{__init__.py,state.py,menu.py,backend.py,service.py,notifications.py}` — new | Extract neutral tray state/menu contracts and shared polling/notification bridge. The service receives a backend factory. Add generation protection for stop/restart and late results. |
| `platform/tray/unix.py` — new | Adapt existing StatusNotifier transport to the neutral tray contract. Use `unix.py` because Linux and BSD can share this transport. |
| `platform/status_notifier/{backend.py,dbusmenu.py}` | Retain SNI/D-Bus protocol mechanics and existing parsing. Convert native menu trees inside the adapter. |
| `platform/status_notifier/{service.py,notifications.py,__init__.py}` | Move shared responsibilities to the tray package. Remove the obsolete StatusNotifier package and update all imports and test doubles to the canonical tray modules. |
| `applets/systemtray/{applet.py,state.py,presentation.py,unix_presentation.py,xembed.py}` | Consume the shared tray service from construction. Do not create a temporary private service. Put XEmbed and Unix presentation behind the presentation interface. Preserve watcher ownership, menus, counts, activation, and empty-state text. |
| `search/services/global_shortcuts.py` | Expose the primary shortcut lifecycle ABC without changing portal mechanics. |
| `search/services/x11_shortcuts.py` | Retain the X11 fallback implementation and its eligibility rules. Keep subprocess ownership and error behavior. |
| `search/services/platform.py` | Become a typed integration/composition interface, not a collection of OS-dispatching factories called by consumers. |
| `search/controller.py` | Accept shortcut/fallback factories. Preserve preference-driven recreation, provider order, query generations, usage, and result selection. |
| `search/ui/{window.py,shortcut_capture.py}` | Preserve existing GTK behavior and shortcut syntax. Do not introduce native Windows focus or modifier probing yet. |
| `ui/factory.py` | Pass the selected Search integration and stable domain dependencies. Keep construction order explicit. |
| `ui/{menu.py,runtime.py}` | Consume the explicit menu-update policy. Preserve current close/removal/reopening behavior and focus handling. |
| `ui/folder/{_browser.py,stack.py}`, `ui/menu.py`, `platform/{targets.py,icons.py}`, `platform/applications/listing.py` | Use the common file adapter at local filesystem URI boundaries. Preserve virtual URI handling and folder/file operations. |
| `platform/unity.py`, `ipc/{items_service.py,introspection.py,__init__.py}` | Preserve Unity overlays and D-Bus item-control behavior. Keep transport failures nonfatal. Do not introduce a Windows IPC replacement. |

Modules such as `ui/about.py`, `ui/update_popup.py`, and ordinary content applets that call `targets.open_target()` retain that facade. They do not need a provider injected merely to open a URL.

## 1.10 Phase 1 verification and exit criteria

### Baseline and tests

Establish the baseline on master, not on the Windows PR snapshot. Earlier PR test results are informational and do not satisfy this phase.

Update and extend:

- `tests/test_app.py`: initial discovery, graph construction, partial failure, cleanup ordering.
- `tests/platform/applications/`: discovery precedence, associations, launcher semantics, identity, generated registration, architecture/import boundaries.
- `tests/core/`: paths, configuration recovery, theme migration.
- `tests/platform/test_backend_{contracts,selection}.py`: unchanged selection and mixed Cinnamon/XWayland behavior.
- Existing applet tests: construction, fallback selection, polling, stop/restart, preferences, rendering.
- `tests/platform/test_status_notifier_service.py` and tray tests: fresh backend lifetimes and stale results.
- Search shortcut/controller tests: portal/fallback behavior and preference changes.
- Browser/Telegram/WhatsApp tests: profiles, storage, recovery, notifications, navigation.
- AI usage tests: headless imports, quoting, concurrent writers, unrelated configuration preservation.
- Association result and folder-name tests: explicit statuses, defaults-before-lower-priority reads, and the required `TargetService` registry dependency. Keep asynchronous Windows acquisition/deferred actions in Phase 2.
- Peripheral battery tests: preserve the updated master behavior for a valid zero-percent reading, including fallback capacity parsing.

Add import/architecture checks that:

- Reject OS selection and concrete native implementation imports in shared consumers.
- Keep host/metadata/headless contracts independent of GTK.
- Require explicit production dependencies.
- Permit legitimate shared Unix implementations on BSD; do not reject imports solely because a historical filename contains `linux`.

### Required checks

Run affected tests first, then the master workflow’s lint, format, type, dead-code, full default, headless GUI, compositor, and BSD checks. Also run `.venv/bin/python -m pytest tests/smoke/test_python314_smoke.py -q` in a separate process: imports cached during full-suite collection can hide missing methods or incorrect readiness/association fixtures. Use the paths supported by master’s actual workflows; do not invoke Windows tooling that has not been integrated.

Update `docs/DEVELOPMENT.md`, `docs/APPLET_DEVELOPMENT.md`, and relevant architecture guidance. Update `AGENTS.md` only when verification or development workflows actually change.

**Phase 1 is complete only when:**

- Linux/BSD behavior matches its baseline across supported sessions.
- New contracts are used by production code.
- Stateful restart and teardown behavior is covered.
- There are no remaining unreviewed default factories in migrated shared consumers.
- Configuration, IDs, pins, translation behavior, and package entry points remain compatible.

## 1.11 Phase 1 source-to-destination manifest

This manifest fixes the final destinations for the extraction work. All paths in the following tables are relative to `docking/`. "Extract" means move the named responsibility and update its callers; keep the rest of the source module shared. Do not change operation order, fallback policy, or scheduling while moving it.

### Host, desktop, application, and tray moves

| Current module or extraction reference | Canonical Phase 1 destination |
|---|---|
| Planned `platform/host/posix.py` | `platform/host/implementations/unix/backend.py` aggregates the narrow host operations; concrete path, flushing, locking, and process/hook primitives live respectively in `paths.py`, `storage.py`, `locking.py`, and `process.py` in that package. |
| `platform/environment/xdg.py`, OS-dependent portions of `core/paths.py` | Extract concrete behavior to `platform/host/implementations/unix/paths.py`; existing modules remain facades. |
| Directory flushing in `core/config.py` and `core/theme/migration.py` | `platform/host/implementations/unix/storage.py`; persistence orchestration remains in the source modules. |
| POSIX locking in `applets/aiusage/store.py`, command construction in the Claude/Codex adapters | Host primitives in `platform/host/implementations/unix/{locking,process}.py`; `applets/aiusage/{locking,hook_command}.py` remain narrow domain facades. Preserve provider hook ownership/format policy in `aiusage/backends/`. |
| Concrete default opening in `platform/targets.py` | `platform/desktop/implementations/unix/targets.py`. |
| Gio file construction behind `platform/files.py` | `platform/desktop/implementations/unix/files.py`. |
| GUI signal installation extracted from `app.py` | `platform/desktop/implementations/unix/lifecycle.py`. Standard-library-only crash/process preparation remains a host primitive. |
| Planned `platform/applications/unix_catalog.py` | `platform/applications/implementations/unix/catalog.py`. |
| Planned `platform/applications/unix_associations.py` | `platform/applications/implementations/unix/associations.py`. |
| PR `platform/applications/associations.py` | Retain this binding-free value module at `platform/applications/associations.py` in Phase 1; adapt Unix readers/consumers to its explicit result states without adding asynchronous acquisition. |
| Catalog observer extracted from `platform/applications/registry.py` | `platform/applications/implementations/unix/monitor.py`. This is the fixed destination, not an optional placement in `catalog.py`. |
| Application backend contracts and their catalog/launch/registration result records | `platform/applications/backend.py`. This is the sole common contract module; keep `types.py` for canonical application values and `associations.py` for association semantics. Do not retain forwarding modules for the superseded contract/type files. |
| `platform/applications/discovery.py` | `platform/applications/implementations/unix/discovery.py`. Move `DiscoveryResult` to shared `backend.py`, and cross-platform native-handle adaptation helpers to shared `handle_adapters.py`; neither shared file may import the Unix discovery implementation. |
| `platform/applications/entries.py` | `platform/applications/implementations/unix/entries.py`. Consumers register launchers through the common registration contract. |
| `platform/applications/flatpak.py` | `platform/applications/implementations/unix/flatpak.py`. Keep independent environment/sandbox helpers in `platform/environment/flatpak.py`. |
| Unix root discovery/launcher construction currently in `platform/applications/steam.py` | Extract to `platform/applications/implementations/unix/steam.py`. Keep VDF parsing and neutral Steam values/lookup policy in shared `steam.py`; inject concrete discovery. Preserve the existing discovered libraries and launch results. |
| Planned `platform/applications/unix_launch.py`, `desktop_launch.py` and concrete launching extracted from `launcher.py` | `platform/applications/implementations/unix/{launch,desktop_launch}.py`. |
| Planned `platform/applications/unix_registration.py` | `platform/applications/implementations/unix/registration.py`. |
| `platform/applications/linux.py`, `bsd.py` | `platform/applications/implementations/linux/process.py`, `platform/applications/implementations/bsd/process.py`. |
| `platform/status_notifier/backend.py`, `dbusmenu.py` | Concrete SNI implementation in `platform/tray/implementations/unix/{status_notifier,dbusmenu}.py`. Neutral state/menu values belong in the shared tray package. |
| Planned `platform/tray/unix.py` | `platform/tray/implementations/unix/backend.py`, adapting SNI to the common tray contract. |
| Shared polling/bridge code from `platform/status_notifier/{service,notifications}.py` | `platform/tray/{service,notifications}.py`. Remove the old package after migrating callers and bootstrap test doubles; do not retain import-only compatibility modules. |
| `applets/systemtray/xembed.py`, planned `unix_presentation.py` | `applets/systemtray/implementations/unix/{xembed,presentation}.py`; `systemtray/presentation.py` remains the ABC. |
| Portal implementation from `search/services/global_shortcuts.py` | `search/services/implementations/unix/shortcuts.py`. Retain shared status values and the lifecycle ABC in `global_shortcuts.py`. |
| `search/services/x11_shortcuts.py` | `search/services/implementations/unix/x11_shortcuts.py`. This is a Unix Search transport; it does not choose the window-management backend. |
| `platform/backends/native_windows.py` | `platform/backends/window_projection.py`, with the explicitly planned `SnapshotWindowService` rename. |
| `applets/windowkiller/state.py` POSIX termination implementation | `platform/backends/unix/processes.py`; it is shared by Unix picking backends, not an applet implementation. |

`platform/desktop/{base,selection}.py` defines and selects only the narrow desktop adapters. GUI-dependent lifecycle/file implementations do not move into `platform/host/`. The existing application contract modules, common facades, composition modules, session packages, and UI modules retain the locations shown in the final tree.

### Applet implementation moves

For an applet named `name`, shared `__init__.py`, `applet.py`, `state.py`, `render.py`, `backend.py`, and domain value/helper modules remain under `applets/name/`. Each target below is relative to that applet directory. `backend.py` at the root is the contract; `implementations/<family>/backend.py` is concrete behavior. Existing implementation class names need not change.

| Applet | Current implementation or responsibility → canonical destination |
|---|---|
| `battery` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; desktop settings and UPower I/O → `implementations/unix/{desktop_settings,upower}.py`. Keep `PeripheralBattery` and tooltip/value logic in root `peripherals.py`; move its native reads into the Unix implementation. |
| `bluetooth` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; planned `unix_desktop.py` → `implementations/unix/desktop.py`. |
| `brightness` | Extract xrandr/brightnessctl selection and command behavior from `state.py` to `implementations/unix/backend.py`; put sysfs read/write/discovery in `implementations/linux/backlight.py`, supplied as a capability by Linux composition. Preserve the existing precedence between command and sysfs operations. BSD retains its existing X11/tool behavior, not a Linux sysfs import. |
| `caffeine` | Screensaver/systemd external-service implementations from `inhibit.py` → `implementations/unix/backend.py`. Preserve availability-based tool selection, including a missing systemd tool on BSD. Shared inhibition contract/composite/request coordination stays in `inhibit.py`. |
| `camshield` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; `unix_client.py`, `unix_control.py`, `unix_devices.py` → `implementations/unix/{client,control,devices}.py`; concrete Unix helper implementation → `implementations/unix/helper.py`. Preserve root `helper.py` as the installed helper entry point. |
| `capslock` | `xset` implementation from `state.py` or PR `linux.py` → `implementations/unix/backend.py`. X11 lock-state tools are not a Linux kernel feature. |
| `keyboardlayout` | Existing GNOME/IBus/Fcitx5/MATE/XKB implementation classes → `implementations/unix/backend.py`; root `state.py` keeps only pure values/policy. |
| `micshield` | Pactl operations and settings → `implementations/unix/backend.py`; PulseAudio protocol support is shared Unix behavior. |
| `music` | MPRIS/playerctl/Rhythmbox implementation classes → `implementations/unix/backend.py`; retain shared artwork coordination and pure state at the root. |
| `network` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; desktop-tool helpers → `implementations/unix/desktop_settings.py`. The existing NetworkManager/procfs implementation remains one Linux backend during this move; do not rewrite its state policy. |
| `notifications` | Dunst/GNOME/null native implementations → `implementations/unix/backend.py`; keep their selection policy in that concrete Unix implementation. Activity-monitor process/thread ownership and D-Bus stream parsing → `implementations/unix/activity.py`; the applet receives semantic callbacks through its activity-monitor contract. |
| `powerprofiles` | Power-profiles-daemon/tuned/TLP/null implementations → `implementations/unix/backend.py`; preserve service/tool availability precedence. |
| `screenshot` | Portal/tool/Flatpak capture implementation → `implementations/unix/backend.py`; pure mode and presentation values stay shared. |
| `session` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; planned `unix.py` command execution and lock helpers → `implementations/unix/actions.py`. Shared action IDs/labels remain at the root. |
| `trash` | Gio/GNOME/MATE/Cinnamon/KDE classes and filesystem/mount watching → `implementations/unix/backend.py`; root `backend.py` retains only the contract/shared result values. |
| `volume` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; `pulseaudio.py`, `command.py`, desktop settings → `implementations/unix/{pulseaudio,command,desktop_settings}.py`. Preserve PulseAudio-first and OS-specific fallback order. |
| `systemmonitor` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; concrete temperature readers → matching `implementations/{linux,bsd}/temperature.py`. Keep pure calculations/value types shared. Any existing GPU sysfs access belongs in `implementations/linux/gpu.py`; portable vendor-command access belongs in `implementations/shared/gpu.py`, with shared GPU values retained at the root. Preserve the same enabled readers; do not add telemetry. |
| `thermals` | `linux.py` → `implementations/linux/backend.py`; `bsd.py` → `implementations/bsd/backend.py`; move concrete sensors/sysfs readers out of shared `state.py` into the Linux implementation, retaining pure parsing and formatting shared. |
| `webapp` | Concrete `browser.py` WebKit host or planned `linux.py` → `implementations/unix/backend.py`, including its private session-directory and download-path helpers; concrete desktop notifications or planned `linux_notifications.py` → `implementations/unix/notifications.py`. Remove root `browser.py`; callers import contracts from `backend.py` and profile values from `profile.py` directly. No separate root `storage.py` is needed. |

This table resolves all earlier "Unix implementation module" placeholders. For screenshot, keyboard layout, Music, notifications, power profiles, Mic Shield, Caps Lock, and WebKit hosting, PR files named `linux.py` map to the Unix destination above because the implementation is a desktop protocol/tool adapter, not Linux kernel code.

### Implementation refinements

Phase 1 is implemented on `refactor/platform-interfaces`, based on master
`39a06684`. Record the reviewed Phase 1 commit before starting Phase 2. The
implementation makes these reviewed boundaries concrete:

- Owned platform interfaces are ABCs with required abstract operations. Native
  adapters inherit them explicitly. Required `family`, `name`, `uri`, and
  `supports_clear` metadata uses abstract read-only properties, implemented by
  concrete properties or class constants. Unix host/provider/applet factories
  intentionally remain abstract until the Linux/BSD subclasses complete them.
  Thin adapters retain implementation-first inheritance and existing static
  aliases. `ThermalsBackend` now enforces `get_snapshot()` at construction while
  keeping its concrete `has_readings()` helper. Thermals, Notifications, and
  Power Profiles control contracts live in their domain `backend.py` modules;
  annotations import state values only during type checking. The thermal
  presentation module keeps its existing GTK dependency, while its contract
  imports without GI. Only Music's
  `HybridBackend` implements `MusicBackend`; its differently shaped native helpers
  do not.
  `BrowserFactory`, `NativeApplicationHandle`, and consumer-view protocols stay
  structural. Phase 2 Windows adapters must inherit and fully implement these
  same ABCs without restoring OS checks in consumers.
- `HostFamily` is resolved by the cached host selector; runtime composition uses
  that result. Shared POSIX primitives remain under the Unix host implementation.
  The old `applets/trash/{linux,bsd}.py` mounted-filesystem readers move to
  `platform/host/implementations/{linux,bsd}/mounts.py`, with host composition in
  the neighboring `backend.py`. Unix Trash asks the narrow host API for mount
  points and performs no OS selection. A Windows host must provide the same
  mounted-filesystem fact through its native volume API.
- Native-backed applets use `PlatformServicesApplet`. Applications, Run Command,
  and Music use the existing `ApplicationServicesApplet`, extended with required
  construction-time `platform_services`. Independent Bluetooth desktop actions and
  Network/Volume settings use typed `operations.py` contracts and corresponding
  factory methods. Stateful reader/control contracts remain in `backend.py`.
- Application contracts and their backend-specific result records share
  `platform/applications/backend.py`. The registry, launcher, identity service,
  native implementations, and composition import their required declarations
  there. Canonical application values and association semantics retain their
  separate modules. This changes import ownership, not runtime selection or
  dependency lifetimes.
- `CamshieldBackend` exposes probe, helper availability, and semantic action
  execution to GTK. Unix privileged ACL/control contracts live in
  `camshield/implementations/unix/control.py`; the public helper entry point calls
  the provider's headless `camera_helper()` boundary. An executed helper requires
  a fresh probe even after a nonzero exit; inability to start it does not.
- Trash owns dropped-file operations as well as file/mount subscriptions behind
  its backend. GTK owns the returned cleanup function and confirmation callback.
- Telegram/WhatsApp profile values live in their respective `state.py` modules
  alongside existing URL metadata. Their former `browser.py` modules and the
  WhatsApp `notifications.py` re-export are removed. The Unix browser owns its
  private storage helpers and concrete notification adapter; shared browser
  contracts and the `BrowserProfile` value type remain at the domain root.
- Notifications creates an activity monitor through its native operations API.
  The Unix adapter owns `dbus-monitor`, its reader thread, and Flatpak host PID
  cleanup. The applet retains history, badges, and presentation timers.
- Tray uses one required backend factory and a serial worker with generation
  guards. Consumers import canonical `platform/tray/` modules; the former
  `status_notifier/` package and its compatibility aliases are removed. System
  Tray and its launcher-overlay bridge borrow the same shared service.
- Headless import, explicit dependency, association status, and owned-worker
  guards are in `tests/platform/test_platform_interfaces.py`. Regression fixtures
  construct native adapters explicitly; they do not restore OS selection to
  shared production modules. The type checker resolves imports from the checkout
  to prevent installed releases from hiding incomplete interface migrations.
- `tests/platform/test_abstract_backends.py` verifies every required operation
  on all 38 converted contracts and nominal inheritance/completeness for the
  native adapters. Backend test doubles inherit the relevant contract or use
  mocks with contract specs. Unexpected operations on narrow fakes fail loudly.
- The final module review removes import-only shims and single-caller AI usage
  forwarding modules. Shared camera device helpers expose
  `enumerate_video_devices()`/`normalized_path()`; shared audio command execution
  exposes `run_command()`. PulseAudio keeps its concrete `BACKEND` value and
  private callbacks. Keep small modules that own real contracts, reusable value
  types, cross-OS operations, or required composition adapters. Patch shared
  browser behavior at its owning WebApp module rather than re-exporting GTK
  dependencies from the WhatsApp applet for tests.

### Phase 1 completion extracted from the Phase 2 review

The initial implementation notes above describe #480. Reviewing #481 identified
additional shared contracts and Unix adapter migrations that must land as a
standalone prerequisite before its Windows integration. This completion changes
the earlier extraction details as follows; these are Phase 1 responsibilities.

| Modules (relative to `docking/`) | Completion and behavior to preserve |
|---|---|
| `platform/composition/{base,unix,linux,bsd}.py`, `app.py`, `applets/{platform,services,base}.py` | Compose `ApplicationSources` as a fresh catalog/association/registration graph. Pass its narrow capabilities into the registry. Supply one backend per applet domain, retaining construction-time ownership and late delivery of session services. |
| `platform/applications/{backend,types,registry,metadata,handle_adapters}.py` | Make discovery readiness, ordered association reads/revisions, lazy private launch handles, optional exports, and worker publication explicit. Unix discovery remains synchronous, reports no initial pending state, and preserves registered/default/ID-less handlers. A failed refresh preserves the published snapshot. |
| `platform/applications/implementations/{unix,shared}/` | Move the Gio observer to `shared/monitor.py`; keep desktop-file discovery, associations, launch commands, and registration in Unix. Preserve lazy association ordering and direct Exec/Flatpak/Gio launch precedence. Each observer lifetime rejects stale callbacks. |
| `platform/applications/{launcher,listing,identity,matcher,recents}.py`, `platform/{model,targets}.py` | Consume the new contracts without selecting implementations. Preserve persisted IDs, provenance, aliases, recent-app policy, and failed-launch results. Uncataloged executable matching is an explicit opt-in; Unix keeps it disabled. |
| `core/paths.py`, `platform/host/{base,implementations/{unix,linux,bsd}/*}.py` | Put explicit-path syntax and platform facts behind the host API. Retain Unix URI expansion, XDG directories, POSIX `flock`, and directory flushing. The lock's new path argument does not change POSIX locking. |
| `platform/desktop/{base,implementations/unix/{backend,diagnostics}}.py`, `platform/environment/environment.py` | Move X11 compositor diagnostics into the Unix desktop adapter. Optional native artwork returns `None`, and native text-modifier handling returns `False` on Unix. Keep headless imports free of GTK/native initialization. |
| `platform/backends/{base,gtk_geometry,gtk_surface,window_projection}.py`, `platform/backends/{reduced/services,x11/services/surface}.py` | Define surface capabilities and logical rectangle projection. Reuse GTK dock hints/placement with their existing values. Preserve X11/Wayland/Cinnamon selection and mixed XWayland session behavior. |
| `platform/{application_icons,icon_source,icons,keyboard}.py`, `applets/applications/{applet,icons,render}.py`, `search/icons.py` | Share artwork resolution and optional readiness subscriptions. Unix retains theme/Gio fallbacks. Canonical application drag data carries an ID that the destination must resolve; arbitrary bytes cannot manufacture catalog entries. |
| `applets/{battery,bluetooth,network,volume}/` | Finish the domain backend ABCs and move peripheral parsing/settings/native commands into their existing Unix/Linux/BSD implementations. Remove separate Bluetooth/Network/Volume operation factories. Preserve command precedence, polling, traffic counters, mute policy, and zero-percent peripheral readings. |
| `applets/brightness/{applet,backend,state,render}.py`, `applets/brightness/implementations/{unix/backend,linux/backlight}.py` | Query and write through a stateful backend off GTK. Preserve xrandr/backlight fallback, 2% steps, the last valid same-display reading, and sub-one-percent poll tolerance. Invalidate stale writes when the target changes and create a fresh backend after restart. |
| `applets/{capslock,keyboardlayout,micshield,music,powerprofiles}/` | Put native acquisition, parsers, commands, settings actions, and resource cleanup in the implementation; keep common values/rendering at the root. Retain GNOME/IBus/Fcitx/MATE/XKB ordering, PulseAudio/MPRIS behavior, and power-profile fallbacks. Queue requests against their originating backend lifetime. |
| `applets/{caffeine,camshield,notifications}/` | Expose semantic controls/state instead of native operation bundles. Keep privileged camera-helper semantics, inhibitor cleanup, and `dbus-monitor` ownership in Unix adapters. Merge notification activity into the backend, retaining bounded history and applet-owned badge/history timers. |
| `applets/screenshot/{applet,backend,state,preview,selection}.py`, `implementations/unix/backend.py` | Route availability/capture/cancellation through the backend. Unix keeps its existing tool order, portal/Flatpak behavior, arguments, delays, and native tool UI. Shared selection/preview capabilities do not replace those Unix tools. |
| `applets/{webapp,telegram,whatsapp}/` | Keep browser callbacks, collision handling, session values, and common errors at the WebApp root; keep WebKitGTK/storage/native notification work in Unix. Preserve profile directories, cookies, notification routing, and disconnect behavior; do not restore import-only wrappers. |
| `applets/aiusage/{hooks,hook_runner,store,backends/*,implementations/unix/hooks}.py` | Separate headless registration policy from provider schemas and host locking/argv. Preserve Unix hook commands, legacy Codex recognition, Claude event registration, and data files without importing GTK. |
| `applets/{session,trash,ambient,deskpresence,colorpicker,windowkiller}/` | Migrate consumers to the common session/state/backend capabilities while preserving existing native commands and unsupported results. Remove obsolete root-native helpers after callers move. |
| `platform/tray/{backend,state,menu,service,notifications}.py`, `implementations/unix/{backend,status_notifier,dbusmenu}.py` | Translate SNI/DBusMenu records into neutral tray values. Preserve watcher/proxy/menu behavior, three-second polling, overlay notifications, and menu ordering. Serialize predecessor close and successor reads on workers; reject obsolete timers, commands, and focus deliveries. |
| `applets/systemtray/{applet,state,presentation,implementations/unix/*}.py`, `applets/popup.py` | Move legacy XEmbed ownership, take-over/release UI, positioning, and errors into the Unix presentation. The applet borrows its construction-time shared tray service and does not replace or stop it when late services arrive. |
| `search/{controller,intents,preview,providers/*,services/*,ui/*}.py` | Consume focus, shortcut, path, artwork, and presentation capabilities. Unix keeps portal plus X11 fallback, no native focus adapter, and `opaque_action_panel=False`; its existing transparent GTK styling remains. `providers/windows.py` means searched desktop windows and remains shared. |
| `ui/{display,dnd,dock_window,factory,hover,input_controller,menu,preview,renderer,runtime,tooltip}.py` | Route geometry, drag, modifier, preview, placement, and focus policy through capabilities rather than OS probes. Keep default Unix behavior and subscription/timer teardown. |
| `tests/`, `features/steps/folder_thumbnail_steps.py`, `tools/linux/visual/probes/wayfire_contract_probe.py` | Migrate callers and patch locations together. Keep generic/Unix assertions on the prerequisite branch and Windows-native cases on #481. Ensure the prerequisite suite collects with Windows implementations absent. Keep isolated no-GI smoke runs separate from normal collection. |
| `.github/workflows/bsd.yml`, `docking/locale/` | Carry the BSD NumPy test dependency and shared camera/microphone translations with their Unix changes. Regenerate the prerequisite POT from its own source tree; Windows-only messages belong to Phase 2. |

The standalone prerequisite must contain no Windows implementations, native
helpers, package/build changes, OS-routing branches, or Windows acceptance tools.
After it is committed, merge it into the integration branch without rewriting
published history and base #481 on that prerequisite. The resulting Phase 2
diff must contain no Unix/Linux/BSD runtime-adapter changes. Preserve the tested
integration implementation while splitting mixed test files; the split must not
drop Windows assertions from the final combined tree.

### Phase 1 import and test migration

- Update domain consumers to import contracts/shared values and receive the concrete dependencies. Update concrete implementation imports to their canonical packages.
- In implementation tests, construct the canonical Unix/Linux/BSD adapter directly. Patch native calls at their new owning module. Pure state tests continue importing root `state.py`.
- Migrate Behave callers in `features/steps/` too, including System Monitor sampling and folder-thumbnail construction. Reuse explicit applet/target test factories. Tests that read the actual host process use the process backend from composition; the deterministic Linux `/proc` fixture factory must not be used for native BSD assertions.
- Extend architecture checks to the full `implementations/` subtree, including imports inside functions and runtime import strings. Shared application/host contracts must import without the Unix implementation present.
- Preserve source and installed hook/helper commands. Their small public entry-point wrappers forward to the selected concrete code without loading the GUI runtime.
- Check packaging/module discovery and all documentation/entry-point references touched by a move. Do not combine these moves with configuration migration, class-wide renaming, or fallback changes.

### Phase 1 local verification

The implementation checkout is based on `39a06684`. The unchanged master
baseline passed 8,226 default tests under Xvfb and an isolated D-Bus session,
with 19 skips and 127 visual tests deselected.

The local verification also covers:

- A final full default run with 8,334 passed, 19 skipped, and 127 visual tests
  deselected, including the ABC contract/inheritance guards. The 16 existing
  Pillow deprecation warnings match master. All 55 focused headless-import,
  dependency/lifetime, and Telegram browser checks also passed after the final
  test-double updates, as did all 4 fresh-process bootstrap smoke tests.
- After consolidating application/native construction in
  `ApplicationServicesApplet`, all 293 affected applet, model, bootstrap, and
  dependency tests passed, including real construction of Applications,
  Run Command, and Music through the model.
- After consolidating application contracts/results in `applications/backend.py`,
  all 2,207 affected platform, Search, integration, applet, and bootstrap tests
  passed (3 skipped), as did the 4 standalone bootstrap smoke tests. All ten
  moved class declarations retain their fields, methods, decorators, and
  signatures.
- The ABC review preserved all 185 existing contract signatures and five
  metadata types, plus all 527 non-contract function/method definitions in the
  64 affected production modules. All 24 unrelated structural protocols were
  retained; 37 owned protocols became ABCs and the existing Thermals base gained
  abstract-method enforcement. No implementation fields or result records
  changed. The new contract/inheritance tests cover 38 contracts and 53 native
  adapter declarations, including the three intentionally abstract Unix bases.
- All 878 tests in `tools/test_gui_headless.sh`.
- All 112 Behave scenarios (516 steps), including explicit platform dependencies
  in System Monitor sampling and folder-thumbnail steps. Native-host process
  identity checks use composition rather than the Linux filesystem fixture.
- Fresh-process bootstrap smoke tests, import/dependency guards, association
  outcomes, native worker finalization, tray restart/stale replies, and mixed
  Cinnamon/XWayland menu policy.
- The repository's Ruff, type, dead-code, translation-template synchronization,
  and translation-packaging checks.
- A wheel built from a clean source snapshot with both packaging configuration
  files. Its 734 Python modules match the source exactly, with no obsolete
  modules. The installed wheel starts on X11, responds over IPC, and shuts down
  cleanly on SIGTERM. Its camera helper imports and parses arguments without GI.

The final review compared the extracted implementations and their wiring against
master, including command/default-handler precedence, generated identities,
browser profiles/storage, restart ownership, and partial-startup teardown. It
confirmed unchanged bodies throughout the extracted native implementations and
unchanged annotated field definitions in 114 shared state/configuration classes;
changed bodies and construction paths were reviewed separately. The review found
and corrected a signal-cleanup regression: after SIGTERM, GLib removes the returning
signal source, so teardown now removes only the remaining live sources.

The subsequent module-ownership review moved three unchanged ABC declarations
into their owning backend modules, removed five forwarding/compatibility
modules, and exposed shared native helper names. It preserved 186 retained
function/method definitions apart from those explicit renames. The only other
body changes delegate the AI usage store lock and two hook-command requests
directly to the same host methods that the removed wrappers called. All 562
focused checks passed (5 skipped); bootstrap fakes and browser patch targets now
refer to the actual owners. A source scan found no remaining import-only modules
outside package initializers. The retained small modules own interfaces, shared
values, native operations, or required composition adapters.

Visual verification ran the same 127 tests on both trees: each reported 112
passes and the same 15 existing golden-image mismatches. With GTK animations
disabled for a separate deterministic comparison, all 28 rendered cases matched
master pixel for pixel. Existing golden images and thresholds were retained.

Native FreeBSD/OpenBSD and compositor desktop acceptance were unavailable in
this environment. Run the existing CI desktop profiles before merging Phase 1;
local mocked BSD/native API tests do not replace those acceptance runs. Record
the accepted Phase 1 commit before adapting the Windows branch.

---

# Phase 2 — Adapt PR #477 to the completed interfaces

## 2.1 Integration procedure

Start from the completed, accepted Phase 1 master.

1. Record the actual Phase 1 revision and current PR head.
2. Compare every PR runtime change against its corresponding Phase 1 module.
3. Retain Phase 1’s contracts and ownership model.
4. Port each Windows implementation and its behavior into those contracts.
5. Remove duplicated extractions, selectors, and compatibility layers.
6. Port the PR’s required shared feature extensions explicitly.
7. Run both Linux/BSD regression checks and the complete Windows acceptance workflows.

Do not resolve conflicts by taking either branch’s entire shared file. Do not cherry-pick a large Windows commit and discard conflicting sections without tracing their behavior.

The following inventories cover the runtime module groups changed in the inspected PR. A listed native helper may require only dependency adaptation and verification; it does not require a rewrite.

## 2.2 Bootstrap, host, and common runtime modules

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `platform/host/windows.py` — new | Implement Windows paths, directory defaults, flushing behavior, hook/process primitives, and writer locking. Keep GUI imports out of the headless host layer. |
| `platform/composition/windows.py` — new | Register Windows application, applet, Search, tray, and session factories. Reuse Phase 1 contracts. |
| `platform/{host,composition}/selection.py` | Add Windows selection centrally. Validate incompatible backend overrides before importing foreign implementations. Keep Windows reduced mode with Windows host/application services. |
| `launcher.py`, `app.py` | Integrate Windows preparation, signals, subscriptions, and resources through composition. Remove the PR’s shared `sys.platform` branches. Preserve registry-before-config and consumer-before-provider teardown. Preserve deferred first-run defaults: only reconcile after catalog and associations resolve, and abandon reconciliation if the user has changed the provisional pin list. |
| `platform/environment/{environment.py,xdg.py,__init__.py}` | Remove duplicated Windows dispatch now handled by host services. Preserve Windows desktop diagnostics and XDG environment overrides. |
| `core/paths.py`, `platform/files.py` | Port Windows drive/UNC URI semantics into the Windows path/file adapters. Preserve NUL rejection, authority validation, percent decoding, and Gio’s UNC workaround. Keep POSIX results unchanged. |
| `core/config.py`, `core/theme/migration.py` | Use Windows directory-flush behavior without weakening file flushing, backup recovery, atomic replacement, or POSIX directory syncing. |
| `platform/model.py` | Integrate asynchronous catalog readiness, pending restored pins, metadata/icon refresh, and native executable registration. Preserve pin order, item identity, custom overrides, recent apps, and live-window continuity. |
| `applets/{base.py,services.py,platform.py}` | Extend the stable/late service groups only where the native implementation requires it. Do not reintroduce fallback selection or temporary service ownership. |
| `applets/worker.py` | Reuse Phase 1’s serial worker; reconcile the PR’s guarded-worker changes without losing completion/error cleanup. Preserve worker-thread finalizers after GTK exits. |
| `platform/{application_icons.py,icon_source.py,icons.py}` | Add the PR’s native pixel source as an explicitly supplied factory. Preserve lazy worker creation, readiness notifications, cache invalidation, custom/model icon precedence, and cleanup. |
| `platform/keyboard.py` | Implement the native modifier service through Windows composition. Shared shortcut capture must not select its own OS implementation. |

## 2.3 Windows application and catalog modules

Paths are under `docking/platform/applications/` unless stated otherwise.

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `windows/{__init__.py,catalog.py,discovery.py}` | Implement the Phase 1 catalog contract. Preserve Gio/shell merging, stable IDs for ID-less native records, shell visibility, association-only clients, and duplicate handling. |
| `metadata.py` | Preserve the shared worker for Win32 Gio discovery, native shell metadata, and association acquisition. Carry source discovery, association IDs/results/revisions, cancellation, coalescing, lifecycle epochs, request generations, and bounded requests. Retain accepted source/handles on temporary acquisition failure. Reconcile with the Phase 1 observer; do not add another worker or synchronous GTK lookup. |
| `backend.py`, `registry.py` | Extend results with explicit native readiness where required. Preserve `None` as unavailable/pending and an empty tuple as successful empty. A failed refresh must not publish false completion. Adapt the PR's separate contract/result modules into this single common declaration module; do not restore their OS-selecting factories. |
| `associations.py`, `backend.py`, `windows/associations.py` | Retain the Phase 1 result contract and adapt the new Windows reader to the same metadata worker. Preserve pending/ready-empty/unavailable, ordered default/recommended/all reads, exact directory AppID/name lookup, URI-scheme/content-type validation, hidden-ID filtering, and retained handles for association-only applications. |
| `types.py` | Port `launch_target`, `document_target`, `export_file`, and `export_data` with their PR meanings. Do not overload Unix `exec_line` or reinterpret native targets as shell command strings. |
| `windows/{launch.py,process.py,registration.py}` | Implement the Phase 1 launch, process, and registration contracts. Preserve native shell launches, selected-document activation, process evidence, PE checks, shortcut import, and stable executable IDs. |
| `windows/export.py` | Preserve native launch-file export and drag data. Retain shortcut arguments, working directory, shell target, icons, elevation options, and registration generation safety. |
| Registration declarations in `backend.py` | Reconcile the PR’s executable-format and ID helpers with the Phase 1 registration interface. Common matchers use the supplied registration strategy. |
| `backend.py`, `launcher.py` | Retain Phase 1 orchestration; integrate Windows outcomes and provenance. Remove duplicate OS factories. |
| `unix_catalog.py`, `unix_launch.py`, `unix_registration.py`, `desktop_launch.py`, `discovery.py`, `entries.py` | Keep Phase 1’s Unix implementations. Port only required PR fixes. Preserve generated-launcher behavior and existing Linux/BSD command semantics. |
| `backend.py`, `identity.py` | Inject `WindowsProcessBackend`; remove the PR’s repeated selection. Preserve shared launch provenance and PID evidence. |
| `matcher.py` | Port validated unlisted-executable matching. Preserve ambiguity handling and stable runtime-to-pinned identity. Do not make all platforms permissively match arbitrary executables. |
| `recents.py` | Preserve records while discovery is pending, then apply normal resolution policy after completion. Keep runtime-only exclusion and persisted history semantics. |
| `listing.py`, `drag.py` | Port bounded application-ID transfer and exported-launcher resolution. Preserve filesystem URI handling and reject malformed IDs before registry resolution. |
| `docking/platform/targets.py` | Keep the required registry dependency and use cached association resolution for directory-handler names. Pending/unavailable results return no name; do not block GTK, infer a handler from its executable/display name, or bypass the registry with a native lookup. |
| `docking/applets/music/applet.py` | Preserve one pending media-launch intent and its registry subscription. Retry on association publication, suppress repeated pending clicks, and clear the intent when media becomes active or the applet stops. Unsubscribe on stop and resubscribe once on restart. |

**Critical readiness scenario:** a saved Windows pin must survive initial native discovery being unavailable, become populated when metadata arrives, and be removed only under the established completed-discovery policy—not because an intermediate worker returned no data.

### Association reads and worker ownership

- Share the catalog's existing metadata worker with the Windows association adapter. Inject that shared lookup during construction; the public association contract must not force consumers to create or manage a concrete worker. Keep Unix acquisition synchronous with the same result vocabulary.
- Preserve `AssociationKey` lookup kinds `default`, `recommended`, `all`, and `name`. A pending higher-priority query prevents choosing a lower-priority result prematurely. An unavailable lookup is distinguishable from a successful query with no handler; fallback may select only a verified requested handler.
- Bound remembered association requests at the PR's 256-key limit. Preserve lifecycle/request generation cancellation and the metadata lookup's `association_revision`. Publish association changes to registry listeners even when the visible application generation has not changed.
- Run default Win32 `Gio.AppInfo.get_all()` acquisition on the worker. Readiness requires both the native shell catalog and the default-source discovery to have completed. An explicitly injected source remains authoritative and must not trigger default enumeration.
- Carry private native AppInfo handles/source snapshots through the established publication path; perform registry listing/token materialization on the registry's GTK thread. Preserve accepted sources/handles on a temporary native read failure and reject superseded deliveries. Do not share worker-owned COM interfaces with GTK.
- Resolve directory association AppID and friendly name through the exact `Directory` open verb and documented Folder fallback. Keep bounded sizing/value queries, distinguish errors from absent associations, and never infer identity from an executable basename or display label. Preserve URI-scheme validation and Windows MIME/native-extension conversion.
- Include requested association IDs in hidden-ID resolution even when they are absent from the normal catalog. Preserve Unix canonical NoDisplay default behavior separately from native hidden-application filtering.

### Observer recovery and cache convergence

- Retain Programs, CommonPrograms, and AppsFolder subscriptions. Register the main-context observer before requesting native resolver work; no native catalog enumeration belongs in monitor construction or the GTK heartbeat.
- Keep the 500 ms Explorer identity observation as a shell-lifetime check, not repeated application enumeration. Reconnect every owned folder subscription when the shell changes, including when `TaskbarCreated` is missing or only one subscription receives it first.
- Preserve finite reconciliation after real changes: the current adapter allows a 30-second convergence window and schedules retry eligibility one second after complete resolver calls. Successful but stale results and failed reads may both need a retry. Do not implement a permanent one-second catalog poll.
- Bracket the complete resolver with `read_started()` / `read_finished(ticket)`. Reserve activity before checking cancellation, capture the monitor instance for that resolver lifetime, and finish that captured instance in `finally`. An old worker must not report completion to its replacement monitor.
- Wait until all in-flight resolver calls complete before reconciliation invalidates again. Coalesce real events and avoid timer-driven cancellation that starves a slow resolver. Stop/reentrant-close must remove pending idle/timer sources, close all subscriptions, and make late tickets/callbacks inert.

### Deferred first-run defaults and media actions

Keep first-run catalog readiness and association readiness separate. New configuration may initially contain the ordinary non-application defaults while either source is pending. When both resolve, replace that provisional list only if it still exactly matches the originally recorded provisional pins. Existing configuration and user edits must never be overwritten by late completion. An unavailable association must not remain pending forever.

Music retains at most one pending default-player launch. Retry it on association publication, not on repeated user clicks. Active media or applet stop cancels the intent, and stop removes the registry listener. Restart creates only one subscription. Preserve immediate Unix launches when synchronous reads are ready.

### Complete selected-application activation

The updated native `documents.py`, `selection.py`, `associations.py`, and `dataobject.py` behavior is part of Phase 2's preservation baseline:

1. Validate the complete ordered selection before activation. Preserve literal Unicode/UNC filenames and unmodified supported protocol URIs. Reject invalid/NUL targets and unsupported mixed or multi-protocol selections without partial opening.
2. For packaged apps, retain the exact selected AppID. Fall back to registered Shell handlers only for the missing activation contract (`0x80270254`), using the same full identity. Never open the default application after a selected-native attempt fails.
3. Enumerate handlers with coherent HRESULT/count/pointer checks, bounded at 256 entries. A handler without an explicit matching AppID, multiple matching handlers, malformed enumeration, or a missing interface cannot authorize invocation. The narrowly recognized unassigned-identity case must not hide a later exact match.
4. Build one complete selection data object, including cross-directory PIDLs. Preserve the one-MiB CIDA bound, Unicode `CF_HDROP` encoding, and the single-protocol URL format. Deduplicate extension lookup without reordering, truncating, or rewriting the selected paths.
5. Require the handler's invoker to support the complete selection. Preserve exact success-status handling for `CreateInvoker`, `SupportsSelection`, and `Invoke`; a nonnegative refusal is not proof of launch. After an actual invoke attempt, do not retry another extension or split the selection.
6. Register cleanup before native calls that can return populated outputs on failure. Release partial PIDLs, COM objects, strings, data objects, and invokers on failure/interruption. Activation-manager PIDs are not owned process instances and must not create false launch provenance.

## 2.4 Windows session and native platform modules

Source/reference paths in this table are under PR `docking/platform/backends/windows/`. Section 2.12 assigns their canonical final destinations; only session services remain in that package.

| Modules | Required adaptation and behavior to preserve |
|---|---|
| `__init__.py`, `session.py` | Register through Windows composition. Supply the shared registry/process services and native session resources. Keep capability reporting dynamic where native availability changes. |
| `startup.py` | Preserve per-monitor DPI preparation, installed runtime resource resolution, DLL-directory handle lifetime, and OLE2 drag environment setup before GTK opens its display. |
| `ffi.py`, `com.py`, `com_callback.py`, `winrt.py`, `winrt_collections.py` | Retain native ABI definitions, ownership, apartments, callback lifetime, error conversion, and release order. Do not move these into shared interfaces. |
| `windows.py`, `properties.py`, `appresolver.py`, `ghosts.py`, `packages.py` | Preserve enumeration, root/owned/ghost handling, application identity, packaged/unpackaged applications, urgency/fullscreen data, and grouping/cycling. Use the injected Phase 1 services. |
| `surface.py`, `appbar.py`, `geometry.py`, `visibility.py`, `close_hook.py` | Preserve reservations, external workarea, mixed-DPI/negative-coordinate placement, fullscreen transitions/recovery, native stacking, close handling, and overlap policy. |
| `capture.py`, `previews.py`, `screen_capture.py` | Preserve asynchronous graphics capture, resource ownership, request identities, bounded caches, stale-result rejection, preview availability callbacks, and color coordinates. |
| `picking.py`, `process_job.py` | Preserve selection/PID identity validation and process ownership. Do not broaden which process may be terminated or managed. |
| `desktop.py`, `workspaces.py`, `virtual_desktops.py` | Preserve supported-build validation, workspace operations, current-workspace filtering, and graceful unsupported states on unknown native ABIs. |
| `idle.py`, `power.py` | Implement existing idle and power operations through domain interfaces. Preserve units, thread requirements, and resource release semantics. |
| `menus.py`, `input_context.py`, `input_profiles.py`, `input_service.py`, `input_thread.py`, `input_hook.cpp`, `modifiers.py`, `hotkeys.py` | Preserve native foreground/typing-target identities, profile validation, cancellation, architecture-specific helpers, dead keys, IME/preedit state, AltGr detection, hotkey conflicts, and layout-change recovery. |
| `shell.py`, `launching.py`, `documents.py`, `shortcuts.py`, `selection.py`, `associations.py` | Preserve exact directory association queries and complete selected-application activation described in section 2.3, native shell/shortcut semantics, quoting, and activation identity. Use the Windows path adapter consistently. |
| `catalog.py`, `catalog_monitor.py`, `catalog_shortcuts.py`, `shell_changes.py` | Preserve native catalog construction/change monitoring, shortcut/reparse handling, Explorer subscription recovery, bounded post-change reconciliation, generation/read-ticket safety, and observer teardown. |
| `icons.py` | Implement the injected pixel-source contract. Preserve asynchronous decoding, image format/alpha, bounded caching, readiness callbacks, and worker teardown. |
| `drag.py`, `drag_source.py`, `dataobject.py` | Preserve native drag formats, source lifetime, external COPY semantics, internal reorder behavior, and drop completion. |
| `toasts.py`, `webview.py` | Preserve notification identity/activation and WebView2 loader discovery, native callbacks, ownership, and runtime availability. |
| `installation.py` | Preserve installed hook discovery/removal validation and stable launcher behavior across upgrades. Keep native entry-point guards local. |

Do not replace native failures with generic success/no-op results to satisfy a common interface. Preserve each operation’s existing unsupported, missing-target, or failed result.

## 2.5 Shared backend changes from the PR

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `platform/backends/base.py` | Add the Windows display family and real shared extension hooks needed by consumers: popup preparation/restoration, managed popup policy, capture overlay preparation, source-window capture, native file drag, screen rectangle conversion, input-region alpha, and monitor workarea. Document ownership and coordinate units. |
| `platform/backends/gtk_geometry.py`, `gtk_surface.py` | Retain common GTK coordinate/hint/placement helpers. Preserve logical coordinates and existing hint order. |
| `platform/backends/reduced/services.py`, `platform/backends/x11/services/surface.py` | Implement the common GTK rectangle behavior and preserve existing reduced/X11 placement. Keep unsupported Windows-only hooks explicitly unsupported. |
| `platform/backends/window_projection.py`, `platform/backends/kwin/bridge.py`, `platform/backends/wayland/sway_ipc.py` | Retain the Phase 1 projection extraction; reconcile duplicate PR renames rather than restoring the old module. |
| `platform/backends/unix/{__init__.py,processes.py}`, `platform/backends/x11/services/picking.py`, `platform/backends/wayland/{niri_picking.py,wayfire_ipc.py}` | Retain Phase 1’s platform-layer process termination and validation. |
| Other existing compositor session/surface implementations | Verify inherited defaults and mixed-session policy. Do not force new Windows behavior onto Unix surfaces. |

Popup focus restoration must retain the PR’s two checks: claim the current request at the final activation boundary and validate again immediately before focus. Stale requests must not map or foreground a popup.

## 2.6 Windows applet modules

Each row also covers the corresponding shared `applet.py`, `backend.py`, and `state.py` where present. Keep Phase 1’s construction and lifecycle model.

| Source/reference modules under `docking/applets/` | Required Phase 2 changes and preservation requirements |
|---|---|
| `battery/{windows.py,windows_peripherals.py,linux.py,bsd.py,desktop_settings.py,peripherals.py,upower.py,render.py}` | Register native battery/peripheral readers. Preserve absent versus unknown battery, unknown percentage, signed power rate, zero-percent readings, charging visibility, and asynchronous peripheral polling. Keep Linux/BSD behavior unchanged. |
| `bluetooth/{windows.py,windows_radio.py,windows_le.py,windows_advertisement.py,windows_gatt.py,windows_pairing.py,windows_pairing_ui.py,unix_desktop.py,linux.py,bsd.py}` | Preserve classic/LE discovery, radio/adapter identity, pairing prompts/helpers, cancellation, capability-specific actions, removal, and device battery data. Shared menus use reported capabilities. |
| `brightness/{windows.py,linux.py,render.py}` | Register native monitor brightness. Preserve monitor-specific control, unavailable readings, ranges, and existing Unix xrandr/sysfs behavior. |
| `caffeine/{windows.py,linux.py,inhibit.py}` | Preserve native inhibition and release requirements. Reconcile shared request coordination with Phase 1; do not lose release during stop or after GTK exits. |
| `camshield/{windows.py,windows_client.py,windows_control.py,windows_elevation.py,windows_helper.py,windows_journal.py,windows_policy.py,windows_sensor.py,windows_sensor_host.py,unix_client.py,unix_control.py,unix_devices.py,helper.py,linux.py,bsd.py}` | Register native camera control and helpers. Preserve elevation, device validation, journal recovery, policy ownership, rollback, sensor worker lifetime, and all Unix ACL/helper behavior. |
| `capslock/{windows.py,linux.py}` | Register native lock-key readings. Preserve unavailable semantics and Unix parser behavior. |
| `keyboardlayout/{windows.py,linux.py}` | Preserve enabled profile enumeration, per-application target context, keyboard/TIP distinction, interaction snapshots, cancellation, and native input verification. |
| `micshield/{windows.py,windows_audio.py,linux.py}` | Preserve capture endpoint/stream attribution, mute behavior, settings, and native object lifetime. Do not confuse microphone control with output volume. |
| `music/{windows.py,windows_audio.py,windows_media.py,linux.py,artwork.py,render.py}` | Preserve selected-session controls, revalidation before writing, player-specific volume attribution, unavailable volume, application identity, and artwork. Never fall back to changing master volume or another player. |
| `network/{windows.py,windows_connections.py,windows_profiles.py,windows_ras.py,windows_vpn.py,windows_wifi.py,desktop_settings.py,linux.py,bsd.py,render.py}` | Preserve WLAN/RAS/VPN identity, saved profiles, connect/disconnect, radio capabilities, counters, unknown signal handling, and settings actions. Do not present unsupported toggles as working. |
| `notifications/{windows.py,linux.py}` | Preserve notification access states, bounded history, quiet-hours validation, revisions, clearing, cancellation, and unavailable/unknown distinctions. Keep native operations worker-owned. |
| `powerprofiles/{windows.py,linux.py}` | Preserve native mode availability and mappings. Shared code operates on reported profiles; Unix daemon/tool precedence remains unchanged. |
| `screenshot/{windows.py,windows_selection.py,linux.py,selection.py,preview.py}` | Port native selection/preview workflow. Preserve source window captured before owned menus, logical/device conversion, region validation, delay, cancel/Escape behavior, output, and cleanup. Retain Unix portal/tool behavior. |
| `session/{windows.py,unix.py,linux.py,bsd.py}` | Register native available actions and action execution. Preserve request-accepted semantics. Stopping the applet must not undo an accepted OS transition. |
| `trash/{windows.py,windows_monitor.py}` | Implement native recycling, counting, opening, emptying, and the Phase 1 change subscription. Preserve shell observation, confirmation, partial failure, and last-known count behavior. Remove the need for a parallel applet-side native watch path. |
| `volume/{windows.py,linux.py,bsd.py,pulseaudio.py,desktop_settings.py}` | Register Core Audio controls and Windows settings. Preserve endpoint recovery and calling-thread COM ownership. Keep Unix mixer/settings precedence unchanged. |
| `systemmonitor`, `thermals`, `pet` | Do not invent native support during integration. Preserve the PR’s existing available/unavailable behavior through explicit adapters; do not accidentally select procfs readers solely because the host is not BSD. |

Optional fields added to shared states—such as unknown percentages, unavailable signal strength, or unavailable player volume—must preserve existing rendering and calculations for all previously valid Linux/BSD values.

## 2.7 Tray modules and system tray applet

Paths are under `docking/platform/tray/` unless stated otherwise.

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `backend.py`, `state.py`, `menu.py` | Extend Phase 1’s neutral contracts with the native geometry, pointer, double-click, and focus-request operations required by Windows. Preserve Unix activation/menu behavior. |
| `service.py` | Integrate provider generations, current-request checks, focus delivery, and backend pinning at the final command boundary. Continue using the required factory for fresh restart lifetimes. |
| `notifications.py`, `__init__.py` | Preserve canonical shared service exports and badge bridge behavior. Avoid duplicate SNI and generic poll loops. |
| PR `linux.py` → Phase 1 `unix.py` | Reconcile the SNI adapter with Phase 1’s canonical Unix implementation. Preserve geometry forwarding, menu conversion, and activation. Do not maintain two equivalent adapters. |
| `windows.py` | Preserve item generations, activation identity, hover lifetime, native callback parameters, focus ownership, and late command rejection. |
| `windows_host.py`, `windows_appbar.py`, `windows_icons.py`, `windows_messages.py` | Preserve hidden receiver/message-pump ownership, Explorer forwarding, AppBar bridging, bounded message parsing, and icon conversion. |
| `docking/applets/systemtray/{applet.py,state.py,presentation.py,unix_presentation.py}` | Port native pointer/keyboard/menu interaction and focus return through the shared service/surface APIs. Preserve XEmbed presentation on Unix. Applet stop must unsubscribe and release its own UI without stopping the shared service. |
| `docking/platform/status_notifier/{backend.py,service.py,notifications.py}` | Move SNI mechanics to `platform/tray/implementations/unix/` and polling/overlays to `platform/tray/`. Update callers and remove the old package rather than recreating compatibility exports or shared OS dispatch. |

Test stop/restart races involving old workers, old menus, removed items, new focus requests, pointer leave/enter delivery, and provider replacement. Static inventory tests alone are insufficient.

## 2.8 Browser and AI usage modules

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `applets/webapp/{backend.py,profile.py,applet.py,state.py}` | Reuse Phase 1 browser construction. Remove the PR’s `browser.py` import-time concrete-class alias and use the contract/profile modules directly. Keep backend-specific session and download storage helpers in their native implementations. Preserve profiles, state transitions, startup/recovery, session erasure, and external opening. |
| `applets/webapp/{windows.py,windows_notifications.py}` | Register the WebView2 browser and notifications. Preserve callbacks, downloads, native ownership, activation, and runtime availability. |
| `applets/webapp/windows_companion/{__init__.py,__main__.py,browser.py,runtime.py,process.py,protocol.py}` | Preserve isolated runtime/package loading and process protocol. Select the companion only through the existing explicit engine configuration in Windows composition. |
| `applets/webapp/windows_companion/{downloads.py,input.py,text_input.py,notifications.py,user_agent.py}` | Preserve download ownership, input/composition behavior, notifications, and engine-specific user agent. Do not introduce GTK into the companion runtime. |
| `applets/webapp/{bridge.py,notification_backend.py,linux.py,linux_notifications.py,notifications.py}` | Port validated activation messages and transport hooks. Preserve bridge size/schema validation, Unix WebKit handling, and notification ownership. |
| `applets/telegram/state.py`, `applets/whatsapp/state.py` | Preserve Phase 1 profile-based construction and each module's `PROFILE` value; remove obsolete `browser.py` and notification re-export modules when adapting the PR. Port native error/availability reporting without losing Unix diagnostics or separate sessions. |
| `applets/aiusage/{hook_runner.py,store.py}`, `platform/host/implementations/windows/` | Implement installed Windows hook discovery, console interpreter fallback, Windows mutex behavior, and explicit encoding/binary flags through the host API. Providers/store call that API directly; do not recreate `hook_command.py` or `locking.py` forwarding modules. Preserve POSIX locking and existing session updates. |
| `applets/aiusage/backends/{claude.py,codex.py}` | Preserve Windows quoting, owned-hook upgrades/removal, robust notify parsing, unrelated user settings, and Unix legacy-hook recognition. Remove direct OS branching from shared registration logic. |

## 2.9 Search and other shared UI modules

| Source/reference module | Required Phase 2 changes and preservation requirements |
|---|---|
| `search/services/{windows_shortcuts.py,windows_focus.py,window_focus.py}` | Register native shortcut/focus implementations through composition. Preserve layout changes, conflicts, cancellation, shell handoff, and foreground ownership. |
| `search/services/{platform.py,global_shortcuts.py,x11_shortcuts.py}` | Keep Phase 1’s explicit integration factories and lifecycle contracts. Remove the PR’s per-call OS dispatch. Preserve portal and fallback behavior. |
| `search/controller.py` | Integrate native status/conflict reporting, focus factory, workarea, icon updates, and asynchronous preview refresh. Preserve provider order, query generations, preference recreation, and stale-result rejection. |
| `search/icons.py`, `platform/application_icons.py` | Preserve current dock/applet/custom artwork precedence and async native icon updates. Unsubscribe when views close. |
| `search/{intents.py,preview.py}`, `search/providers/{base.py,path.py}` | Port native path recognition and newline normalization through the shared path interface. Preserve Unix query recognition, malformed-target rejection, bounded reads, and text decoding. |
| `search/providers/windows.py` | This is the **window-search provider**, not an OS implementation. Preserve its backend-neutral role; integrate registry application names without adding OS selection. |
| `search/ui/{window.py,shortcut_capture.py}` | Preserve native focus lifecycle, AltGr/dead-key rejection for shortcuts, composition/preedit, workarea fitting, asynchronous preview refresh, and icon refresh. GTK fallback behavior remains unchanged on Unix. |
| `ui/display.py` | Preserve explicit workarea providers and the distinction between external workarea and usable workarea including dock reservation. |
| `ui/dnd.py`, `platform/applications/drag.py`, `applets/applications/{applet.py,icons.py,render.py}` | Port stable internal application-ID drags, native external exports, prepared native drag ownership, and one-time drop completion. Preserve Unix URI drops, dock reorder, pin insertion, and live menu icons. |
| `ui/{dock_window.py,factory.py}` | Port icon bounds in anchors, shared icon services, native workarea wiring, and startup autohide initialization. Preserve dock geometry and initial pointer-outside behavior. |
| `applets/popup.py`, `ui/runtime.py` | Wire explicit surface hooks for menu focus, capture overlays, managed popups, and source-window capture. Avoid permissive attribute probing of internal contracts. |
| `ui/{menu.py,preview.py}` | Port asynchronous thumbnails, fallback icons, menu preparation, and update subscription cleanup. Retain Phase 1’s menu-removal policy. |
| `ui/{hover.py,tooltip.py}` | Preserve preview/tooltip exclusion, popup visibility, and hide/show transitions. |
| `ui/{input_controller.py,renderer.py}` | Port popup-anchor updates and input-region alpha through surface policy. Preserve visual transparency and hit testing on Unix, with no unconditional alpha fill. |
| `ui/folder/{_browser.py,stack.py}` | Use the common file adapter so Windows UNC authority survives. Preserve folder navigation, monitoring, file operations, and virtual URI handling. |
| `applets/{colorpicker,windowkiller}/applet.py` | Pass capture anchors to surface integration. Preserve existing selection, Escape/cancel, and target validation. |
| `applets/ambient/applet.py` | Preserve GStreamer filename-to-URI conversion and playback-start failure handling. Retain master’s white-noise and playback fixes when reconciling the branches. |
| `applets/deskpresence/applet.py` | Keep idle behavior driven by the selected service. The PR’s documentation change must not alter timing or accounting. |
| `applets/{battery,brightness,music,network}/render.py` | Port required optional/unknown native-state presentation. Verify identical output for existing Unix states and explicit presentation of unknown native values. |

## 2.10 Packaging, tests, translations, and release support

Although the implementation focus is `docking/`, these supporting changes are required to avoid regressions.

| Area | Required Phase 2 treatment |
|---|---|
| `pyproject.toml`, `setup.py`, `MANIFEST.in`, package-data configuration | Preserve platform-specific dependencies, editable/build behavior, native helper compilation, binary wheel tags/fixtures, and exclusion of generated Windows artifacts from Unix packages. Do not broaden Linux dependencies to mask Windows-tool import problems. |
| `packaging/windows/` and native launchers | Preserve portable and per-user installation, isolated interpreter invocation, stable launchers/hooks, versioned upgrade payloads, startup choices, ownership inventories, and uninstall preservation of preferences/unknown files. |
| `tools/windows/` and `.github/workflows/{windows.yml,windows-input.yml,ci.yml,bsd.yml}` | Preserve the Server-produced candidate artifact, Windows 11 consumption of that exact wheel, separate coverage profiles, and dependent combined release-evidence job. Reconcile module/resource paths without weakening identities, report completeness, child exit checks, or release gates. |
| `tools/windows/visual/release_smoke.py`, `tests/test_windows_release_smoke.py` | Preserve commit `2334e7b2`: imports/test collection must not require the Windows-only native process dependency. |
| `tools/windows/visual/bundle_smoke.py`, `tests/test_windows_bundle.py` | Preserve helper provenance from `8f53eb5d` and the newer verified-runtime rule: modules inside the verified bundle are valid even if its ancestor path contains `msys64`; the only external helper-cache exception is the exact content-addressed deployment/checksum. Preserve bounded audio progress logs, EOS checks, timeout diagnostics, and primary failure through cleanup. |
| Windows input/tray/application acceptance | Preserve strict workstation IME/preedit and dead-key requirements, AMD64/x86 helper/editor identities, complete matrix collection, and native interaction profiles. The Server API profile must explicitly make no composition claim; it cannot substitute for Windows 11 workstation evidence. |
| `docking/locale/`, translation tooling | Carry required PR strings and regenerate catalogs through the established workflow. Avoid unrelated translation churn. |
| Installation/development/packaging documentation and `AGENTS.md` | Reconcile commands and interfaces with the final implementation. Keep PR descriptions concise and follow repository instructions. |

### Candidate identity, input profiles, and release staging

| Supporting modules/workflows | Required current behavior |
|---|---|
| `tools/windows/input_acceptance.py`, `tests/test_windows_input_acceptance.py` | Keep stdlib-only candidate/evidence validation. Bind source commit, wheel SHA-256, installed bytes, exact module origins, both helper architectures and checksums, original child reports, child return codes, and cleanup evidence. Validate structure and each required matrix cell rather than trusting an `ok` summary. |
| `tools/windows/ci.py`, `.github/workflows/windows.yml` | Server produces the candidate manifest and wheel artifact. Input collection requires `--installed-root`, `--wheel`, and `--source-sha`; `--input-coverage server-api` retains API/profile activation, classic/dead-key/layout/AltGr, shortcuts, cancellation and cleanup coverage while recording composition as not requested. Continue collecting both child results before failing the domain. |
| `.github/workflows/windows-input.yml`, `tools/windows/visual/{keyboardlayout_smoke,input_profile_smoke,input_requests,input_editor,input_fixture}.py` | Called CI downloads, verifies, and installs the exact Server candidate; it must not independently rebuild a nominally equal version. Manual dispatch may build its own explicitly identified candidate. Workstation evidence retains all twelve request controls, eight genuine Telex/VNI compositions across both architectures/key routes, and plain GTK/Search preedit, Return and Escape checks. |
| `.github/workflows/ci.yml`, `tools/windows/release.py`, release tests | Preserve `windows-release-evidence`, which validates both downloaded artifacts before staging actual packages on PRs. Publication repeats validation. `stage(..., source_sha=...)` and CLI `--source-sha` are mandatory. Require Windows 11 product/AMD64 runtime metadata and matching candidate identities across Server/client artifacts. Missing, stale, failed, cancelled, or Server-only evidence cannot authorize release. |
| `tools/windows/visual/release_smoke.py`, `tests/test_windows_release_smoke.py` | Resolve mutable output/config/cache/data/state paths absolutely outside the bundle before launcher CWD changes. Verify every declared payload byte before and after measurement; retain matching complete inventory evidence and reject links/reparse points, missing/changed/unlisted files. Only the existing generated Python-bytecode exception remains. Preserve bounded relative path/shape/hash diagnostics and primary failures without recording file contents. |
| `tools/windows/installer.py`, installer/bundle fixtures | Preserve short temporary source-build paths for setuptools/MAX_PATH, output-volume staging and atomic publication, including TEMP on another drive. Preserve verified binary wheel fixtures; do not change product metadata to fix a test fixture. |
| `tools/windows/visual/{applications_domain_smoke,explorer,desktop_driver,window_focus,runtime_pin_smoke,file_smoke}.py` and their tests | Preserve physical activation of a freshly verified owned caption, full process/window/ABI identity checks, temporary position restoration, correct native argument handling, and owned fixture cleanup. These are acceptance-tool helpers, not production desktop services. |
| `tools/windows/visual/catalog_changes_smoke.py` and its tests | Keep bounded passive worker/publication/observer timelines for owned labels. Do not prime the Shell cache by running an independent oracle before a failing wait is final. Diagnostic failures must not replace the original timeout/error. |
| `tests/smoke/test_python314_smoke.py`, `.githooks/pre-commit`, `tools/install_precommit_hook.sh`, `docs/DEVELOPMENT.md`, folder-thumbnail/folder-browser fixtures | Retain explicit catalog/association/readiness and `TargetService` fixtures. Run the no-GI smoke slice independently from full-suite collection so cached real modules cannot hide an incomplete test double. |
| `.github/workflows/compositor.yml` | Retain the newer nonfatal cache-export setting; this affects cache publication only and must not suppress build or compositor acceptance failures. |

Release evidence must identify the actual source commit recorded by the candidate-producing workflow, which can be a merge commit. Do not replace it with a PR display/head SHA, reuse reports from a previous wheel, or relabel reports after code/package moves. Rebuild the candidate and regenerate both acceptance artifacts for the integrated implementation.

Retain the exact profile identifiers `server-api` and `workstation-composition`; the latter is the strict/default workstation contract. The explicit Server profile reflects the observed hosted image and does not establish a universal Windows Server IME limitation. It must never relax the workstation requirements or be accepted as workstation proof.

### Hosted desktop containment

Keep hosted-only preparation in `tools/windows/visual/hosted_desktop.py`, not the product bootstrap or native runtime provider. Preserve the native Windows 11 ARM/disposable GitHub-hosted guards, the collector's owned job and explicit permission handshake, main-thread desktop guarding, and exact privacy-policy restoration.

The new `--minimize-runner-terminals` policy is opt-in and limited to verified Terminal package family, registered image path, process creation/session identity, and native window class. Revalidate before every action. Terminal windows may be minimized, never terminated by this policy; the disposable hosted policy intentionally leaves them minimized and must be recorded as such. It is not general authorization to manipulate local/self-hosted desktops or unrelated windows.

An unreadable candidate can be left untouched only with the recorded bounded proof that it has no visible top-level blocker and is not the foreground process. Unknown visible/foreground ownership, identity changes, or failed proof remain failures. Preserve collector containment, quiescence before policy restoration, restoration-failure reporting, bounded passive diagnostics, and both child results.

## 2.11 Phase 2 verification and exit criteria

### Linux/BSD regression gate

Run the entire Phase 1 acceptance suite after Windows integration, including:

- X11 and native Wayland/compositor workflows.
- Mixed Cinnamon/XWayland behavior.
- BSD native packages/desktops.
- Flatpak and generated launcher/document handling.
- Applet restart, shutdown, preferences, rendering, and fallback precedence.
- Browser profiles and Unix notifications.
- Package/import isolation.

Windows integration is not accepted merely because Windows checks pass.

### Windows domain gate

Retain and adapt all existing PR tests, particularly:

- `tests/platform/applications/test_windows.py`: catalog merging, readiness, empty native catalog, association-only clients, retained handles.
- `tests/platform/applications/test_associations.py`, `test_visibility.py`: ordered association statuses, native hidden-ID handling, canonical Unix defaults, ready-empty versus unavailable, and association-only publication.
- `tests/platform/test_model.py`: pending/restored pins, metadata refresh, identity and order.
- Windows registration, windows, paths, startup, previews, tray, and input tests.
- Applet `test_windows_*` suites and request/lifecycle tests.
- Search native focus/shortcut/input tests.
- Browser/WebView2/companion tests.
- AI usage installed-hook and locking tests.
- Bundle, installer, upgrade, uninstall, release, and helper-provenance tests.

The latest-commit cases below are mandatory additions to the migration gate; do not lose them while updating imports and test doubles:

| Test modules | Scenarios to preserve |
|---|---|
| `tests/test_app.py`, `tests/applets/test_music.py`, `tests/platform/test_targets.py` | Deferred fresh-install pins with/without catalog completion, user edits before resolution, ready/unavailable defaults, one pending media action, stop/restart subscription ownership, and nonblocking folder-handler names. |
| `tests/test_windows_catalog_worker.py`, `tests/test_windows_catalog_associations_native.py` | Real GLib heartbeat during blocked Gio/association reads, superseded worker generations, explicit-source authority, retained handles on failure, successful empty completion, accepted association publication, initialization order, and both GI source-callback conventions. |
| `tests/platform/test_windows_catalog_monitor.py`, `tests/test_windows_catalog_changes_smoke.py` | Explorer replacement with/without broadcast, coalesced native bursts, finite retry of stale/failed completed reads, all in-flight tickets, slow resolver non-starvation, captured old monitor completion, reentrant close, and passive failed-wait diagnostics. |
| `tests/platform/{test_windows_documents.py,test_windows_selection.py,test_windows_dataobject.py}`, `tests/test_windows_associations.py` | Exact directory identity, complete ordered Unicode/UNC and protocol selection, cross-directory PIDLs, byte/enumeration bounds, malformed/ambiguous/missing identities, positive refusal statuses, cleanup of populated failed outputs, no partial/default fallback, and only the recognized missing-contract fallback. |
| `tests/test_windows_application_associations.py`, `tests/test_windows_applications_domain.py`, `tests/test_windows_explorer.py`, `tests/test_windows_window_focus.py` | Owned native registration cleanup, fresh-child/installed-wheel isolation, complete arguments, exact process/ABI/window ownership, real physical focus and caption readiness, placement restoration, and preservation of the primary failure. |
| `tests/test_windows_input_acceptance.py`, `tests/test_windows_ci_runner.py`, `tests/test_windows_release.py`, input editor/request/client/profile tests | Exact wheel/source/installed bytes and origins, both helper identities, Server/workstation profile separation, every required matrix cell, both original reports and child exits, full cleanup/desktop guard evidence, and rejection of stale/tampered/partial evidence. |
| `tests/test_windows_hosted_desktop.py` | Hosted-only opt-in Terminal policy, package/path/class/process revalidation, minimization-only behavior, unreadable background-candidate proof, visible/foreground failures, collector containment, and exact policy cleanup. |
| `tests/test_windows_release_smoke.py`, `tests/test_windows_bundle.py`, `tests/test_windows_installer.py`, isolated smoke and folder fixture tests | Absolute external mutable paths, before/after payload equality, bounded diagnostics, original bytecode exception only, authenticated helper/runtime paths, audio timeout/failure visibility, short-path cross-volume fixture publication, and explicit new service interfaces in isolated fixtures. |

Exercise:

1. Startup with no visible windows and with restored pins.
2. Uncataloged multiwindow grouping, cycling, pinning, and restart.
3. Fullscreen entry/exit and startup recovery.
4. Mixed DPI and negative monitor coordinates.
5. Native menus, tray hover/activation/focus return, and stale callback races.
6. Search typing, dead keys, IME/preedit, layout changes, conflicts, and recovery on the exact installed candidate. Preserve explicit Server API coverage and mandatory workstation composition evidence separately.
7. Drive/UNC paths, Unicode filenames, document launching, and native drag exports.
8. Device absence/removal/recovery and unavailable capabilities.
9. Stop/restart during native work and cleanup after GTK exits.
10. Installed ZIP/installer startup, upgrades, startup choices, uninstall, and artifact verification.

Use the current Windows CI stages and installed-artifact acceptance tools documented in the repository. Preserve their strict requirements rather than weakening assertions during API migration.

### Final acceptance

Phase 2 is complete only when:

- Windows implementations use the accepted OS/session interfaces.
- Shared consumers contain no OS dispatch or concrete Windows selection.
- Native runtime checks remain local to native boundaries.
- No duplicated service, selector, catalog, browser host, or tray poll loop remains.
- Both Linux/BSD and Windows acceptance gates pass.
- Combined release staging validates complete Server and Windows 11 workstation artifacts for the exact rebuilt candidate/source, including both original input reports, child exits, and unchanged before/after payload evidence.
- All fixes present in the recorded Phase 1 and Windows PR baselines have been accounted for.
- Configuration, identities, pins, package entry points, installed upgrades, and cleanup behavior remain compatible.

Signing and Windows pressure reveal remain outside these phases unless their scope is explicitly changed.

## 2.12 Phase 2 source-to-destination manifest

Interpret the source paths in this manifest relative to the recorded PR head, and the destinations relative to the completed Phase 1 tree. Do not recreate the PR's old flat OS modules alongside the accepted implementations.

### Application, desktop, and tray implementation moves

| PR module or responsibility | Canonical Phase 2 destination |
|---|---|
| Planned `platform/host/windows.py` | `platform/host/implementations/windows/{backend,paths,storage,locking,process}.py`, matching the Unix host contract. The headless host may use suitable native ABI leaves but may not import GUI services. |
| Windows branches in `platform/files.py`, `targets.py`, `keyboard.py`, and GUI signal installation | `platform/desktop/implementations/windows/{files,targets,keyboard,lifecycle}.py`, supplied through the domain contracts/facades. |
| `platform/applications/windows/{catalog,discovery,associations,launch,process,registration,export}.py` | Same basenames under `platform/applications/implementations/windows/`. Adapt the now-existing association reader to the common contract and its catalog-owned metadata worker; do not implement a second native query/cache path. |
| `platform/backends/windows/catalog.py` | `platform/applications/implementations/windows/native_catalog.py`. Keep this distinct from the higher-level `catalog.py` adapter; it imports application models/export policy and is not a reusable ABI primitive. |
| `platform/backends/windows/{catalog_monitor,catalog_shortcuts,documents,drag_source}.py` | Same basenames under `platform/applications/implementations/windows/`. Adapt callers to the application contracts and shared native primitives. |
| `platform/backends/windows/{associations,selection}.py` | `platform/native/windows/{associations,selection}.py`: exact Shell association-query and complete PIDL/data-object primitives. Registry/default-handler policy and selected-application activation orchestration remain in the application adapter. |
| `platform/backends/windows/icons.py` | `platform/desktop/implementations/windows/icons.py`, implementing the shared native artwork source. |
| `platform/backends/windows/{menus,geometry}.py` | Same basenames under `platform/desktop/implementations/windows/`. These adapters may use the GTK/main-loop boundary; do not put them in the headless host or ABI-only layer. |
| `platform/tray/windows.py` | `platform/tray/implementations/windows/backend.py`. |
| `platform/tray/windows_{host,appbar,icons,messages}.py` | `platform/tray/implementations/windows/{host,appbar,icons,messages}.py`, respectively. |
| PR `platform/tray/linux.py` | Reconcile into the accepted `platform/tray/implementations/unix/backend.py`; do not retain another Unix adapter. |
| PR `search/services/windows_{shortcuts,focus}.py` | `search/services/implementations/windows/{shortcuts,focus}.py`, respectively. Shared lifecycle/status contracts stay in `search/services/`. |
| PR `search/services/x11_shortcuts.py` | Reconcile into Phase 1's `search/services/implementations/unix/x11_shortcuts.py`. |

Keep the Phase 1 common registry/launch/association/registration/monitoring contracts. Extract any still-shared native handle conversions into `platform/applications/handle_adapters.py`; a Windows adapter must not import Unix discovery or desktop-entry parsing to obtain a generic helper.

The Windows surface receives the native application drag adapter/callback through session composition; it must not import `applications/implementations/windows/drag_source.py` directly. Focus and coordinate operations use the selected desktop adapters. Keeping these dependencies explicit avoids replacing the old mixed Windows directory with new cross-domain concrete imports.

### Session services versus reusable Windows primitives

All source basenames in this table currently live under PR `platform/backends/windows/`. The table exhaustively assigns that package's non-initializer source files; the application/desktop rows intentionally repeat their destinations for auditability.

| Source basenames | Canonical destination package |
|---|---|
| `session.py`, `windows.py`, `surface.py`, `appbar.py`, `visibility.py`, `previews.py`, `screen_capture.py`, `picking.py`, `desktop.py`, `workspaces.py`, `virtual_desktops.py`, `idle.py`, `ghosts.py` | Stay in `platform/backends/windows/`: session composition, tracked windows, surfaces, and session-owned services/helpers. |
| `ffi.py`, `com.py`, `com_callback.py`, `winrt.py`, `winrt_collections.py` | `platform/native/windows/`: ABI, COM, WinRT and callback ownership. |
| `input_context.py`, `input_profiles.py`, `input_service.py`, `input_thread.py`, `input_hook.cpp`, `modifiers.py`, `hotkeys.py` | `platform/native/windows/`: native input/profile/hotkey primitives reused by the keyboard applet and Search integration. |
| `capture.py`, `close_hook.py`, `process_job.py`, `properties.py`, `appresolver.py`, `packages.py` | `platform/native/windows/`: reusable native graphics/process/shell primitives. |
| `dataobject.py`, `drag.py`, `selection.py`, `associations.py`, `launching.py`, `shell.py`, `shortcuts.py`, `shell_changes.py` | `platform/native/windows/`: low-level native shell/data-transfer/association/observer operations. Preserve complete selection formats and exact association identities; keep registry selection, catalog export policy, and selected-application orchestration in their owning application adapter. |
| `toasts.py`, `webview.py`, `power.py`, `installation.py`, `startup.py` | `platform/native/windows/`: reusable native notification/browser/power/install/bootstrap mechanisms. `startup.py` remains callable before GI; `webview.py` must not import the GTK browser host. |
| `catalog.py` | `platform/applications/implementations/windows/native_catalog.py`. |
| `catalog_monitor.py`, `catalog_shortcuts.py`, `documents.py`, `drag_source.py` | `platform/applications/implementations/windows/`, retaining basenames. |
| `icons.py`, `menus.py`, `geometry.py` | `platform/desktop/implementations/windows/`, retaining basenames. |

Add lightweight initializers for `platform/native/` and `platform/native/windows/`; retain the session package initializer without re-exporting the whole native runtime. Native operations may use binding-free result DTOs such as launch outcomes, but may not acquire a registry/model/provider or import a domain's concrete implementation.

### Windows applet implementation moves

For each applet below, source filenames are relative to `applets/<name>/` in the PR, and destinations are relative to the same applet directory in the accepted tree.

| Applet | Source → canonical destination |
|---|---|
| `battery` | `windows.py` → `implementations/windows/backend.py`; `windows_peripherals.py` → `implementations/windows/peripherals.py`. |
| `bluetooth` | `windows.py` → `implementations/windows/backend.py`; `windows_{radio,le,advertisement,gatt,pairing,pairing_ui}.py` → `implementations/windows/{radio,le,advertisement,gatt,pairing,pairing_ui}.py`. |
| `brightness` | `windows.py` → `implementations/windows/backend.py`. |
| `caffeine` | `windows.py` → `implementations/windows/backend.py`; retain shared inhibition coordination at the root. |
| `camshield` | `windows.py` → `implementations/windows/backend.py`; `windows_{client,control,elevation,helper,journal,policy,sensor,sensor_host}.py` → corresponding basenames under `implementations/windows/`. Update every helper invocation/authorization check for the canonical module; preserve established public helper commands. |
| `capslock` | `windows.py` → `implementations/windows/backend.py`. |
| `keyboardlayout` | `windows.py` → `implementations/windows/backend.py`; shared native input machinery comes from `platform/native/windows/`, not a Search or session implementation. |
| `micshield` | `windows.py` → `implementations/windows/backend.py`; `windows_audio.py` → `implementations/windows/audio.py`. |
| `music` | `windows.py` → `implementations/windows/backend.py`; `windows_{audio,media}.py` → `implementations/windows/{audio,media}.py`. |
| `network` | `windows.py` → `implementations/windows/backend.py`; `windows_{connections,profiles,ras,vpn,wifi}.py` → corresponding basenames under `implementations/windows/`. |
| `notifications`, `powerprofiles`, `volume` | Each `windows.py` → that applet's `implementations/windows/backend.py`. |
| `screenshot` | `windows.py` → `implementations/windows/backend.py`; `windows_selection.py` → `implementations/windows/selection.py`. Root `selection.py`/`preview.py` remain shared GTK workflow modules. |
| `session` | `windows.py` → `implementations/windows/backend.py`. |
| `trash` | `windows.py` → `implementations/windows/backend.py`; `windows_monitor.py` → `implementations/windows/monitor.py`. |
| `webapp` | `windows.py` → `implementations/windows/backend.py`; `windows_notifications.py` → `implementations/windows/notifications.py`; the entire `windows_companion/` package → `implementations/windows/companion/`, preserving its internal basenames. |
| `systemtray` | Windows transport belongs in `platform/tray/implementations/windows/`; no second applet-local Windows tray receiver. Existing Unix presentation follows the Phase 1 manifest. |

For the PR's Unix files, reconcile into the Phase 1 destinations: `unix_desktop.py` → `implementations/unix/desktop.py`; `unix_{client,control,devices}.py` → that applet's `implementations/unix/{client,control,devices}.py`; desktop settings, UPower, PulseAudio, and browser notifications follow the explicit Phase 1 applet rows. Never restore flat `linux.py` files for an implementation already classified as Unix.

Shared applet values, renderers, browser profile/storage/bridge logic, Search UI, and GTK popup workflows retain their domain-root paths. `aiusage/backends/{claude,codex,...}.py` remain provider adapters; their directory does not become an OS package. `search/providers/windows.py` remains the neutral window-search provider and must not be moved into the Windows implementation tree.

### Resource, entry-point, and regression checks for the moves

1. Move the Windows input helper source to `platform/native/windows/input_hook.cpp`; generated `input-thread-32.dll` and `input-thread-64.dll` belong to that owning package in the built payload. Update `setup.py`, build inputs/output mappings, package-data rules, bundle inventory, native loader paths, and source/installed probes together. Do not commit generated DLLs.
2. Retain the public launcher/hook executable names, persisted hook commands where stable launchers are used, native ABI/protocol, both helper architectures, and checksum/content-addressed deployment validation. Update the exact owning package lookup, not the strength of the provenance check or a broad exception list. Update `tools/windows/input_acceptance.py` and its producers to require the new exact installed module origins and helper paths; do not loosen those checks to accept arbitrary paths after a move.
3. Update every browser companion invocation, relative import and isolated package bootstrap after moving to `webapp/implementations/windows/companion/`. Its interpreter must still load the intended Docking package without adding the GTK interpreter's site-packages. Test source and installed startup, downloads, input, notifications, and session cleanup.
4. Update camera/elevation/helper invocation strings and installed module allowlists consistently. Do not weaken identity/path validation to accommodate renamed modules.
5. Update native and adapter test imports/patch targets, packaging probes, acceptance harnesses and architecture rules. Add a coverage check against the actual PR native-module inventory so no file remains accidentally owned by both the old session package and a domain/native destination.
6. Reject new application/applet/Search imports of concrete Windows session services. Those implementations may import their own domain's Windows helpers and shared `platform/native/windows/` primitives; the session's surface may use the selected desktop adapter for GTK focus/geometry/drag operations.
7. Re-run the complete Phase 2 Linux/BSD and installed Windows gates. Passing source imports is insufficient after a package/resource move.
8. Preserve `tools/windows/input_acceptance.py` and `tools/windows/visual/window_focus.py` in tooling; they are not new production modules. Update tooling imports of the newly moved native package, source-isolation fixtures, reported origins, and release validators together. Generate new candidate/source evidence after the moves rather than reusing old reports.
