"""Load, migrate and persist the books.json catalogue."""
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from .config import Config
from .logging_setup import Colors, get_logger
from .text import normalize_space

logger = get_logger()

Book = Dict[str, Any]


class Catalog:
    """The book database, keyed by file hash.

    Writes go through a temporary file and an atomic rename so an interrupted
    save can never leave a truncated books.json behind.
    """

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._pending_migration = 0
        self.books: Dict[str, Book] = self._load()
        if self._pending_migration:
            # Persist the rewrite immediately so read-only commands still benefit.
            self.save()
            self._pending_migration = 0

    def _load(self) -> Dict[str, Book]:
        path = self.config.books_file
        if not path.exists():
            logger.info(f"{Colors.YELLOW}No catalogue at {path} — starting empty{Colors.ENDC}")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                books = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"{Colors.RED}{path} is not valid JSON ({e}) — starting empty{Colors.ENDC}")
            return {}
        except OSError as e:
            logger.error(f"{Colors.RED}Could not read {path}: {e}{Colors.ENDC}")
            return {}

        self._pending_migration = self._migrate(books)
        if self._pending_migration:
            logger.info(f"Migrated {self._pending_migration} fields from an older catalogue format")
        return books

    def _migrate(self, books: Dict[str, Book]) -> int:
        """Bring records written by older versions up to date.

        Rewrites absolute cover paths as relative, strips the stray XML
        whitespace older EPUB parsing left behind, and puts author names into
        canonical form (natural order, regular initials, aliases applied).
        """
        from .authors import canonical_author, load_aliases  # local: avoids a cycle

        library_dir = str(self.config.library_dir)
        aliases = load_aliases(self.config)
        changed = 0

        for book in books.values():
            cover = book.get('cover_url', '')
            if cover and not cover.startswith('http') and os.path.isabs(cover):
                try:
                    book['cover_url'] = str(Path(cover).relative_to(library_dir))
                except ValueError:
                    # Points outside the library entirely; drop it and refetch later.
                    book['cover_url'] = ''
                changed += 1

            for field in ('title', 'language', 'series'):
                value = book.get(field)
                if isinstance(value, str):
                    cleaned = normalize_space(value)
                    if cleaned != value:
                        book[field] = cleaned
                        changed += 1

            author = book.get('author')
            if isinstance(author, str) and author:
                canonical = canonical_author(author, aliases)
                if canonical and canonical != author:
                    book['author'] = canonical
                    changed += 1

        return changed

    def save(self) -> None:
        """Atomically write the catalogue to disk."""
        path = self.config.books_file
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(self.books, f, indent=2, ensure_ascii=False)
                    os.replace(tmp_name, path)
                except BaseException:
                    Path(tmp_name).unlink(missing_ok=True)
                    raise
            except OSError as e:
                logger.error(f"{Colors.RED}Could not save catalogue: {e}{Colors.ENDC}")

    # ------------------------------------------------------------ dict access

    def __len__(self) -> int:
        return len(self.books)

    def __contains__(self, file_hash: str) -> bool:
        return file_hash in self.books

    def __getitem__(self, file_hash: str) -> Book:
        return self.books[file_hash]

    def __setitem__(self, file_hash: str, book: Book) -> None:
        self.books[file_hash] = book

    def items(self) -> Iterator[Tuple[str, Book]]:
        return iter(self.books.items())

    def values(self) -> Iterator[Book]:
        return iter(self.books.values())

    def clear(self) -> None:
        self.books = {}

    # --------------------------------------------------------------- queries

    def find_duplicate(
        self, title: str, author: str, exclude_hash: str = "", exclude_path: str = ""
    ) -> str:
        """Hash of a *different* file holding the same book, or ''.

        ``exclude_path`` matters when a book is rescanned after being edited:
        the edit changes its hash, so its own previous entry is still present
        under the old key and would otherwise look like a duplicate of itself —
        causing the freshly read record to be thrown away.
        """
        key = f"{title.lower()}|{author.lower()}"
        for file_hash, book in self.books.items():
            if file_hash == exclude_hash:
                continue
            if exclude_path and book.get('path') == exclude_path:
                continue
            if not (book.get('title') and book.get('author')):
                continue
            if f"{book['title'].lower()}|{book['author'].lower()}" == key:
                return file_hash
        return ""

    def needing_subjects(self) -> list:
        """Books with a title and author but no API subjects yet."""
        return [
            (h, b) for h, b in self.books.items()
            if b.get('title') and b.get('author') and not b.get('api_subjects')
        ]

    def _cover_present(self, book: Book) -> bool:
        """Whether the book's cover image is actually on disk.

        Counting the field alone reports full coverage even when a file has been
        renamed or removed underneath the catalogue.
        """
        cover = book.get('cover_url')
        if not cover:
            return False
        if cover.startswith('http'):
            return True
        return (self.config.library_dir / cover).exists()

    def stats(self) -> Dict[str, int]:
        """Coverage counts used for the end-of-run summary."""
        total = len(self.books)
        return {
            'total': total,
            'categorized': sum(1 for b in self.books.values() if b.get('categories')),
            'with_description': sum(1 for b in self.books.values() if b.get('description')),
            'with_cover': sum(1 for b in self.books.values() if self._cover_present(b)),
            'with_subjects': sum(1 for b in self.books.values() if b.get('api_subjects')),
            'with_series': sum(1 for b in self.books.values() if b.get('series')),
        }
