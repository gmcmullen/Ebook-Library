"""Merge metadata from several providers into a single record."""
import threading
from typing import Dict, Optional

from .categorize import categorize_from_subjects
from .config import Config
from .http_client import RateLimitedSession
from .logging_setup import get_logger
from .providers import Providers
from .text import normalize_author, search_title_variants

logger = get_logger()

# Fields taken from an API only when the local file did not supply them.
PREFER_LOCAL_FIELDS = ('title', 'author', 'description', 'series', 'series_index')

# Fields an API is always authoritative for (the EPUB rarely carries them).
API_FIELDS = ('publisher', 'published_date', 'page_count', 'isbn')


def _surname(name: str) -> str:
    """The last word of a name, lowercased — enough to sanity-check a match."""
    parts = normalize_author(name).split()
    return parts[-1].lower() if parts else ""


def _author_plausible(expected: str, candidates: list) -> bool:
    """Whether a title-only search result is credibly by the expected author.

    Surnames are compared because forenames vary far more across editions
    (initials, middle names, anglicised spellings).
    """
    if not expected or not candidates:
        return False
    target = _surname(expected)
    if not target:
        return False
    return any(_surname(c) == target for c in candidates)


class MetadataService:
    """Look up and merge book metadata, caching by author|title."""

    def __init__(self, http: RateLimitedSession, config: Config):
        self.providers = Providers(http, config)
        self._cache: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()

    def lookup(self, title: str, author: str, existing: Optional[Dict] = None) -> Dict:
        """Return metadata for a book, preferring what ``existing`` already has.

        The result always carries a ``subjects`` list (possibly empty) merged
        from every provider that answered; the caller stores it as
        ``api_subjects`` so categories can later be recomputed offline.
        """
        existing = existing or {}
        cache_key = f"{author.lower()}|{title.lower()}"

        with self._cache_lock:
            if cache_key in self._cache:
                return dict(self._cache[cache_key])

        result: Dict = {}
        subjects: list = []

        # Filenames often bury the real title in series noise, so a failed
        # lookup is retried with a stripped-down variant before giving up.
        variants = search_title_variants(title) or [title]

        for variant in variants:
            google = self.providers.google_books(variant, author)
            if google:
                subjects.extend(google.pop('subjects', []))
                result.update(google)
                break

        # OpenLibrary fills the gaps Google left. Its subject lists are usually
        # far richer, which is what makes categorisation work at all.
        need_subjects = not subjects
        need_description = not (existing.get('description') or result.get('description'))

        if need_subjects or need_description:
            attempts = [(v, author) for v in variants]
            # Last resort: search on the title alone. An author recorded under a
            # pen name or a different transliteration will not match by name,
            # but the returned authors are checked before the result is trusted.
            attempts.append((variants[0], ""))

            for variant, search_author in attempts:
                ol = self.providers.openlibrary(variant, search_author)
                if not ol:
                    continue

                found_authors = ol.pop('_authors', [])
                if not search_author and not _author_plausible(author, found_authors):
                    logger.debug(
                        f"Title-only match for {variant!r} rejected: "
                        f"{found_authors} does not match {author!r}"
                    )
                    continue

                work_key = ol.pop('_work_key', '')
                subjects.extend(ol.pop('subjects', []))

                for key, value in ol.items():
                    result.setdefault(key, value)

                if need_description and not result.get('description') and work_key:
                    description = self.providers.openlibrary_description(work_key)
                    if description:
                        result['description'] = description
                break

        # Wikipedia is the last resort for a blurb only.
        if not (existing.get('description') or result.get('description')):
            for variant in variants:
                description = self.providers.wikipedia_description(variant)
                if description:
                    result['description'] = description
                    break

        # Never overwrite metadata the file itself provided.
        merged = {}
        for key, value in result.items():
            if key in PREFER_LOCAL_FIELDS and existing.get(key):
                continue
            merged[key] = value

        if subjects:
            # De-duplicate case-insensitively while preserving order.
            seen = set()
            unique = []
            for subject in subjects:
                lowered = subject.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    unique.append(subject)
            merged['api_subjects'] = unique

        with self._cache_lock:
            self._cache[cache_key] = dict(merged)

        return merged

    @staticmethod
    def categorize(book: Dict) -> list:
        """Categories for a book, preferring subjects set in the file itself.

        Tags a person put on a book in their reader or editor outrank anything
        an API returns — that edit is the whole point of making it, and it must
        survive the next scan.
        """
        subjects = book.get('file_subjects') or book.get('api_subjects') or []
        return categorize_from_subjects(
            subjects,
            title=book.get('title', ''),
            description=book.get('description', ''),
        )
