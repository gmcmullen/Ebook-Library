"""Metadata and cover lookups against Google Books, OpenLibrary and Wikipedia.

Each provider returns a plain dict of whatever it could find; the caller decides
how to merge. Every function is failure-tolerant and returns ``{}`` rather than
raising, so one dead API never aborts a scan.
"""
import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from .config import Config
from .http_client import RateLimitedSession
from .logging_setup import get_logger
from .text import clean_description, parse_series_from_title

logger = get_logger()

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPENLIBRARY_COVER = "https://covers.openlibrary.org/b"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"

# Fields OpenLibrary can return inline, so subjects arrive with the search
# result instead of costing a second request per book.
OPENLIBRARY_FIELDS = (
    "key,title,author_name,subject,first_publish_year,cover_i,isbn,series,edition_count"
)

# OpenLibrary relevance often puts a bare, subject-less work first, so several
# results are fetched and the best-described one is chosen.
OPENLIBRARY_LIMIT = 5


def _best_doc(docs: List[Dict]) -> Dict:
    """Pick the most usefully described search result.

    OpenLibrary's top hit is frequently a thin work record with no subjects at
    all, while a lower-ranked duplicate of the same book carries a full set.
    Since the query is already constrained by author, preferring the richest
    record picks a better edition of the same book rather than a different one.
    """
    return max(
        docs,
        key=lambda d: (len(d.get('subject') or []), d.get('edition_count') or 0),
    )


class Providers:
    """Thin wrappers over the three upstream APIs."""

    def __init__(self, http: RateLimitedSession, config: Config):
        self.http = http
        self.config = config

    # ------------------------------------------------------------------ Google

    def _google_url(self, query: str) -> str:
        url = f"{GOOGLE_BOOKS_API}?q={query}&maxResults=1"
        if self.config.google_books_api_key:
            url += f"&key={self.config.google_books_api_key}"
        return url

    def google_books(self, title: str, author: str = "", isbn: str = "") -> Dict:
        """Look a book up on Google Books by ISBN if known, else title+author."""
        query = f"isbn:{isbn}" if isbn else quote_plus(f"{title} {author}".strip())
        data = self.http.get_json(self._google_url(query))
        if not data or not data.get('items'):
            return {}

        info = data['items'][0].get('volumeInfo', {})
        result: Dict = {
            'title': info.get('title', ''),
            'author': (info.get('authors') or [''])[0],
            'description': clean_description(info.get('description', '')),
            'publisher': info.get('publisher', ''),
            'published_date': info.get('publishedDate', ''),
            'page_count': info.get('pageCount', ''),
            'subjects': [c.strip() for c in info.get('categories', []) if c.strip()],
        }

        identifiers = info.get('industryIdentifiers') or []
        # Prefer ISBN-13 over whatever happens to be listed first.
        for wanted in ('ISBN_13', 'ISBN_10'):
            match = next((i for i in identifiers if i.get('type') == wanted), None)
            if match:
                result['isbn'] = match.get('identifier', '')
                break

        thumbnail = (info.get('imageLinks') or {}).get('thumbnail', '')
        if thumbnail:
            result['cover_url'] = thumbnail.replace('&edge=curl', '')

        subtitle = info.get('subtitle', '')
        if subtitle:
            series_name, series_index = parse_series_from_title(subtitle)
            if series_name:
                result['series'] = series_name
                if series_index is not None:
                    result['series_index'] = series_index

        return {k: v for k, v in result.items() if v not in ('', [], None)}

    @staticmethod
    def google_cover_variants(thumbnail: str) -> List[str]:
        """Progressively higher-resolution variants of a Google Books thumbnail."""
        base = re.sub(r'&edge=curl', '', thumbnail)
        variants = []
        for zoom in ('3', '2', '1'):
            if 'zoom=' in base:
                variants.append(re.sub(r'zoom=\d+', f'zoom={zoom}', base))
            else:
                variants.append(f"{base}&zoom={zoom}")
        return variants

    def google_thumbnail(self, title: str, author: str = "", isbn: str = "") -> str:
        """The raw thumbnail URL for a book, or ''."""
        query = f"isbn:{isbn}" if isbn else quote_plus(f"{title} {author}".strip())
        data = self.http.get_json(self._google_url(query))
        if not data or not data.get('items'):
            return ""
        info = data['items'][0].get('volumeInfo', {})
        return (info.get('imageLinks') or {}).get('thumbnail', '')

    # ------------------------------------------------------------ OpenLibrary

    def openlibrary(self, title: str, author: str) -> Dict:
        """Search OpenLibrary, returning subjects inline from the search result.

        Requesting ``fields=`` gets subjects back with the search response, so
        the common case costs one request instead of two. The work record is
        only fetched when a description is still missing.
        """
        if author:
            url = (
                f"{OPENLIBRARY_SEARCH}?title={quote_plus(title)}"
                f"&author={quote_plus(author)}&fields={OPENLIBRARY_FIELDS}&limit={OPENLIBRARY_LIMIT}"
            )
        else:
            url = (
                f"{OPENLIBRARY_SEARCH}?title={quote_plus(title)}"
                f"&fields={OPENLIBRARY_FIELDS}&limit={OPENLIBRARY_LIMIT}"
            )
        data = self.http.get_json(url)

        # Fall back to a free-text search when the structured one finds nothing;
        # this catches titles whose author field is formatted unusually.
        if author and (not data or not data.get('docs')):
            url = (
                f"{OPENLIBRARY_SEARCH}?q={quote_plus(f'{title} {author}')}"
                f"&fields={OPENLIBRARY_FIELDS}&limit={OPENLIBRARY_LIMIT}"
            )
            data = self.http.get_json(url)

        if not data or not data.get('docs'):
            return {}

        doc = _best_doc(data['docs'])
        result: Dict = {'_authors': doc.get('author_name') or []}

        subjects = doc.get('subject') or []
        if subjects:
            # OpenLibrary returns hundreds of subjects for popular works; the
            # leading ones are the most representative.
            result['subjects'] = [s.strip() for s in subjects[:40] if s.strip()]

        series = doc.get('series')
        if series:
            result['series'] = series[0] if isinstance(series, list) else series

        if doc.get('first_publish_year'):
            result['published_date'] = str(doc['first_publish_year'])

        isbns = doc.get('isbn') or []
        if isbns:
            result['isbn'] = isbns[0]

        if doc.get('cover_i'):
            result['cover_url'] = f"{OPENLIBRARY_COVER}/id/{doc['cover_i']}-L.jpg"

        result['_work_key'] = doc.get('key', '')
        return result

    def openlibrary_description(self, work_key: str) -> str:
        """Fetch a work's description from OpenLibrary given its key."""
        if not work_key:
            return ""
        data = self.http.get_json(f"https://openlibrary.org{work_key}.json")
        if not data:
            return ""
        raw = data.get('description', '')
        if isinstance(raw, dict):
            raw = raw.get('value', '')
        return clean_description(raw) if raw else ""

    def openlibrary_isbn_cover(self, isbn: str) -> str:
        return f"{OPENLIBRARY_COVER}/isbn/{isbn}-L.jpg" if isbn else ""

    # -------------------------------------------------------------- Wikipedia

    def wikipedia_description(self, title: str) -> str:
        """Wikipedia's article summary for a title, used as a last-resort blurb."""
        data = self.http.get_json(f"{WIKIPEDIA_SUMMARY}/{quote_plus(title)}")
        if data and data.get('type') == 'standard' and data.get('extract'):
            return clean_description(data['extract'])
        return ""

    def wikipedia_image(self, title: str, author: str) -> Optional[str]:
        """The lead image of the best-matching Wikipedia article, or None."""
        search = self.http.get_json(
            f"{WIKIPEDIA_API}?action=query&list=search"
            f"&srsearch={quote_plus(f'{title} {author}')}&format=json"
        )
        results = (search or {}).get('query', {}).get('search') or []
        if not results:
            return None

        page_id = results[0]['pageid']
        detail = self.http.get_json(
            f"{WIKIPEDIA_API}?action=query&pageids={page_id}"
            f"&prop=pageimages&format=json&pithumbsize=600"
        )
        thumb = (
            (detail or {}).get('query', {})
            .get('pages', {})
            .get(str(page_id), {})
            .get('thumbnail')
        )
        return thumb.get('source') if thumb else None
