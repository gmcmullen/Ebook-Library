"""Canonical author names.

Two different problems are handled here. Formatting differences ("Corey, James
S.A." vs "James S. A. Corey") are mechanical and fixed by `normalize_author`.
Genuinely different names for the same person — a pen name, a transliteration,
an anglicisation — cannot be inferred, so they come from an alias file the user
controls.
"""
import json
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

from .config import Config
from .logging_setup import Colors, get_logger
from .text import normalize_author, normalize_space

logger = get_logger()

ALIAS_FILENAME = "author_aliases.json"

# Seeded so the file is self-explanatory when the user first opens it.
DEFAULT_ALIASES = {
    "adrian czajkowski": "Adrian Tchaikovsky",
}


def alias_path(config: Config) -> Path:
    return config.library_dir / ALIAS_FILENAME


def load_aliases(config: Config) -> Dict[str, str]:
    """Load the alias map, creating it with defaults on first run.

    Keys are matched case-insensitively against the normalised author name.
    """
    path = alias_path(config)
    if not path.exists():
        try:
            config.library_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(DEFAULT_ALIASES, indent=2, ensure_ascii=False) + "\n",
                encoding='utf-8',
            )
            logger.info(f"Created {path} — edit it to merge author names by hand")
        except OSError as e:
            logger.debug(f"Could not create {path}: {e}")
        return dict(DEFAULT_ALIASES)

    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"{Colors.YELLOW}Ignoring {path}: {e}{Colors.ENDC}")
        return {}

    if not isinstance(raw, dict):
        logger.warning(f"{Colors.YELLOW}{path} should be a JSON object; ignoring{Colors.ENDC}")
        return {}

    return {
        normalize_space(str(k)).lower(): normalize_space(str(v))
        for k, v in raw.items() if k and v
    }


def canonical_author(author: str, aliases: Dict[str, str]) -> str:
    """Normalise an author's name, then apply any alias the user has configured."""
    normalized = normalize_author(author)
    if not normalized:
        return ""
    return aliases.get(normalized.lower(), normalized)


def author_from_path(path: str) -> str:
    """The author encoded in an ``Author - Title.ext`` filename, normalised."""
    if not path or ' - ' not in path:
        return ""
    return normalize_author(Path(path).name.split(' - ')[0])


def _comparison_key(name: str) -> str:
    return ''.join(ch for ch in name.lower() if ch.isalnum())


def find_mismatches(books) -> List[Tuple[str, str, str]]:
    """Books whose stored author disagrees with the filename.

    Returns ``(title, stored_author, filename_author)``. These are reported
    rather than corrected: sometimes the embedded metadata is right and the
    filename has a typo, and sometimes the reverse. Only a human can say which.
    """
    mismatches = []
    for book in books:
        stored = book.get('author', '')
        from_file = author_from_path(book.get('path', ''))
        if not (stored and from_file):
            continue
        if _comparison_key(stored) != _comparison_key(from_file):
            mismatches.append((book.get('title', '?'), stored, from_file))
    return sorted(mismatches, key=lambda m: m[1].lower())


# --------------------------------------------------------------------- renames

MULTI_AUTHOR_MARKERS = (' and ', ' & ', ',', ';', '(')


def _strip_diacritics(name: str) -> str:
    decomposed = unicodedata.normalize('NFD', name)
    return ''.join(c for c in decomposed if not unicodedata.combining(c))


def _words(name: str) -> set:
    return {w.strip('.').lower() for w in name.split() if w.strip('.')}


def rename_decision(meta_author: str, file_author: str) -> Tuple[bool, str]:
    """Whether a filename's author should be rewritten to the metadata's version.

    Renaming is only safe when the two are the same name spelled differently.
    A filename that names more people, or carries a middle initial the metadata
    lacks, holds information the metadata does not — rewriting it would lose
    something, so those are reported instead.
    """
    import difflib

    if not (meta_author and file_author):
        return False, "one side is empty"

    if _comparison_key(meta_author) == _comparison_key(file_author):
        return False, "already identical"

    lowered = f" {file_author.lower()} "
    if any(marker in lowered for marker in MULTI_AUTHOR_MARKERS):
        return False, "filename lists several authors"
    if any(marker in f" {meta_author.lower()} " for marker in MULTI_AUTHOR_MARKERS):
        return False, "metadata lists several authors"

    # Never trade an accented name for an unaccented one: "Óscar Martínez" in a
    # filename is correct even when the metadata has stripped the diacritics.
    if _strip_diacritics(meta_author).lower() == _strip_diacritics(file_author).lower():
        return False, "differs only in diacritics; keeping the filename"

    meta_words, file_words = _words(meta_author), _words(file_author)

    # The filename knows more than the metadata (a middle initial, a fuller
    # forename): keep the filename.
    if meta_words < file_words:
        return False, "filename is more complete than the metadata"

    # Wildly different lengths mean these are not the same string misspelled.
    if abs(len(file_author.split()) - len(meta_author.split())) > 1:
        return False, "names differ too much to be a spelling variant"

    meta_surname = meta_author.split()[-1].lower()
    file_surname = file_author.split()[-1].lower()
    ratio = difflib.SequenceMatcher(None, file_author.lower(), meta_author.lower()).ratio()

    surname_ratio = difflib.SequenceMatcher(None, file_surname, meta_surname).ratio()
    if surname_ratio < 0.7:
        return False, f"different surname ({file_surname} vs {meta_surname})"
    if ratio < 0.75:
        return False, f"too dissimilar (ratio {ratio:.2f})"

    return True, f"spelling variant (ratio {ratio:.2f})"
