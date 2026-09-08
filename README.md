# Ebook Library

Scans a directory of ebooks, enriches each one with metadata and cover art from
public APIs, sorts them into bookstore-style categories, and publishes a
searchable HTML catalogue.

**This code was written with AI assistance and should be audited before you rely
on it for anything important.**

## Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python3 libraryscan.py /path/to/your/books
```

With no flags, the scanner does an incremental update when a catalogue already
exists and a full scan when it doesn't. Books must sit directly in the given
directory; subdirectories are not scanned.

Output lands in `<your-books>/Library/`:

| File | Contents |
| --- | --- |
| `books.json` | The catalogue, keyed by SHA-256 of each file |
| `books.html` | The browsable catalogue |
| `covers/` | Downloaded and extracted cover images |

### Commands

| Flag | Effect |
| --- | --- |
| *(none)* | Incremental update, or a full scan if there is no catalogue |
| `--update`, `-u` | Scan only new or modified files |
| `--full`, `-f` | Discard the catalogue and rescan everything, covers included |
| `--fetch-metadata` | Look up subjects and descriptions for books missing them |
| `--refetch-metadata` | Re-look-up metadata for every book |
| `--recat`, `-r` | Recompute categories offline from stored subjects |
| `--fix-authors` | Canonicalise author names and report ones needing review |
| `--write-metadata` | Write canonical authors into the EPUBs and fix misspelled filenames |
| `--dry-run` | With `--write-metadata`, show every change without making it |
| `--covers-only` | Refetch covers for every book |
| `--missing-covers-only` | Fetch covers only for books that lack one |
| `--html-only` | Regenerate `books.html` from the existing catalogue |
| `--stats` | Report catalogue coverage and exit |
| `--offline` | Skip all network lookups |
| `--workers N` | Parallel workers (default: 2× CPU cores, capped at 32) |
| `--verbose`, `-v` | Debug logging |

Long operations checkpoint every 25 books, so an interrupted run resumes where
it stopped rather than starting over.

### Google Books quota

Google Books rate-limits unauthenticated traffic aggressively — enough that a
few hundred books will exhaust it and leave most of them without subjects.
Setting an API key avoids that:

```bash
export GOOGLE_BOOKS_API_KEY=your-key-here
```

Without a key the scanner still works: it falls back to OpenLibrary, which
supplies richer subject lists anyway, and to Wikipedia for descriptions.

## How categorisation works

Each book gets a primary category (`Fiction` or `Non-Fiction`) plus up to three
sub-genres, derived from the subject strings the APIs return. Those raw subjects
are stored in the catalogue as `api_subjects`, so `--recat` can re-derive every
category offline after a rule change — no network, no rescan.

Sub-genre matching consumes longer phrases first, so a specific term is not also
counted as a generic one — "political science" belongs to Politics & Society and
does not additionally register as "science" for Science & Nature.

When no subjects are available at all, the scanner falls back to the title and
description, but only assigns a primary category and never a sub-genre. Prose is
not a subject heading: a novel whose blurb asks whether a text is "autobiography,
fantasy or fraud" is not a memoir. An explicit marker ("A Novel", "A Memoir") or
two independent signals are required, and anything less is left uncategorised
rather than guessed at.

There is no machine-learning classifier. An earlier version used BART zero-shot
classification; it scored roughly 0.95 for every candidate category regardless
of the book, so it was removed.

## Author names

Author names arrive in several shapes, and two different problems come out of that.

Formatting differences are mechanical and fixed automatically: `Corey, James S.A.`
and `James S. A. Corey` both become `James S. A. Corey`, and `Ursula K Le Guin`
becomes `Ursula K. Le Guin`. Inverted names are flipped back to natural order,
but only when the comma really marks an inversion — `Julian Assange, Jacob
Appelbaum, Andy Muller-Maguhn` is a list of people and is left alone.

Genuinely different names for the same person cannot be inferred. A pen name, a
transliteration, an anglicisation, or a typo in a filename all look identical to
a program. These come from `Library/author_aliases.json`, which you edit:

```json
{
  "adrian czajkowski": "Adrian Tchaikovsky"
}
```

Keys are matched case-insensitively against the normalised name. Run
`--fix-authors` to apply the file and list every book whose stored author
disagrees with its filename, so you can decide which spelling is right — the
embedded metadata is often correct and the filename is the one with the typo.

### Writing changes back to the files

`--write-metadata` pushes the canonical name into each EPUB's `dc:creator` and
renames files whose author is misspelled. Always preview it with `--dry-run`
first; it modifies your books.

Two rules keep it from destroying information. An author is only written when
the new value derives from that file's own `dc:creator` — a formatting fix or a
configured alias — so a name is never imported from some other source. And a
filename is only rewritten when it is the same name misspelled: one that names
more people, carries a middle initial the metadata lacks, or differs only in
accents is reported and left alone.

Rewriting an EPUB changes its SHA-256, which is the catalogue key, so entries
are re-keyed automatically. Each rebuilt archive keeps `mimetype` first and
uncompressed as the spec requires, and is reopened and re-parsed before it
replaces the original — a file that fails verification is left untouched.

## Metadata sources

Metadata is merged from Google Books, then OpenLibrary, then Wikipedia, and
anything the ebook file itself provides always wins over an API.

Covers are resolved in cost order: an existing local file, the cover embedded in
the EPUB, OpenLibrary by ISBN, Google Books, OpenLibrary search, then a
Wikipedia page image. Downloads under 10 KB and known "image not available"
placeholders are rejected.

## Layout

```
libraryscan.py          Entry point
ebooklibrary/
  cli.py                Argument parsing and command dispatch
  config.py             Paths, concurrency, rate limits
  scanner.py            Orchestration
  catalog.py            books.json load, migrate, atomic save
  http_client.py        Per-host rate limiting, backoff, cooldown
  providers.py          Google Books / OpenLibrary / Wikipedia
  metadata.py           Merging provider results
  covers.py             Cover resolution and caching
  categorize.py         Subject-to-category rules (pure)
  extract.py            Hashing, EPUB/PDF/text parsing, embedded covers
  text.py               Description cleaning, series parsing
  html_report.py        Catalogue page generation
  logging_setup.py      Console and file logging
tests/                  Tests for the pure logic
```

## Tests

```bash
python3 -m pytest tests/ -q
```
