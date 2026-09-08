"""Find, download and cache book cover images."""
import hashlib
import re
import threading
from pathlib import Path
from typing import Dict, Optional

from .config import Config
from .extract import read_epub_cover
from .http_client import RateLimitedSession
from .logging_setup import Colors, get_logger
from .providers import Providers

logger = get_logger()

# Anything smaller than this is a spacer or an error page, not a cover.
MIN_COVER_BYTES = 10 * 1024

# SHA-256 of OpenLibrary's "image not available" placeholder.
PLACEHOLDER_HASHES = {
    "12557f8948b8bdc6af436e3a8b3adddd45f7f7d2b67c5832e799cdf4686f72bb",
}

COVER_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def cover_base_name(author: str, title: str, year: Optional[int] = None) -> str:
    """Filesystem-safe stem for a cover file, matching the historic naming scheme."""
    author_clean = re.sub(r'[/\\:*?"<>|]', '', author.lower()).replace(' ', '_')
    title_clean = re.sub(r'[/\\:*?"<>|]', '', title.lower()).replace(' ', '_')
    stem = f"{author_clean}-{title_clean}"
    return f"{stem}-{year}" if year else stem


def parse_year(value) -> Optional[int]:
    """First four digits of a published-date string, as an int."""
    try:
        text = str(value)
        return int(text[:4]) if len(text) >= 4 else None
    except (ValueError, TypeError):
        return None


class CoverFetcher:
    """Resolves a cover for a book, preferring local sources over the network.

    Paths are stored relative to the Library directory so books.json and
    books.html stay valid if the library is moved or synced to another machine.
    """

    def __init__(self, http: RateLimitedSession, config: Config):
        self.config = config
        self.providers = Providers(http, config)
        self.http = http
        self._cache: Dict[str, str] = {}
        self._lock = threading.Lock()

    def _relative(self, path: Path) -> str:
        """Path relative to the Library dir, e.g. ``covers/asimov-foundation.jpg``."""
        try:
            return str(Path(path).relative_to(self.config.library_dir))
        except ValueError:
            return str(path)

    def _existing_cover(self, base_name: str) -> Optional[Path]:
        """The most recent already-downloaded cover matching this book, if any."""
        matches = [
            p for p in self.config.covers_dir.glob(f"{base_name}*")
            if p.suffix.lower() in COVER_EXTENSIONS
        ]
        return max(matches, key=lambda p: p.stat().st_mtime) if matches else None

    def _write_cover(self, data: bytes, ext: str, base_name: str) -> str:
        """Save cover bytes, leaving the file alone if it already has this content.

        Rewriting an identical image on every run would make a synced folder
        re-upload every cover in the library, so the content is compared first.
        """
        local_path = self.config.covers_dir / f"{base_name}{ext}"

        if local_path.exists() and local_path.read_bytes() == data:
            return self._relative(local_path)

        local_path.write_bytes(data)

        # Drop other extensions for the same book so one cover does not linger
        # as both .jpg and .jpeg.
        for old in self.config.covers_dir.glob(f"{base_name}.*"):
            if old != local_path and old.suffix.lower() in COVER_EXTENSIONS:
                try:
                    old.unlink()
                except OSError as e:
                    logger.debug(f"Could not remove stale cover {old}: {e}")

        return self._relative(local_path)

    def _download(self, url: str, base_name: str) -> str:
        """Download a candidate cover, rejecting placeholders and thumbnails."""
        if not url:
            return ""

        response = self.http.get(url)
        if not response or response.status_code != 200:
            return ""

        content = response.content
        if len(content) < MIN_COVER_BYTES:
            logger.debug(f"Rejected cover under {MIN_COVER_BYTES // 1024}KB: {url}")
            return ""

        if hashlib.sha256(content).hexdigest() in PLACEHOLDER_HASHES:
            logger.debug(f"Rejected 'image not available' placeholder: {url}")
            return ""

        return self._write_cover(content, '.jpg', base_name)

    def fetch(self, book: Dict, force: bool = False) -> str:
        """Resolve a cover for ``book``, returning a Library-relative path or ''.

        Order: the EPUB's own embedded cover, then a previously cached file,
        then OpenLibrary by ISBN, Google Books, OpenLibrary search and Wikipedia.
        """
        title = book.get('title', '')
        author = book.get('author', '')
        isbn = book.get('isbn', '')
        if not title or not author:
            return ""

        year = parse_year(book.get('published_date'))
        base_name = cover_base_name(author, title, year)
        plain_name = cover_base_name(author, title)
        cache_key = f"{author.lower()}|{title.lower()}|{isbn}"

        with self._lock:
            if not force and cache_key in self._cache:
                return self._cache[cache_key]

        result = ""

        # 1. The cover embedded in the book itself. This is the publisher's own
        # art and always matches the file in hand, so it outranks whatever was
        # cached on an earlier run — a cover replaced inside the EPUB should show
        # up without having to clear the cache first.
        if book.get('path'):
            book_path = self.config.base_dir / book['path']
            if book_path.suffix.lower() == '.epub' and book_path.exists():
                embedded = read_epub_cover(book_path)
                if embedded:
                    data, ext = embedded
                    result = self._write_cover(data, ext, base_name)
                    logger.debug(f"Cover taken from the EPUB for '{title}'")

        # 2. Otherwise fall back to a file cached by an earlier run.
        if not result and not force:
            for candidate in (base_name, plain_name):
                existing = self._existing_cover(candidate)
                if existing:
                    result = self._relative(existing)
                    break

        # 3-6. Network sources, cheapest first.
        if not result and isbn:
            result = self._download(self.providers.openlibrary_isbn_cover(isbn), base_name)

        if not result:
            thumbnail = self.providers.google_thumbnail(title, author)
            for variant in self.providers.google_cover_variants(thumbnail) if thumbnail else []:
                result = self._download(variant, base_name)
                if result:
                    break

        if not result and isbn:
            thumbnail = self.providers.google_thumbnail(title, isbn=isbn)
            for variant in self.providers.google_cover_variants(thumbnail) if thumbnail else []:
                result = self._download(variant, base_name)
                if result:
                    break

        if not result:
            ol = self.providers.openlibrary(title, author)
            if ol.get('cover_url'):
                result = self._download(ol['cover_url'], base_name)

        if not result:
            image_url = self.providers.wikipedia_image(title, author)
            if image_url:
                result = self._download(image_url, base_name)

        if result:
            logger.debug(f"{Colors.GREEN}Cover resolved for '{title}' by {author}{Colors.ENDC}")
        else:
            logger.debug(f"No cover found for '{title}' by {author}")

        with self._lock:
            self._cache[cache_key] = result
        return result
