# Changes

## Writing metadata back to the files — September 5, 2026

### Added

- `--write-metadata` (with `--dry-run`) writes canonical author names into EPUB
  `dc:creator` and corrects misspelled filenames. Archives are rebuilt with
  `mimetype` first and uncompressed, verified by reopening and re-parsing, and
  swapped in atomically; a file that fails verification is left untouched.
  Rewriting changes a file's SHA-256, so catalogue entries are re-keyed.
- `opf:file-as` is kept in step with the display name when one is present.

### Notes

- An author is only written when the new value derives from that file's own
  `dc:creator`, so a name is never imported from another source.
- Renaming is refused when the filename holds information the metadata does not:
  more authors, a middle initial, or accents the metadata has stripped.

## Categorisation and author-name fixes — September 5, 2026

### Fixed

- **A generic keyword matched inside a specific phrase.** "POLITICAL SCIENCE"
  registered as both Politics & Society *and* Science & Nature, so Bob Woodward's
  *Fear* was filed under science. Sub-genre matching now consumes longer phrases
  first, and every occurrence of a phrase, not just the first.
- **The free-text fallback invented categories.** A blurb mentioning
  "autobiography" made *Three Eight One* — a novel — Non-Fiction with
  Biography & Memoir, Technology & Computers and Travel attached. Free text now
  yields a primary category only, requires an explicit marker or two independent
  signals, and never produces sub-genres.
- **Books that already had subjects kept stale categories.** `--fetch-metadata`
  skips them by design, so the earlier nonfiction fix never reached them;
  `--recat` recomputes every book offline and now does.
- **Author names had several spellings.** `Corey, James S.A.`, `Corey, James S. A.;`
  and `James S.A. Corey` were three different authors in the catalogue. Names are
  normalised to natural order with regular initials; multi-author strings are
  detected and left intact.
- **macOS filenames compare unequal to their own metadata.** Filenames are stored
  decomposed (NFD), so "André" from a filename never matched "André" from the
  OPF. All text is normalised to NFC.

### Added

- `Library/author_aliases.json`, a user-edited map for names no program can
  reconcile — pen names, transliterations, filename typos. Seeded with
  Adrian Czajkowski → Adrian Tchaikovsky.
- `--fix-authors`, which applies the alias file and lists every book whose stored
  author disagrees with its filename for review.
- A title-only lookup as a last resort, guarded by a surname check so it cannot
  attach the wrong book's metadata.

---

## Restructure and metadata fixes — September 5, 2026

### Fixed

- **Non-fiction was classified as Fiction.** `'fiction' in 'nonfiction'` is true,
  so any book whose subjects included "Nonfiction" tripped both the fiction and
  non-fiction tests and fell through to a branch that returned `Fiction`.
  Markers now match on word boundaries.
- **`--recat` could never backfill.** It took the offline fast path whenever
  *any* book had stored subjects. With 33 of 397 qualifying, every run reported
  "Recategorized 0 books" and the other 364 stayed uncategorised. `--recat` is
  now offline-only and says how many books still need a lookup;
  `--fetch-metadata` does the backfill.
- **Google Books rate limiting starved the catalogue.** The old circuit breaker
  disabled a host for the whole run after two retries, so once Google returned
  429 nothing else was tried. Rate limiting is now per host with escalating
  cooldowns, `Retry-After` is honoured, and OpenLibrary — whose subject lists
  are richer anyway — carries the load.
- **OPF whitespace leaked into names.** Author and title values kept the XML's
  newlines and indentation, so those books sorted ahead of everything else in
  the catalogue. Values are normalised on extraction, and existing records are
  migrated on load.
- **`lxml` was missing from requirements.** `BeautifulSoup(..., 'xml')` needs it;
  without it, OPF parsing failed silently inside a broad `except` and series
  metadata was lost.
- **HTML output was unescaped.** Titles and descriptions were concatenated
  straight into markup and attributes. The page now renders from embedded JSON
  through the DOM, so escaping is structural.
- **Cover paths were absolute.** `books.json` stored machine-specific paths;
  they are now relative to the Library directory, and old entries are migrated.
- Catalogue writes are atomic, so an interrupted save cannot truncate
  `books.json`.

### Changed

- Split the 2,232-line `libraryscan.py` into the `ebooklibrary` package.
  `libraryscan.py` remains as the entry point.
- Replaced the `hasattr(self, 'force_scan')` flag pattern with explicit
  parameters and a `Config` object.
- Rewrote the catalogue page: client-side search, grouping by author, title,
  series or category, category filtering including an "uncategorised" view,
  clickable links to open the book files, lazy-loaded covers, coverage stats,
  and a light theme that follows the system setting.
- Rate limiting is per host, so a throttled API no longer stalls the others.
- `--update` compares path and mtime instead of hashing every file.
- Long operations checkpoint every 25 books and resume after an interrupt.

### Added

- `--fetch-metadata` / `--refetch-metadata` to backfill subjects and descriptions
  without rescanning files or refetching covers.
- `--stats` for catalogue coverage, and an end-of-run coverage summary.
- `--offline` and `--workers N`.
- `GOOGLE_BOOKS_API_KEY` support, which lifts the quota that causes most
  missing-category problems.
- Tests for the categorisation and text-parsing rules.

### Removed

- `torch`, `transformers`, `accelerate`, `sentencepiece`, `protobuf` and `numpy`
  from requirements. They were left over from the BART classifier removed in
  December 2025 and nothing imported them.
- `PyPDF2`, replaced by its maintained successor `pypdf`.

---

# Code Cleanup - December 29, 2025

## Changes Made

### Removed Dead Code
- Removed unused `_get_book_categories()` method (never called, relied on non-existent tokenizer/model)
- Removed unused `_update_book_metadata()` method (never called)
- Removed unused `scan_library()` method (never called)

<!-- NOTE: the remainder of this December 2025 entry was lost when this file was
     rewritten on 2026-09-05. Restore it from Dropbox version history. -->
