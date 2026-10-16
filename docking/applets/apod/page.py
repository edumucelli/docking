"""Extract APOD content from NASA Science's media detail hero."""

from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

APOD_URL = "https://science.nasa.gov/apod/"
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _row_label(text: str) -> str:
    """Normalize a metadata row label.

    NASA writes these headers both with and without a trailing colon, and has
    renamed them over time ("Credit & Copyright" became "Credit").
    """
    return " ".join(text.split()).rstrip(" :")


_CREDIT_LABELS = ("Credit & Copyright", "Credit", "Image Credit", "Video Credit")


def _credit(metadata: dict[str, str]) -> str:
    """Return the credit row, which NASA labels by media type."""
    for label in _CREDIT_LABELS:
        value = metadata.get(label, "")
        if value:
            return value
    return ""


class _ApodParser(HTMLParser):
    """Scope extraction to the hero, ignoring navigation and NASA branding."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, str]] = []
        self.text: dict[str, list[str]] = {}
        self.metadata: dict[str, str] = {}
        self.image_url = ""
        self.video_url = ""
        # A poster is a video's still, so it is only media alongside a video.
        self.poster_url = ""
        self.page_url = ""

    def _absolute(self, value: str | None) -> str:
        """Resolve a page-relative URL, treating a missing attribute as absent."""
        return urljoin(APOD_URL, value) if value else ""

    def _in_video_player(self) -> bool:
        """Inside <video>, an anchor is the player's no-JS fallback, not the page."""
        return any(frame[0] == "video" for frame in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        _, hero, media, field = self.stack[-1] if self.stack else ("", False, False, "")
        hero = hero or bool(
            {"hds-media-detail-hero", "media-detail-hero"}.intersection(classes)
        )
        media = media or (hero and "media-detail-hero__media" in classes)
        if hero:
            if tag == "h2":
                field = "title"
            elif "media-detail-hero__description" in classes:
                field = "explanation"
            elif tag in {"th", "td"}:
                field = tag
                self.text[field] = []
            if media:
                if tag == "a" and not self.page_url and not self._in_video_player():
                    self.page_url = self._absolute(values.get("href"))
                elif tag == "img" and not self.image_url:
                    self.image_url = self._absolute(values.get("src"))
                elif tag in {"iframe", "video", "source"} and not self.video_url:
                    # NASA now serves some entries from its own player as an
                    # HTML5 <video> with a <source>, not a YouTube <iframe>.
                    self.video_url = self._absolute(values.get("src"))
                if tag == "video" and not self.poster_url:
                    self.poster_url = self._absolute(values.get("poster"))
            if tag == "br" and field:
                self.text.setdefault(field, []).append("\n")
        if tag not in _VOID_TAGS:
            self.stack.append((tag, hero, media, field))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                if tag == "td" and self.stack[index][1]:
                    label = _row_label("".join(self.text.get("th", [])))
                    value = " ".join("".join(self.text.get("td", [])).split())
                    self.metadata[label] = value
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.stack:
            _, hero, _, field = self.stack[-1]
            if hero and field:
                self.text.setdefault(field, []).append(data)


def _published_date(*, raw: str) -> date:
    """Read NASA's "October 6, 2026" metadata value.

    NASA uses English month names regardless of the desktop's locale.
    """
    try:
        month, day, year = raw.replace(",", "").split()
        return date(int(year), _MONTHS.index(month) + 1, int(day))
    except ValueError as exc:
        raise ValueError(f"APOD page has an unreadable date: {raw!r}") from exc


def parse_page(html: str) -> dict[str, str]:
    """Return API-shaped metadata, rejecting incomplete or unrelated pages."""
    parser = _ApodParser()
    parser.feed(html)
    title = " ".join("".join(parser.text.get("title", [])).split())
    if not title or not (parser.image_url or parser.video_url):
        raise ValueError("APOD page is missing its title or media")
    published = _published_date(raw=parser.metadata.get("Date", ""))
    explanation = "".join(parser.text.get("explanation", [])).strip()
    explanation = explanation.removeprefix("Explanation:").strip()
    # Site announcements and tomorrow's teaser follow the explanation.
    explanation = explanation.split("APOD's", 1)[0].split("Tomorrow's picture:", 1)[0]
    thumbnail = parser.image_url or parser.poster_url
    if parser.video_url and not thumbnail:
        video = urlparse(parser.video_url)
        if video.hostname in {
            "www.youtube.com",
            "youtube.com",
            "www.youtube-nocookie.com",
            "youtube-nocookie.com",
        }:
            video_id = (
                video.path.removeprefix("/embed/")
                if video.path.startswith("/embed/")
                else parse_qs(video.query).get("v", [""])[0]
            )
            if video_id and all(c.isalnum() or c in "_-" for c in video_id):
                thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        elif parser.video_url.lower().endswith(".mp4"):
            # A self-hosted video has no poster attribute, but NASA serves the
            # matching still at the same path with a .jpg extension.
            thumbnail = parser.video_url[:-4] + ".jpg"
    return {
        "date": published.isoformat(),
        "title": title,
        "explanation": " ".join(explanation.split()),
        "media_type": "video" if parser.video_url else "image",
        "url": parser.video_url or parser.image_url,
        "thumbnail_url": thumbnail,
        "page_url": parser.page_url or APOD_URL,
        "copyright": _credit(parser.metadata),
    }
