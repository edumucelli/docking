# Code Quality Improvements Plan

## Phase 1 — Extract Shared Utilities

### 1A. Create `docking/applets/_cairo.py` with shared drawing helpers

Extract `_rounded_rect()` from 11 files into one shared function:
- `docking/applets/desktop/render.py:19`
- `docking/applets/ambient/render.py:15`
- `docking/applets/music/render.py:17`
- `docking/applets/trash/render.py:31`
- `docking/applets/battery/render.py:22`
- `docking/applets/network/render.py:76`
- `docking/applets/screenshot/render.py:10`
- `docking/applets/notifications/render.py:17`
- `docking/applets/applications/render.py:18`
- `docking/applets/quote/render.py:68`
- `docking/ui/shelf.py:19`

Compare signatures — two variants exist:
- `_rounded_rect(cr, x, y, w, h, r)` — most files
- `_rounded_rect_path(cr, x, y, w, h, r)` — ambient, music, screenshot

Verify if both fill/stroke or just build path. Unify into one function that only builds the path (callers fill/stroke themselves).

### 1B. Replace `TWO_PI` with `math.tau`

10 files define `TWO_PI = 2 * math.pi`. Python's `math.tau` is identical. Replace all:
- `docking/applets/brightness/render.py:16`
- `docking/applets/notifications/render.py:14`
- `docking/applets/hydration/render.py:22`
- `docking/applets/moon/render.py:23`
- `docking/applets/colorpicker/render.py:16`
- `docking/applets/cpumonitor/render.py:16`
- `docking/applets/session/render.py:14`
- `docking/applets/bluetooth/render.py:14`
- `docking/applets/quote/render.py:9`
- `docking/applets/pomodoro/render.py:17`

For each: delete the `TWO_PI` line, replace all `TWO_PI` usages with `math.tau`. Some files already use `math.tau` directly (desktop, applications, clippy, network) — no change needed there.

### 1C. Extract pointer position helper in `docking/ui/runtime.py`

4 call sites repeat the same 3-line pattern:
- `docking/ui/runtime.py:88`
- `docking/ui/interaction.py:257`
- `docking/ui/placement.py:453`
- `docking/applets/calendar/applet.py:123`

Extract:
```python
def get_pointer_position(display: Gdk.Display) -> tuple[int, int]:
    seat = display.get_default_seat()
    pointer = seat.get_pointer()
    _, x, y = pointer.get_position()
    return x, y
```

Place in `docking/ui/runtime.py` (already has display utilities). Update all call sites.

### 1D. Extract screen clamping helper

Identical clamping in:
- `docking/ui/tooltip.py:414-415`
- `docking/ui/preview.py:454-455`

Extract into `docking/ui/runtime.py`:
```python
def clamp_to_screen(x, y, w, h, screen_w, screen_h) -> tuple[int, int]:
    return max(0, min(x, screen_w - w)), max(0, min(y, screen_h - h))
```

---

## Phase 2 — Simplify Core Logic

### 2A. Single-pass `visible_items()` in `docking/platform/model.py:349-364`

Current: 3 list comprehensions iterate `items` 3 times.

Replace with single-pass classification:
```python
def visible_items(self) -> list[DockItem]:
    items = self.pinned_items + self._transient
    anchor_applets = self._config.anchor_applets
    anchor_files = self._config.anchor_files
    regular, files, applet_items = [], [], []
    for i in items:
        if anchor_applets and i.kind == APPLET_KIND:
            applet_items.append(i)
        elif anchor_files and i.kind in {FILE_KIND, FOLDER_KIND}:
            files.append(i)
        else:
            regular.append(i)
    return regular + files + applet_items
```

### 2B. Consolidate `_normalize_int` / `_normalize_float` in `docking/core/config.py:237-292`

Both functions are structurally identical — only `int()` vs `float()` differs. Merge into one generic:
```python
def _normalize_numeric(
    value: object,
    *,
    type_: type[int] | type[float],
    default: int | float,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
) -> int | float:
```

Keep `_normalize_bool` separate — its logic is different.

### 2C. Hoist strut index maps to module level in `docking/platform/struts.py:183-194`

Move `idx_edge`, `idx_start`, `gap` dicts out of `compute_struts()` body to module-level constants. They're pure data, rebuilt every call for no reason.

### 2D. Walrus in `normalize_pinned_entries` in `docking/core/config.py:470-476`

```python
def normalize_pinned_entries(raw_entries: list[object]) -> list[PinnedEntry]:
    return [e for raw in raw_entries if (e := PinnedEntry.from_raw(raw)) is not None]
```

---

## Phase 3 — Idiom & Style Fixes

### 3A. Replace side-effect dedup in `docking/platform/window_tracker.py:174-176`

```python
# Before
seen: set[str] = set()
return [c for c in candidates if not (c in seen or seen.add(c))]

# After
return list(dict.fromkeys(candidates))
```

### 3B. Use `nonlocal` instead of mutable-list-of-one in `docking/ui/hover.py:292-296`

```python
# Before
frames_left = [duration_ms // 16]
def tick() -> bool:
    frames_left[0] -= 1
    if frames_left[0] <= 0:

# After
frames_left = duration_ms // 16
def tick() -> bool:
    nonlocal frames_left
    frames_left -= 1
    if frames_left <= 0:
```

### 3C. Use `any()` instead of for-break in `docking/ui/hover.py`

```python
# Before
for item in self._model.visible_items():
    if item.is_urgent and item.last_urgent > 0:
        self.start_anim_pump(duration_ms=700)
        break

# After
if any(item.is_urgent and item.last_urgent > 0 for item in self._model.visible_items()):
    self.start_anim_pump(duration_ms=700)
```

### 3D. `_ink` → `_` in 5 call sites

- `docking/applets/base.py:223`
- `docking/applets/clock/render.py:127`
- `docking/applets/clock/render.py:177`
- `docking/applets/calendar/render.py:60`
- `docking/applets/calendar/render.py:73`

### 3E. Remove lazy import in `docking/platform/model.py:177`

`normalize_pinned_entries` is from `docking.core.config` — no circular dep risk since `PinnedEntry` is already imported at top level from the same module. Move to top-level imports.

---

## Phase 4 — Long Method Extraction (Optional)

Lower priority — only do when touching these files for other reasons.

### 4A. `docking/ui/dock_window.py:_on_draw` (90 lines)

Split into:
- `_build_and_render_frame()` — layout + draw
- `_reconcile_post_hide()` — autohide cleanup after animation
- `_pump_animations()` — urgent glow scheduling

### 4B. `docking/ui/interaction.py:on_effective_leave` (48 lines)

Split into:
- `_schedule_preview_on_leave()`
- `_cleanup_hover_state()`

### 4C. `docking/ui/menu.py:_build_dock_menu` (87 lines)

Extract submenu builders:
- `_build_applet_submenu()`
- `_build_settings_section()`

---

## Execution Order

Phases 1-3 are independent at the file level — can be done in any order.
Phase 4 is optional/deferred.

Within Phase 1: do 1A first (largest impact, 11 files), then 1B (10 files, mechanical), then 1C/1D (smaller).

## Unresolved Questions

- 1A: do the `_rounded_rect` variants differ in any way besides name? need to diff all 11 implementations
- 1A: name the shared module `_cairo.py` or `_draw.py`?
- 2B: use `@overload` for return type narrowing or just `int | float`?
- 4A-C: worth doing now or defer until next time those files are touched?
