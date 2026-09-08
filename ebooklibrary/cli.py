"""Command-line entry point."""
import argparse
import sys
from pathlib import Path

from .config import Config
from .logging_setup import Colors, setup_logging
from .scanner import BookScanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='libraryscan',
        description='Scan, catalogue and publish an ebook collection.',
        epilog=(
            'Set GOOGLE_BOOKS_API_KEY to raise the Google Books quota; without one, '
            'large libraries get rate limited and many books end up uncategorised.'
        ),
    )
    parser.add_argument('directory', help='Directory containing your ebooks')

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--update', '-u', action='store_true',
                      help='Scan only new or modified files (default if a catalogue exists)')
    mode.add_argument('--full', '-f', action='store_true',
                      help='Discard the catalogue and rescan everything, covers included')
    mode.add_argument('--fetch-metadata', action='store_true',
                      help='Look up subjects and descriptions for books missing them')
    mode.add_argument('--refetch-metadata', action='store_true',
                      help='Re-look-up metadata for every book, even those already complete')
    mode.add_argument('--recat', '-r', action='store_true',
                      help='Recompute categories offline from stored subjects')
    mode.add_argument('--covers-only', action='store_true',
                      help='Refetch covers for every book')
    mode.add_argument('--missing-covers-only', action='store_true',
                      help='Fetch covers only for books that lack one')
    mode.add_argument('--html-only', action='store_true',
                      help='Regenerate books.html from the existing catalogue')
    mode.add_argument('--write-metadata', action='store_true',
                      help='Write canonical author names into the EPUB files and '
                           'fix misspelled filenames (modifies your books)')
    mode.add_argument('--fix-authors', action='store_true',
                      help='Canonicalise author names and report ones needing review')
    mode.add_argument('--prune-covers', action='store_true',
                      help='Delete cover images no book refers to any more')
    mode.add_argument('--stats', action='store_true',
                      help='Report catalogue coverage and exit')

    parser.add_argument('--dry-run', action='store_true',
                        help='With --write-metadata or --prune-covers, show changes without making them')
    parser.add_argument('--offline', action='store_true',
                        help='Skip all network lookups')
    parser.add_argument('--workers', type=int, default=None,
                        help='Number of parallel workers (default: 2x CPU cores, max 32)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logging(verbose=args.verbose)

    directory = Path(args.directory).expanduser()
    if not directory.is_dir():
        logger.error(f"{Colors.RED}{args.directory} is not a directory{Colors.ENDC}")
        return 1

    config = Config(base_dir=directory)
    if args.workers:
        config.num_workers = max(1, args.workers)

    scanner = BookScanner(config, offline=args.offline)

    try:
        if args.stats:
            scanner.log_summary()
            return 0

        if args.html_only:
            scanner.write_html()
        elif args.full:
            scanner.scan(force=True)
            scanner.update_covers()
        elif args.fetch_metadata:
            scanner.fetch_metadata(only_missing=True)
        elif args.refetch_metadata:
            scanner.fetch_metadata(only_missing=False)
        elif args.recat:
            scanner.recategorize()
        elif args.write_metadata:
            scanner.write_metadata(dry_run=args.dry_run)
        elif args.fix_authors:
            scanner.fix_authors()
        elif args.prune_covers:
            scanner.prune_covers(dry_run=args.dry_run)
        elif args.covers_only:
            scanner.update_covers(missing_only=False)
        elif args.missing_covers_only:
            scanner.update_covers(missing_only=True)
        elif args.update or len(scanner.catalog):
            # An existing catalogue means an incremental update is what's wanted.
            scanner.update()
        else:
            scanner.scan()

        scanner.log_summary()
        return 0

    except KeyboardInterrupt:
        logger.warning(f"{Colors.YELLOW}Interrupted — saving progress{Colors.ENDC}")
        scanner.catalog.save()
        return 130


if __name__ == '__main__':
    sys.exit(main())
