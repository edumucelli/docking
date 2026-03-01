"""Quote applet inspired by the classic Cairo-Dock Quote plugin.

Features:
- Multiple legacy source labels (Quotationspage, Bash, Xkcdb, etc.)
- Click to advance to next quote
- Menu actions: next, copy current, refresh from web, change source
- Built-in fallback quotes so it still works offline
"""

from __future__ import annotations

import html
import json
import math
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet

if TYPE_CHECKING:
    from docking.core.config import Config

TWO_PI = 2 * math.pi

DEFAULT_SOURCE = "quotationspage"

SOURCE_LABELS: dict[str, str] = {
    "quotationspage": "Quotationspage.com",
    "qdb": "Qdb.us",
    "danstonchat": "Danstonchat.com",
    "viedemerde": "Viedemerde.fr",
    "fmylife": "Fmylife.com",
    "vitadimerda": "Vitadimerda.it",
    "chucknorrisfactsfr": "Chucknorrisfacts.fr",
}


@dataclass(frozen=True)
class QuoteEntry:
    text: str
    author: str = ""


FALLBACK_QUOTES: dict[str, tuple[QuoteEntry, ...]] = {
    "quotationspage": (
        QuoteEntry("Simplicity is the soul of efficiency.", "Austin Freeman"),
        QuoteEntry("Well done is better than well said.", "Benjamin Franklin"),
        QuoteEntry("First, solve the problem. Then, write the code.", "John Johnson"),
    ),
    "qdb": (
        QuoteEntry("Never test for an error condition you don't know how to handle."),
        QuoteEntry("Debugging is archaeology with breakpoints."),
        QuoteEntry("Logs are a time machine for bugs."),
    ),
    "danstonchat": (
        QuoteEntry("I refactored everything, now nothing is where it was."),
        QuoteEntry("If it's stupid and it works, document it."),
        QuoteEntry("The deadline is tomorrow; the bug is today."),
    ),
    "viedemerde": (
        QuoteEntry("Today I fixed one bug and discovered three."),
        QuoteEntry("Today production taught us a lesson in humility."),
        QuoteEntry("Today I trusted a quick workaround."),
    ),
    "fmylife": (
        QuoteEntry("Today I said 'tiny change'."),
        QuoteEntry("Today cache invalidation won."),
        QuoteEntry("Today tests passed and runtime disagreed."),
    ),
    "vitadimerda": (
        QuoteEntry("Today CI was green until I looked at it."),
        QuoteEntry("Today I optimized the wrong thing."),
        QuoteEntry("Today I merged right before lunch."),
    ),
    "chucknorrisfactsfr": (
        QuoteEntry("Chuck Norris can unit test entire systems with one assert."),
        QuoteEntry(
            "Chuck Norris commits directly to production. Production says thanks."
        ),
        QuoteEntry("Chuck Norris does not need retries. The network retries itself."),
    ),
}


def _normalize_quote(text: str) -> str:
    clean = html.unescape(text).replace("\n", " ").replace("\r", " ").strip()
    return " ".join(clean.split())


def format_quote(entry: QuoteEntry) -> str:
    """Render quote text for tooltip/clipboard."""
    if entry.author:
        return f'"{entry.text}" — {entry.author}'
    return entry.text


def _http_get_json(url: str, timeout: float = 8.0) -> Any:
    request = Request(
        url=url,
        headers={"User-Agent": "DockingQuoteApplet/1.0 (+https://github.com/)"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _parse_zenquotes(data: Any, limit: int) -> list[QuoteEntry]:
    quotes: list[QuoteEntry] = []
    if not isinstance(data, list):
        return quotes
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_text = item.get("q")
        raw_author = item.get("a", "")
        if not isinstance(raw_text, str):
            continue
        text = _normalize_quote(raw_text)
        author = _normalize_quote(raw_author) if isinstance(raw_author, str) else ""
        if text:
            quotes.append(QuoteEntry(text=text, author=author))
        if len(quotes) >= limit:
            break
    return quotes


def _parse_jokeapi(data: Any, limit: int) -> list[QuoteEntry]:
    quotes: list[QuoteEntry] = []
    if not isinstance(data, dict):
        return quotes

    jokes = data.get("jokes")
    if isinstance(jokes, list):
        entries = jokes
    else:
        entries = [data]

    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_joke = item.get("joke")
        if not isinstance(raw_joke, str):
            continue
        text = _normalize_quote(raw_joke)
        if text:
            quotes.append(QuoteEntry(text=text))
        if len(quotes) >= limit:
            break
    return quotes


def _parse_chuck(data: Any) -> list[QuoteEntry]:
    if not isinstance(data, dict):
        return []
    value = data.get("value")
    if not isinstance(value, str):
        return []
    text = _normalize_quote(value)
    if not text:
        return []
    return [QuoteEntry(text=text)]


def fetch_quotes(source: str, limit: int = 20) -> list[QuoteEntry]:
    """Fetch quotes for a source. Returns empty list on any failure."""
    try:
        if source == "quotationspage":
            data = _http_get_json("https://zenquotes.io/api/quotes")
            return _parse_zenquotes(data=data, limit=limit)
        if source == "chucknorrisfactsfr":
            data = _http_get_json("https://api.chucknorris.io/jokes/random")
            return _parse_chuck(data=data)
        data = _http_get_json(
            f"https://v2.jokeapi.dev/joke/Any?type=single&amount={limit}"
        )
        return _parse_jokeapi(data=data, limit=limit)
    except Exception:
        return []


def _source_fallback(source: str) -> list[QuoteEntry]:
    quotes = FALLBACK_QUOTES.get(source) or FALLBACK_QUOTES[DEFAULT_SOURCE]
    return list(quotes)


class QuoteApplet(Applet):
    """Quote of the day style applet inspired by the legacy Cairo-Dock plugin."""

    id = "quote"
    name = "Quote"
    icon_name = "idea"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._source = DEFAULT_SOURCE
        self._quotes: list[QuoteEntry] = []
        self._index = -1
        self._current: QuoteEntry | None = None
        self._loading = False
        self._clipboard: Gtk.Clipboard | None = None

        if config:
            prefs = config.applet_prefs.get("quote", {})
            source = prefs.get("source", DEFAULT_SOURCE)
            if source in SOURCE_LABELS:
                self._source = source

        super().__init__(icon_size, config)
        self._quotes = _source_fallback(source=self._source)
        self._advance_quote()
        self._update_item_name()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        self._draw_bulb_icon(cr=cr, size=size)
        if hasattr(self, "item"):
            self._update_item_name()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        if self._advance_quote():
            self.refresh_icon()
            # Fetch next batch in background when we reach the end.
            if self._index >= len(self._quotes) - 1:
                self._fetch_async(show_first=False)
            return
        self._current = None
        self._set_loading_name()
        self.refresh_icon()
        self._fetch_async(show_first=True)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        source_header = Gtk.MenuItem(label=SOURCE_LABELS.get(self._source, "Quote"))
        source_header.set_sensitive(False)
        items.append(source_header)

        next_item = Gtk.MenuItem(label="Next Quote")
        next_item.connect("activate", lambda _: self.on_clicked())
        items.append(next_item)

        copy_item = Gtk.MenuItem(label="Copy Quote")
        copy_item.connect("activate", lambda _: self._copy_current_quote())
        items.append(copy_item)

        refresh_item = Gtk.MenuItem(label="Refresh from Web")
        refresh_item.connect("activate", lambda _: self._refresh_from_web())
        items.append(refresh_item)

        items.append(Gtk.SeparatorMenuItem())

        source_title = Gtk.MenuItem(label="Source")
        source_title.set_sensitive(False)
        items.append(source_title)

        group: Gtk.RadioMenuItem | None = None
        for source_id, label in SOURCE_LABELS.items():
            radio = Gtk.RadioMenuItem(label=label)
            if group:
                radio.join_group(group)
            else:
                group = radio
            radio.set_active(source_id == self._source)
            radio.connect("toggled", self._on_source_toggled, source_id)
            items.append(radio)

        return items

    def _on_source_toggled(self, widget: Gtk.RadioMenuItem, source_id: str) -> None:
        if not widget.get_active():
            return
        self._set_source(source_id=source_id)

    def _set_source(self, source_id: str) -> None:
        if source_id not in SOURCE_LABELS or source_id == self._source:
            return
        self._source = source_id
        self.save_prefs({"source": self._source})
        self._quotes = _source_fallback(source=self._source)
        self._index = -1
        self._current = None
        self._advance_quote()
        self.refresh_icon()
        self._fetch_async(show_first=False)

    def _refresh_from_web(self) -> None:
        self._fetch_async(show_first=True)

    def _copy_current_quote(self) -> None:
        if not self._current:
            return
        text = format_quote(self._current)
        try:
            if self._clipboard is None:
                self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            self._clipboard.set_text(text, -1)
            self._clipboard.store()
        except Exception:
            return

    def _fetch_async(self, show_first: bool) -> None:
        if self._loading:
            return
        self._loading = True
        if show_first:
            self._set_loading_name()
            self.refresh_icon()

        source = self._source

        def worker() -> None:
            quotes = fetch_quotes(source=source, limit=20)
            GLib.idle_add(self._on_fetch_result, source, quotes, show_first)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_result(
        self,
        source_id: str,
        quotes: list[QuoteEntry],
        show_first: bool,
    ) -> bool:
        self._loading = False
        # Ignore stale async results if source changed in the meantime.
        if source_id != self._source:
            return False

        if quotes:
            self._quotes = quotes
            self._index = -1
            if show_first:
                self._current = None
                self._advance_quote()
        elif self._current is None:
            # Always recover to local fallback.
            self._quotes = _source_fallback(source=self._source)
            self._index = -1
            self._advance_quote()

        self._update_item_name()
        self.refresh_icon()
        return False

    def _advance_quote(self) -> bool:
        nxt = self._index + 1
        if nxt >= len(self._quotes):
            return False
        self._index = nxt
        self._current = self._quotes[nxt]
        self._update_item_name()
        return True

    def _set_loading_name(self) -> None:
        if hasattr(self, "item"):
            self.item.name = f"{SOURCE_LABELS.get(self._source, 'Quote')}: loading..."

    def _update_item_name(self) -> None:
        if not hasattr(self, "item"):
            return
        if self._current:
            self.item.name = format_quote(self._current)
            return
        self.item.name = "Quote"

    def _draw_bulb_icon(self, cr: cairo.Context, size: int) -> None:
        cx = size / 2
        bulb_r = size * 0.34
        bulb_cy = size * 0.36

        # Bulb glass
        cr.arc(cx, bulb_cy, bulb_r, 0, TWO_PI)
        cr.set_source_rgba(1.0, 0.87, 0.20, 0.96)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.4, size * 0.045))
        cr.stroke()

        # Neck
        neck_w = size * 0.22
        neck_h = size * 0.12
        neck_x = cx - neck_w / 2
        neck_y = bulb_cy + bulb_r * 0.62
        self._rounded_rect(cr=cr, x=neck_x, y=neck_y, w=neck_w, h=neck_h, r=size * 0.03)
        cr.set_source_rgba(0.92, 0.78, 0.18, 0.98)
        cr.fill()

        # Base
        base_w = size * 0.30
        base_h = size * 0.21
        base_x = cx - base_w / 2
        base_y = neck_y + neck_h - size * 0.01
        self._rounded_rect(
            cr=cr,
            x=base_x,
            y=base_y,
            w=base_w,
            h=base_h,
            r=size * 0.035,
        )
        cr.set_source_rgba(0.33, 0.35, 0.40, 0.97)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.set_line_width(max(1.0, size * 0.03))
        cr.stroke()

        # Base grooves
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(max(1.0, size * 0.02))
        for i in range(3):
            y = base_y + base_h * (0.28 + i * 0.22)
            cr.move_to(base_x + size * 0.02, y)
            cr.line_to(base_x + base_w - size * 0.02, y)
            cr.stroke()

        # Highlight
        cr.arc(cx - bulb_r * 0.35, bulb_cy - bulb_r * 0.25, bulb_r * 0.32, 0, TWO_PI)
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.fill()

    def _rounded_rect(
        self,
        cr: cairo.Context,
        x: float,
        y: float,
        w: float,
        h: float,
        r: float,
    ) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
