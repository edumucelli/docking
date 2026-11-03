# Dependency analysis

[Back to Docking](../README.md)

Docking benefits from selectively replacing dependencies that implement a small,
stable requirement. The best first candidate is the weather integration: three
direct packages brought in 21 Python distributions for two small API requests.
Replacing graphics toolkits, browser engines, or test frameworks would transfer
much broader compatibility and maintenance work into Docking.

Agents reduce the cost of writing a replacement. They do not remove the need for
an independent correctness reference, platform testing, or future maintenance.
The objective is less third-party code to trust, with installation size and
packaging simplicity as additional benefits.

## Measurement baseline

The baseline is commit `5834db0e`, inspected on 2026-11-03 using the available
Linux Python 3.11 environment. The tables retain those measurements after the
replacements so that before and after comparisons remain meaningful.

- Python payload sizes count installed distribution files, excluding bytecode
  caches and distribution metadata. They include compiled code and data files.
- Python line counts are physical `.py` lines, including comments and any
  shipped tests or vendored code. They do not measure compiled implementation
  complexity.
- Usage counts come from tracked source imports, literal dynamic imports, and
  call-site inspection. One public call can depend on substantial internal code;
  a function-count percentage would not measure the fraction actually needed.
- Dependency chains follow installed metadata with environment markers evaluated
  for the inspected interpreter and without optional extras unless requested.
  Removal savings subtract dependencies still needed by the other roots.
- Native sizes use the local Debian package manager's installed-size metadata
  and exclude transitive packages. Website sizes count installed npm files,
  excluding nested dependencies from each package's own size.

These are installed sizes, not download sizes, RAM usage, or performance gains.
Shared system packages may remain installed after Docking stops requiring them.
Release formats resolve different versions, so their savings can differ.

The local, ignored `uv.lock` describes Docking 0.1.0 and obsolete flake8/mypy
development requirements. It is not tracked or an inventory of the current
application. The documented `uv pip install` workflow reads `pyproject.toml`.
Some installed tool versions also differ from current requirements, notably
Ruff 0.16.0 versus the required 0.17.1.

## Weather dependencies

At the baseline, all three packages were used exclusively in
[`weather/api.py`](../docking/applets/weather/api.py). Docking requests current
temperature and weather code, five days of three forecast values, and four
air-quality readings: approximately 21 numeric values plus dates per refresh.

| Direct package | Version | Own payload | Python lines | Docking usage | Decision |
|---|---|---|---|---|---|
| openmeteo-requests | 1.7.5 | 4.9 KiB | 177 | Client and synchronous weather_api calls to two endpoints | Replace |
| requests-cache | 1.3.0 | 187 KiB | 4,903 | One CachedSession with SQLite persistence and 300-second expiry | Replace with a weather cache |
| retry-requests | 2.0.0 | 2.8 KiB | 86 | One retry configuration call | Replace with the weather transport |

The Open-Meteo client supports asynchronous access and binary time-series
processing. Its FlatBuffers design targets larger analytical workloads.
Docking's small requests can use the service's JSON responses instead.
[Open-Meteo API documentation](https://open-meteo.com/en/docs) and
[Python client documentation](https://github.com/open-meteo/python-requests/blob/main/README.md).

Docking already has standard-library HTTP helpers in
[`applets/http.py`](../docking/applets/http.py), used by 11 other modules at the
baseline. Reusing those helpers avoids writing a general HTTP library.

### Transitive weather packages

Docking imports none of the following packages directly. Some perform required
indirect work; others are installation requirements of functionality Docking does
not select. In particular, Docking supplied a Requests session while the newer
Open-Meteo client still required and imported Niquests.

| Package | Own payload | Python lines | Reason present |
|---|---|---|---|
| openmeteo-sdk | 69.1 KiB | 875 | Generated weather response decoding |
| flatbuffers | 84.2 KiB | 3,052 | Binary serialization |
| niquests | 764 KiB | 20,637 | Newer Open-Meteo HTTP client requirement |
| urllib3-future | 2.44 MiB | 72,366 | Niquests transport stack |
| h11 | 90.9 KiB | 2,568 | HTTP/1.1 protocol support |
| jh2 | 1.17 MiB | 13,269 | HTTP/2 support |
| qh3 | 6.43 MiB | 10,764 | QUIC and HTTP/3, including compiled code |
| wassima | 237 KiB | 4,478 | Certificate trust support |
| requests | 183 KiB | 5,623 | Transport for the supplied cached session |
| urllib3 | 413 KiB | 11,870 | Requests transport and retries |
| charset-normalizer | 428 KiB | 5,019 | Response encoding detection |
| idna | 335 KiB | 13,827 | Internationalized domain handling |
| certifi | 270 KiB | 99 | CA certificate bundle |
| attrs | 214 KiB | 6,259 | Cache model support |
| cattrs | 236 KiB | 6,815 | Cache serialization |
| platformdirs | 67.7 KiB | 1,943 | Cache directory support |
| url-normalize | 18.2 KiB | 664 | Request normalization for caching |
| typing-extensions | 161 KiB | 4,422 | Compatibility support |

The complete baseline chain contains 21 distributions, 14,388,109 payload bytes
(13.72 MiB), and 189,716 Python lines. Independent replacement opportunities are:

| Change | Packages removed from weather chain | Gross payload removed |
|---|---|---|
| Replace retry-requests alone | 1 | 2.8 KiB |
| Replace requests-cache alone | 6 | 0.86 MiB |
| Replace openmeteo-requests with JSON, retaining Requests/cache/retries | 9 | 11.27 MiB |
| Replace the complete weather stack with standard-library HTTP | 21 | 13.72 MiB |

These rows are alternatives, not additive savings. Development tools can retain
shared packages such as typing-extensions. Flatpak pinned openmeteo-requests
1.2.0 at the baseline and therefore had a different, smaller transport chain.

### Internal weather implementation

Weather now uses the existing standard-library JSON HTTP helper, with private
response parsing, retry, and cache functions. Public fetch signatures and return
types remain compatible with the applet. Requests explicitly select GMT and Unix
timestamps to preserve UTC forecast dates.

The cache persists validated responses for 300 seconds in hashed request files
under the weather cache directory's `json-v1` subdirectory. Temporary files are
unique, private, and replaced atomically. Corrupt, future-dated, expired, or
invalid cached responses trigger a fresh request. Storage failures do not discard
fresh network results. Successful writes prune expired JSON entries while
preserving fresh entries and the newly written response; publication and pruning
are serialized among worker threads. Old SQLite caches are left untouched and
are not migrated. Non-finite optional UV readings are cached as JSON null without
changing the decoded response or relaxing strict JSON serialization.

The retry policy retains five retries after the initial attempt, a five-second
timeout for each attempt, and backoff delays of 0, 0.4, 0.8, 1.6, and 3.2 seconds.
HTTP 500, 502, and 504 are retryable; 413, 429, and 503 are retryable when they
provide Retry-After. A positive valid Retry-After of at most 60 seconds overrides
backoff; a longer delay fails the request without sleeping. Malformed JSON and
required weather fields are not retried. WMO codes must be integral; unknown
integral codes retain the fallback description. Missing or invalid UV remains
optional. Expected network and response failures return None to the existing
applet error handling. Logs identify exception classes and HTTP status while
keeping request coordinates out of diagnostics.

The replacement removes the weather requirements and their explicit packaging
installations. Linux has no unconditional PyPI runtime requirements; BSD retains
psutil and the Wayland extra retains PyWayland. The native desktop stack remains.
The 13.72 MiB figure is the eliminated baseline dependency payload before
accounting for the internal implementation, not a measured release download
reduction or a claim that packages were uninstalled from development machines.

### Verified replacement result

| Metric | Baseline | Replacement |
|---|---|---|
| Direct weather requirements | 3 | 0 |
| Weather dependency chain | 21 distributions | 0 distributions |
| External weather payload | 14,388,109 bytes | 0 bytes |
| Weather API module | 9,447 bytes; 278 Python lines | 14,987 bytes; 451 Python lines |

The API module grows by 5,540 bytes and 173 lines. Subtracting that growth from
the baseline external payload gives 14,382,569 fewer runtime payload bytes,
approximately 13.72 MiB, before bytecode and other packaging differences. The
existing shared HTTP helper requires no changes.

Verification includes the default test suite, the seven isolated
no-GI smoke tests, 112 passing acceptance scenarios, and lint/format checks with
the required Ruff 0.17.1, type checks, and dead-code checks. A controlled local
HTTP server exercises JSON transport, HTTP failure recovery, and cache reuse.
A subprocess with the old dependency imports blocked covers both persistent
cache hits and cold-cache requests for weather and air quality;
live HTTPS requests to both Open-Meteo endpoints also succeed.

The built wheel runs weather with site-packages disabled. Its metadata retains
BSD psutil and the Wayland extra, with no active base requirements on Linux.
The source distribution and an amd64 Debian package build successfully; the
extracted Debian payload imports weather, the application, and native PyWayland,
and contains no weather vendor directory. Other native package formats,
architectures, and BSD installation checks remain for their CI environments.

## Other Python runtime packages

| Package | Measured size | Docking usage | Decision |
|---|---|---|---|
| psutil | About 0.94 MiB for the Linux distribution package | Six BSD modules; CPU, memory, disks, network interfaces, battery, temperature, executable paths and command lines | Keep |
| pywayland | 1.47 MiB; 36,237 Python lines | 22 production files, including 19 generated bindings | Keep |
| cffi | 696 KiB; 8,498 Python lines | Native interoperability for PyWayland | Keep with PyWayland |
| pycparser | 188 KiB; 5,624 Python lines | CFFI dependency; no direct calls | Consider only a packaging optimization |
| PyGObject | About 634 KiB of module payload | Imports in 253 production files | Keep |
| pycairo | About 240 KiB of module payload, mainly compiled | Imports in 84 production files | Keep |

Psutil exposes roughly twelve functions/classes/methods used here, but these span
several kernel interfaces. A replacement needs separate FreeBSD and OpenBSD
behavior, including process races, permissions, network enumeration, and sensors.
Replacing disk usage alone with standard-library functions would not eliminate
the package. The measured local version is Linux 5.9.4, whereas Docking requires
6.0 or later on BSD; the actual BSD payload was not measured.
[Psutil documentation](https://psutil.io/).

PyWayland, CFFI, and pycparser total approximately 2.33 MiB. Much of PyWayland is
generated protocol source; Docking also has 2,984 lines of its own generated
bindings at the baseline. Trimming unused server, scanner, or protocol material
could reduce packaging size. A private binding still needs native callbacks,
proxy lifetimes, protocol versions, event queues, and file descriptors.
[Wayland client API](https://wayland.freedesktop.org/docs/html/apb.html).

The GI imports include GTK in 111 production files, GLib in 129, GdkPixbuf in 143,
and Gio in 44. The Python bindings are a small part of the native implementation
on which the application architecture depends.

## Native dependencies

These sizes cover the named local Debian packages only. Shared desktop
components and overlapping transitive chains prevent adding them together to
predict uninstall savings.

| Component | Installed size | Docking usage | Decision |
|---|---|---|---|
| GTK 3 and GDK | 10.1 MiB | Windows, widgets, input, display integration and drag-and-drop | Keep |
| GLib, GObject and Gio | 4.0 MiB | Main loop, timers, signals, D-Bus, files and applications | Keep |
| Cairo | 1.28 MiB | Rendering and compositing | Keep |
| Pango and PangoCairo | 0.70 MiB | Text layout and rendering | Keep |
| GdkPixbuf | 0.47 MiB | Image loading and pixel buffers | Keep |
| GI repository library | 0.25 MiB | Native introspection | Keep |
| gtk-layer-shell | 0.28 MiB | Wayland dock surface placement | Keep |
| libwnck | 0.32 MiB | X11 window tracking and actions | Keep |
| libnm | 1.47 MiB | NetworkManager integration | Keep |
| Wayland client and server libraries | 0.20 MiB combined | Native Wayland support | Keep |
| X11 and XScreenSaver libraries | 1.57 MiB combined | X11 integration and idle information | Keep |
| GStreamer core | 4.31 MiB, excluding plugins | Ambient audio and generated noise | Keep for the feature |
| WebKitGTK 4.1 | 90.4 MiB, excluding dependencies | Embedded persistent web applications | Keep; consider optional packaging |

WebKit is the largest individual native candidate for reducing the baseline
installation. Its integration uses persistent sessions, JavaScript bridges,
notifications, navigation, and downloads. Replacing the engine would be a browser
project. Optional packaging is more practical because the backend already loads
WebKit lazily and handles its absence. Users selecting embedded web applications
would still need the engine. An external audio player likewise exchanges
GStreamer for another dependency.

## Development dependencies

These packages normally do not ship as application runtime requirements.

| Direct package | Version measured | Own payload | Docking usage | Decision |
|---|---|---|---|---|
| scikit-image | 0.26.0 | 28.2 MiB | Exactly two functions, SSIM and PSNR | Replaced with the selected upstream paths |
| Pillow | 12.1.1 | 18.4 MiB | Seven test/tool files; loading, conversion, cropping, drawing and differences | Possible later reduction |
| pytest | 9.0.2 | 1.26 MiB | 226 importing files; fixtures, parametrization and test execution | Keep |
| pytest-cov | 7.0.0 | 34.3 KiB | Coverage integration and CI thresholds | Keep |
| behave | 1.3.3 | 781 KiB | 56 scenarios and 13 importing step files | Keep unless intentionally migrating tests |
| ruff | 0.16.0 | 26.6 MiB | Broad linting and formatting | Keep |
| ty | 0.0.84 | 28.1 MiB | Project-wide type checking | Keep |
| vulture | 2.16 | 47.5 KiB | Dead-code checks and a custom analysis tool | Keep |
| PyGObject-stubs | 2.17.0 | 11.2 MiB | GTK and GDK typing information | Keep |

### Visual metrics

At the baseline, [`tests/visual/support.py`](../tests/visual/support.py) called only
`peak_signal_noise_ratio` and `structural_similarity` from scikit-image. The
dependency chain included:

| Package | Own payload | Relevance |
|---|---|---|
| scipy | 108.8 MiB | Filtering used by SSIM |
| networkx | 6.65 MiB | No direct Docking use |
| imageio | 1.10 MiB | No direct Docking use |
| tifffile | 1.01 MiB | No direct Docking use |
| lazy-loader | 10.5 KiB | scikit-image loading support |
| numpy | 54.5 MiB | Direct use in two Docking test/tool modules |
| Pillow | 18.4 MiB | Independently used by Docking |
| packaging | 254 KiB | Also required by other development tools |

The internal SSIM/PSNR implementation retains NumPy and Pillow and removes
scikit-image, SciPy, NetworkX, ImageIO, tifffile, and lazy-loader: approximately
145.8 MiB of baseline development payload. NumPy is now an explicit development
dependency. This does not reduce the installed dock's runtime dependencies.

[`tests/visual/metrics.py`](../tests/visual/metrics.py) adapts scikit-image 0.26.0's
SSIM and PSNR code for the float32 RGB/RGBA configuration Docking uses. The SSIM
source is also unchanged in scikit-image 0.25.2. The port retains per-channel
processing, a seven-pixel window, sample covariance, border cropping, and
floating-point operation order. Its NumPy implementation of SciPy 1.17.1's
rolling-sum filter preserves float64 accumulators and float32 output after each
axis. Unused Gaussian, gradient, dimensionality, and data-range options are not
copied. The port is 93 lines and 3,769 bytes; the two retained BSD notices add
2,882 bytes. Code, licenses, frozen upstream values, and their tests total
13,439 bytes before packaging differences.

Comparison against scikit-image 0.26.0/SciPy 1.17.1 produced exactly equal SSIM
and PSNR values for 189 pairs: 117 generated cases and perturbations of all 72
tracked offscreen/compositor baselines. These include flat regions, minimum
windows, RGB/RGBA channels, and 3,840-pixel-wide images. No acceptance decisions
changed. Frozen reference results cover both sides of each existing SSIM/PSNR
threshold, and an isolated import-blocking test exercises comparisons without
any of the six removed distributions. All 36 metric tests pass on NumPy 1.24.4
and 2.4.4. Baseline images and thresholds remain
unchanged. The source links and update procedure are documented in
[visual compositor testing](VISUAL_COMPOSITOR_TESTING.md#layer-2--pixels).

Local SSIM timing medians over five warmed calls on Python 3.11/NumPy 2.4.4 were:

| RGBA crop (width × height) | scikit-image/SciPy | Internal port |
|---|---|---|
| 512 × 64 | 14.1 ms | 22.4 ms |
| 1,920 × 120 | 96.5 ms | 128.5 ms |
| 3,840 × 120 | 233.4 ms | 262.4 ms |

The port exchanges a modest comparison-time increase for removing the compiled
SciPy stack. These are local measurements, not performance guarantees.

Pillow's PNG-oriented operations could later use existing Cairo/GdkPixbuf
facilities, with attention to alpha handling and the independence of visual
tests. Removing NumPy as well requires more pixel-processing code and separate
performance evidence.

### Test and analysis frameworks

The repository contains approximately 6,088 test function definitions at the
baseline. Pytest, Ruff, ty, and Behave expose broad framework behavior even when
their visible interfaces are small. Maintaining private equivalents would add
substantial ongoing work.

- Pytest brings iniconfig, packaging, pluggy, and pygments. Its measured chain is
  approximately 5.82 MiB.
- Pytest-cov adds coverage. Removing both saves about 0.79 MiB but loses current
  coverage enforcement.
- Behave brings cucumber-expressions, cucumber-tag-expressions, parse, parse-type,
  six, and colorama. Its measured chain is approximately 1 MiB.

## Build and security tools

| Package | Measured own payload | Usage | Decision |
|---|---|---|---|
| setuptools | 3.78 MiB | Build backend and package metadata | Keep |
| wheel | 93.7 KiB | Wheel tooling | Keep |
| build | 45 KiB | Release build frontend | Keep |
| bandit | 374 KiB | CI source security checks | Keep |
| pip-audit | Not installed locally | CI dependency vulnerability checks | Keep |
| appimage-builder | Not installed locally | AppImage packaging | Keep |

Private implementations of packaging and security tools would add release and
tooling responsibilities without reducing installed Docking runtime requirements.

## Website dependencies

The three declared npm packages are build or test dependencies. They do not ship
as browser JavaScript libraries.

| Package | Version | Own payload | Complete chain | Usage | Decision |
|---|---|---|---|---|---|
| tailwindcss | 3.4.17 | 5.47 MiB | 11.60 MiB; 73 installed instances | Generate utility CSS for two source files | Reasonable replacement candidate |
| parse5 | 7.2.1 | 678 KiB | 1.06 MiB; 2 packages | Parse/serialize translated HTML and SEO tests | Keep |
| playwright | 1.56.1 | 3.70 MiB | 11.54 MiB; 2 packages | Chromium browser acceptance tests | Keep |

There are 77 installed npm package instances. The lockfile includes two
additional optional fsevents instances absent on the inspected machine.

Tailwind's 72 transitive instances provide CSS processing, class discovery,
configuration, module resolution, watching, and supporting utilities. Docking
calls none directly; removing Tailwind removes that entire chain. The generated
stylesheet at the baseline is only 28,586 bytes. Retaining that generated file
eliminates the build toolchain but retains generated third-party output.
Converting to maintained CSS also removes reliance on Tailwind conventions, at
the cost of maintaining the style rules and verifying all languages and layouts.

Parse5's two imported functions, parse and serialize, exercise its core purpose.
Its transitive package, entities, handles character references. Ad hoc parsing
would introduce HTML correctness work for modest savings.
[Parse5 documentation](https://parse5.js.org/).

Playwright's transitive package is playwright-core. Sizes exclude browser
downloads. The website uses enough automation behavior that extracting a Chromium
subset would become a private browser automation framework.
[Playwright browser documentation](https://playwright.dev/docs/browsers).

Google Fonts is another website dependency outside npm, supplying two font
families. Self-hosting those assets or selecting system fonts would address that
external dependency separately.

## Reduction priorities

| Priority | Change | Gross baseline reduction | Maintenance transferred |
|---|---|---|---|
| 1 | Replace the weather stack; implemented | 21 runtime distributions; 13.72 MiB; 189,716 Python lines | Endpoint parsing, persistent cache and bounded retries |
| 2 | Replace selected SSIM/PSNR behavior, retain NumPy/Pillow; implemented | 6 development distributions; 145.8 MiB | Numerical implementation and compatibility tests |
| 3 | Replace Tailwind with maintained CSS | 73 npm package instances; 11.60 MiB | Style rules and visual verification |
| 4 | Make WebKit installation optional | Potentially large baseline native reduction | Packaging choices; selected browser feature still requires WebKit |
| 5 | Consider replacing Pillow in test tooling | Up to 18.4 MiB once its other consumers are addressed | Image operations and pixel correctness |

These are different runtime, development, and website environments. Their sizes
must not be added to describe an end-user installation.

## Policy for future dependencies

For each dependency or proposed extraction:

1. Define the exact behavior required and inspect the complete dependency chain.
2. Prefer existing Docking helpers or the Python standard library.
3. Compare the code Docking would own with packages actually eliminated after
   accounting for shared dependencies.
4. Test against an independent reference, then retain provenance and upstream
   tracking for copied code.

For weather, the relevant contract includes real JSON fixtures, required and
optional values, UTC dates, restart persistence, expiry, concurrent writes,
network failures, retry exhaustion, and HTTP errors. Standard-library HTTP
already supplies HTTPS verification, proxies, and timeouts.
[Python HTTP documentation](https://docs.python.org/3.10/library/urllib.request.html).

Copied code remains third-party-derived code and its license obligations remain.
For example, MIT requires preservation of applicable notices.
[MIT license](https://opensource.org/license/mit).

The current pip-audit workflow examines package requirements. Extracted source
fragments no longer receive the same automatic package/version-based advisory
tracking, so their origin and upstream version need to remain identifiable.
[pip-audit documentation](https://pypi.org/project/pip-audit/).
