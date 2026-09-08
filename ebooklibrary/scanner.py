"""Scan a directory of ebooks and keep the catalogue up to date."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from .authors import (author_from_path, canonical_author, find_mismatches,
                      load_aliases, rename_decision)
from .catalog import Catalog
from .config import SUPPORTED_EXTENSIONS, Config
from .covers import CoverFetcher
from .extract import calculate_file_hash, extract_epub_metadata, metadata_from_filename
from .epub_writer import read_creator, set_author
from .html_report import generate_html
from .http_client import RateLimitedSession
from .logging_setup import Colors, get_logger
from .metadata import MetadataService

logger = get_logger()

PROGRESS_FORMAT = '{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'


class BookScanner:
    """Coordinates extraction, lookup, covers and HTML generation."""

    def __init__(self, config: Config, offline: bool = False):
        self.config = config
        self.offline = offline
        config.ensure_dirs()

        self.catalog = Catalog(config)
        self.aliases = load_aliases(config)
        # Paths whose cover must be re-read rather than taken from the cache.
        self._refresh_covers: set = set()
        # Files that produced no catalogue entry this run.
        self._skipped: List[Path] = []
        self.http = RateLimitedSession(config)
        self.metadata = MetadataService(self.http, config)
        self.covers = CoverFetcher(self.http, config)

    # ------------------------------------------------------------- discovery

    def _library_files(self) -> List[Path]:
        """Supported ebook files sitting directly in the library directory."""
        return sorted(
            p for p in self.config.base_dir.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def _file_record(self, file_path: Path) -> Dict:
        stat = file_path.stat()
        return {
            'path': str(file_path.relative_to(self.config.base_dir)),
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'type': file_path.suffix.lower()[1:],
        }

    # -------------------------------------------------------------- per-file

    def process_file(self, file_path: Path, fetch_online: bool = True) -> Optional[Tuple[str, Dict]]:
        """Build a catalogue entry for one file. Returns None if it should be skipped."""
        try:
            file_hash = calculate_file_hash(file_path)
            if not file_hash:
                return None

            book = self._file_record(file_path)

            if file_path.suffix.lower() == '.epub':
                book.update(extract_epub_metadata(file_path))

            # Non-EPUB formats carry no embedded metadata we can read, so the
            # filename is the only source of a title and author.
            for key, value in metadata_from_filename(file_path).items():
                if not book.get(key):
                    book[key] = value

            if book.get('author'):
                book['author'] = canonical_author(book['author'], self.aliases)

            if not (book.get('title') and book.get('author')):
                # Nothing to look up, but the file still belongs in the catalogue.
                return file_hash, book

            if fetch_online and not self.offline:
                book.update(self.metadata.lookup(book['title'], book['author'], existing=book))

            # A file that changed on disk may carry a new embedded cover, so the
            # cached one is not reused for it.
            refresh = str(file_path.relative_to(self.config.base_dir)) in self._refresh_covers
            cover = self.covers.fetch(book, force=refresh)
            if cover:
                book['cover_url'] = cover

            book['categories'] = self.metadata.categorize(book)

            # Two files of the same book: keep whichever record is more complete.
            duplicate = self.catalog.find_duplicate(
                book['title'], book['author'],
                exclude_hash=file_hash, exclude_path=book.get('path', ''),
            )
            if duplicate:
                existing = self.catalog[duplicate]
                if len(book) <= len(existing):
                    logger.debug(f"Duplicate of '{book['title']}' — keeping existing entry")
                    return None
                logger.debug(f"Duplicate of '{book['title']}' — replacing with fuller record")
                return duplicate, book

            return file_hash, book

        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            return None

    def _store(self, file_hash: str, book: Dict) -> None:
        """Add a book, dropping any previous entry for the same file.

        Editing a book's metadata changes its content hash but not its path, and
        the hash is the catalogue key — so without this an edited file would be
        added again alongside its stale entry.
        """
        path = book.get('path')
        if path:
            stale = [
                h for h, existing in self.catalog.items()
                if h != file_hash and existing.get('path') == path
            ]
            for h in stale:
                del self.catalog.books[h]
        self.catalog[file_hash] = book

    def _process_batch(self, files: List[Path], description: str) -> int:
        """Process files across the worker pool, checkpointing as it goes."""
        processed = 0
        with tqdm(total=len(files), desc=description, unit="book", bar_format=PROGRESS_FORMAT) as bar:
            with ThreadPoolExecutor(max_workers=self.config.num_workers) as pool:
                for start in range(0, len(files), self.config.batch_size):
                    batch = files[start:start + self.config.batch_size]
                    # Paired with their paths so a file that yields nothing can be named.
                    futures = [(f, pool.submit(self.process_file, f)) for f in batch]
                    for path, future in futures:
                        result = future.result()
                        if result:
                            file_hash, book = result
                            self._store(file_hash, book)
                            processed += 1
                        else:
                            self._skipped.append(path)
                        bar.update(1)
                    self.catalog.save()
        return processed

    # -------------------------------------------------------------- commands

    def scan(self, force: bool = False) -> None:
        """Full scan of the library directory."""
        logger.info(f"{Colors.BLUE}Scanning {self.config.base_dir}{Colors.ENDC}")

        if force:
            logger.info(f"{Colors.YELLOW}--full: discarding the existing catalogue{Colors.ENDC}")
            self.catalog.clear()
            self.catalog.save()

        files = self._library_files()
        logger.info(f"Found {len(files)} book files")
        if not files:
            logger.warning(f"{Colors.YELLOW}No supported ebooks in {self.config.base_dir}{Colors.ENDC}")
            return

        self._process_batch(files, "Scanning")
        self.catalog.save()
        logger.info(f"{Colors.GREEN}Scan complete — {len(self.catalog)} books catalogued{Colors.ENDC}")
        self.report_uncatalogued()
        self.write_html()

    def update(self) -> None:
        """Scan only files that are new or have changed since the last run."""
        logger.info(f"{Colors.BLUE}Checking {self.config.base_dir} for changes{Colors.ENDC}")

        known_paths = {b.get('path'): b for b in self.catalog.values()}
        files = []
        for file_path in self._library_files():
            relative = str(file_path.relative_to(self.config.base_dir))
            existing = known_paths.get(relative)
            modified = datetime.fromtimestamp(
                file_path.stat().st_mtime
            ).strftime('%Y-%m-%d %H:%M:%S')

            # Compare path and mtime first; hashing every file defeats the point
            # of an incremental update on a large library.
            if existing is None or existing.get('modified') != modified:
                files.append(file_path)
                if existing is not None:
                    # Known file, changed on disk: its cover may have changed too.
                    self._refresh_covers.add(relative)

        removed = self._prune_missing()

        if not files:
            if not removed:
                logger.info(f"{Colors.YELLOW}No new or modified books{Colors.ENDC}")
            self.catalog.save()
            self.report_uncatalogued()
            self.write_html()
            return

        logger.info(f"Found {len(files)} new or modified files")
        self._process_batch(files, "Updating")
        self.catalog.save()
        logger.info(f"{Colors.GREEN}Update complete{Colors.ENDC}")
        self.report_uncatalogued()
        self.write_html()

    def _prune_missing(self) -> int:
        """Drop entries whose file is gone, so renames and deletions don't linger."""
        gone = [
            file_hash for file_hash, book in self.catalog.items()
            if book.get('path') and not (self.config.base_dir / book['path']).exists()
        ]
        for file_hash in gone:
            logger.debug(f"Removing missing file: {self.catalog[file_hash].get('path')}")
            del self.catalog.books[file_hash]
        if gone:
            logger.info(f"Removed {len(gone)} books whose files no longer exist")
        return len(gone)

    def fetch_metadata(self, only_missing: bool = True) -> None:
        """Backfill subjects and descriptions for books already in the catalogue.

        Never rescans files or touches covers. Progress is checkpointed, so an
        interrupted run resumes where it stopped.
        """
        if only_missing:
            targets = self.catalog.needing_subjects()
            skipped = len(self.catalog) - len(targets)
            logger.info(
                f"{Colors.BLUE}Fetching metadata for {len(targets)} books "
                f"({skipped} already have subjects){Colors.ENDC}"
            )
        else:
            targets = [
                (h, b) for h, b in self.catalog.items()
                if b.get('title') and b.get('author')
            ]
            logger.info(f"{Colors.BLUE}Refreshing metadata for {len(targets)} books{Colors.ENDC}")

        if not targets:
            logger.info(f"{Colors.GREEN}Nothing to fetch{Colors.ENDC}")
            return

        updated = 0
        with tqdm(total=len(targets), desc="Fetching", unit="book", bar_format=PROGRESS_FORMAT) as bar:
            for i, (_, book) in enumerate(targets, start=1):
                try:
                    found = self.metadata.lookup(book['title'], book['author'], existing=book)

                    changed = False
                    if found.get('api_subjects'):
                        book['api_subjects'] = found['api_subjects']
                        changed = True
                    for field in ('description', 'publisher', 'published_date',
                                  'page_count', 'isbn', 'series'):
                        if not book.get(field) and found.get(field):
                            book[field] = found[field]
                            changed = True

                    categories = self.metadata.categorize(book)
                    if categories != book.get('categories'):
                        book['categories'] = categories
                        changed = True

                    if changed:
                        updated += 1
                except Exception as e:
                    logger.debug(f"Lookup failed for '{book.get('title')}': {e}")

                if i % self.config.save_interval == 0:
                    self.catalog.save()
                bar.update(1)

        self.catalog.save()
        logger.info(f"{Colors.GREEN}Updated {updated} books{Colors.ENDC}")
        self.write_html()

    def recategorize(self) -> None:
        """Recompute categories offline from stored subjects."""
        updated = 0
        for book in self.catalog.values():
            categories = self.metadata.categorize(book)
            if categories != book.get('categories'):
                book['categories'] = categories
                updated += 1

        self.catalog.save()
        logger.info(f"{Colors.GREEN}Recategorised {updated} books{Colors.ENDC}")

        missing = len(self.catalog.needing_subjects())
        if missing:
            logger.warning(
                f"{Colors.YELLOW}{missing} books have no API subjects — "
                f"run --fetch-metadata to look them up{Colors.ENDC}"
            )
        self.write_html()

    def update_covers(self, missing_only: bool = False) -> None:
        """Refresh covers, preferring the artwork embedded in each book.

        Because the embedded cover now outranks the cache, this re-reads every
        EPUB's own image and only goes to the network for books that have none.
        """
        targets = []
        for _, book in self.catalog.items():
            if not (book.get('title') and book.get('author')):
                continue
            if missing_only and self._has_local_cover(book):
                continue
            targets.append(book)

        skipped = len(self.catalog) - len(targets)
        logger.info(
            f"{Colors.BLUE}Fetching covers for {len(targets)} books "
            f"({skipped} skipped){Colors.ENDC}"
        )

        found = 0
        with tqdm(total=len(targets), desc="Covers", unit="book", bar_format=PROGRESS_FORMAT) as bar:
            for i, book in enumerate(targets, start=1):
                # No need to force: the embedded cover is consulted first now,
                # so a refresh picks up new artwork without re-hitting the APIs
                # for books that already have a cached image.
                cover = self.covers.fetch(book)
                if cover:
                    book['cover_url'] = cover
                    found += 1
                else:
                    logger.debug(f"No cover for '{book.get('title')}' by {book.get('author')}")

                if i % self.config.save_interval == 0:
                    self.catalog.save()
                bar.update(1)

        self.catalog.save()
        logger.info(f"{Colors.GREEN}Found covers for {found} of {len(targets)} books{Colors.ENDC}")
        self.write_html()

    def _has_local_cover(self, book: Dict) -> bool:
        cover = book.get('cover_url', '')
        if not cover or cover.startswith('http'):
            return False
        return (self.config.library_dir / cover).exists()

    def fix_authors(self) -> None:
        """Re-canonicalise every author, then report names that need a human.

        Formatting is corrected automatically. Where the stored author and the
        filename disagree on the actual name, both spellings are printed so the
        user can add the right one to author_aliases.json — the metadata is
        sometimes right and the filename sometimes is, and nothing in the data
        says which.
        """
        self.aliases = load_aliases(self.config)
        changed = 0
        for book in self.catalog.values():
            author = book.get('author')
            if not author:
                continue
            canonical = canonical_author(author, self.aliases)
            if canonical and canonical != author:
                logger.debug(f"{author!r} -> {canonical!r}")
                book['author'] = canonical
                changed += 1

        self.catalog.save()
        logger.info(f"{Colors.GREEN}Canonicalised {changed} author names{Colors.ENDC}")

        mismatches = find_mismatches(self.catalog.values())
        if mismatches:
            logger.info(
                f"{Colors.YELLOW}{len(mismatches)} books where the stored author "
                f"differs from the filename — add the correct spelling to "
                f"{self.config.library_dir / 'author_aliases.json'} if needed:{Colors.ENDC}"
            )
            for title, stored, from_file in mismatches:
                logger.info(f"    metadata={stored!r}  filename={from_file!r}  ({title})")

        self.write_html()

    def write_metadata(self, dry_run: bool = False) -> None:
        """Write canonical author names into the EPUB files, then fix filenames.

        Only EPUBs are touched, and only where the new name derives from the
        file's own dc:creator (a formatting fix or a configured alias) or where
        the file has no author at all. Renaming is limited to filenames that are
        the same name misspelled; anything carrying extra information is
        reported instead.
        """
        self.aliases = load_aliases(self.config)
        prefix = "[dry run] " if dry_run else ""

        written, rekeyed, skipped = 0, 0, []

        for file_hash, book in list(self.catalog.items()):
            path = self.config.base_dir / book.get('path', '')
            if path.suffix.lower() != '.epub' or not path.exists():
                continue

            current, _ = read_creator(path)
            if current:
                desired = canonical_author(current, self.aliases)
            else:
                # No author in the file: fall back to what the catalogue holds,
                # which came from the filename.
                desired = book.get('author', '')

            if not desired or desired == (current or ''):
                continue

            logger.info(f"{prefix}{path.name}: {current!r} -> {desired!r}")
            if dry_run:
                written += 1
                continue

            if not set_author(path, desired):
                skipped.append(f"{path.name}: write failed")
                continue

            written += 1
            book['author'] = desired
            stat = path.stat()
            book['size'] = stat.st_size
            book['modified'] = datetime.fromtimestamp(stat.st_mtime).strftime(
                '%Y-%m-%d %H:%M:%S'
            )

            # Rewriting the archive changes the file's hash, which is the
            # catalogue key — move the entry rather than leaving a stale one.
            new_hash = calculate_file_hash(path)
            if new_hash and new_hash != file_hash:
                del self.catalog.books[file_hash]
                self.catalog[new_hash] = book
                rekeyed += 1

        if not dry_run:
            self.catalog.save()
        logger.info(
            f"{Colors.GREEN}{prefix}Updated author metadata in {written} EPUBs "
            f"({rekeyed} catalogue keys moved){Colors.ENDC}"
        )

        self._rename_files(dry_run)

        if not dry_run:
            self.catalog.save()
            self.write_html()
        for note in skipped:
            logger.warning(f"{Colors.YELLOW}{note}{Colors.ENDC}")

    def _rename_files(self, dry_run: bool = False) -> None:
        """Rewrite the author part of filenames that misspell it."""
        prefix = "[dry run] " if dry_run else ""
        renamed, declined = 0, []

        for book in list(self.catalog.values()):
            relative = book.get('path', '')
            meta_author = book.get('author', '')
            file_author = author_from_path(relative)
            if not (relative and meta_author and file_author):
                continue

            should, reason = rename_decision(meta_author, file_author)
            if not should:
                if reason not in ('already identical', 'one side is empty'):
                    declined.append((relative, meta_author, file_author, reason))
                continue

            old_path = self.config.base_dir / relative
            if not old_path.exists():
                continue

            # Replace only the author segment; the rest of the name is untouched.
            remainder = old_path.name.split(' - ', 1)[1]
            new_path = old_path.with_name(f"{meta_author} - {remainder}")

            if new_path.exists():
                declined.append((relative, meta_author, file_author, "target name already exists"))
                continue

            logger.info(f"{prefix}rename: {old_path.name!r} -> {new_path.name!r}")
            if dry_run:
                renamed += 1
                continue

            try:
                old_path.rename(new_path)
            except OSError as e:
                declined.append((relative, meta_author, file_author, f"rename failed: {e}"))
                continue

            book['path'] = str(new_path.relative_to(self.config.base_dir))
            renamed += 1

        logger.info(f"{Colors.GREEN}{prefix}Renamed {renamed} files{Colors.ENDC}")

        if declined:
            logger.info(
                f"{Colors.YELLOW}{len(declined)} filenames left alone — "
                f"they hold information the metadata does not:{Colors.ENDC}"
            )
            for relative, meta, from_file, reason in declined:
                logger.info(f"    {from_file!r} vs metadata {meta!r} — {reason}")

    def write_html(self) -> Path:
        return generate_html(self.catalog, self.config)

    def report_uncatalogued(self) -> None:
        """Warn about any book file on disk that is not in the catalogue.

        A file can go missing from the results because it duplicates a book
        already catalogued, because it could not be parsed, or because it was
        moved by another program while the scan was running. Whatever the cause,
        it should never be silent — the catalogue claiming fewer books than the
        folder holds is exactly the sort of thing that goes unnoticed.
        """
        catalogued = {b.get('path') for b in self.catalog.values()}
        on_disk = {
            str(p.relative_to(self.config.base_dir)) for p in self._library_files()
        }
        missing = sorted(on_disk - catalogued)
        if not missing:
            return

        skipped_now = {str(p.relative_to(self.config.base_dir)) for p in self._skipped}

        # A file holding a book that is already catalogued under a different
        # name is a second copy, not something a further scan would pick up.
        catalogued_books = {
            (b.get('title', '').lower(), b.get('author', '').lower()): b.get('path')
            for b in self.catalog.values()
        }

        logger.warning(
            f"{Colors.YELLOW}{len(missing)} book files are not in the catalogue:{Colors.ENDC}"
        )
        for relative in missing:
            metadata = extract_epub_metadata(self.config.base_dir / relative) \
                if relative.lower().endswith('.epub') else {}
            key = (
                metadata.get('title', '').lower(),
                canonical_author(metadata.get('author', ''), self.aliases).lower(),
            )
            other = catalogued_books.get(key)

            if other and other != relative:
                reason = f"duplicate of {other!r} — delete whichever copy you don't want"
            elif relative in skipped_now:
                reason = "skipped this run (duplicate or unreadable)"
            else:
                reason = "appeared after the scan started — re-run --update"
            logger.warning(f"    {relative}  — {reason}")

    def log_summary(self) -> None:
        """Print coverage percentages so gaps in the catalogue are visible."""
        stats = self.catalog.stats()
        total = stats['total'] or 1

        def pct(n: int) -> str:
            return f"{n}/{stats['total']} ({100 * n // total}%)"

        logger.info(f"{Colors.BLUE}Catalogue summary{Colors.ENDC}")
        logger.info(f"  Books:        {stats['total']}")
        logger.info(f"  Categorised:  {pct(stats['categorized'])}")
        logger.info(f"  Descriptions: {pct(stats['with_description'])}")
        logger.info(f"  Covers:       {pct(stats['with_cover'])}")
        logger.info(f"  API subjects: {pct(stats['with_subjects'])}")
        logger.info(f"  In a series:  {pct(stats['with_series'])}")
