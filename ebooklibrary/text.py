"""Text normalisation for descriptions and series titles."""
import re
import unicodedata
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from .logging_setup import get_logger

logger = get_logger()

# A paragraph starting with one of these begins the publisher's boilerplate;
# everything from there on is dropped.
SECTION_CUTOFFS = [
    r'^praise\s+for\b',
    r'^about\s+the\s+author\b',
    r'^about\s+the\s+book\b',
    r'^from\s+the\s+publisher\b',
    r"^publisher'?s\s+note\b",
    r"^editor'?s\s+note\b",
    r'^also\s+by\b',
    r'^other\s+books\s+by\b',
    r'^more\s+from\s+this\s+author\b',
]

# A paragraph matching one of these is pure marketing and is skipped.
PROMOTIONAL_PATTERNS = [
    r'\b(new\s+york\s+times|usa\s+today|wall\s+street\s+journal)\b.{0,60}\bbest[\s-]?seller\b',
    r'\b(pre-?order|buy\s+now|order\s+now|purchase\s+now|available\s+now|coming\s+soon)\b',
    r'\bstarred\s+review\b|\brave\s+reviews\b',
    r'\bkirkus\b|\bpublishers\s+weekly\b|\blibrary\s+journal\b|\bbooklist\b',
    r'^\s*[""]?[A-Z].{5,80}[""]?\s*[-—]\s*\w[\w\s]+,\s*(author|editor|reviewer)\b',
]

MAX_DESCRIPTION_LENGTH = 1500

# Series patterns carrying an explicit position number.
NUMBERED_SERIES_PATTERNS = [
    r'\(([^)]+?)\s+#(\d+(?:\.\d+)?)\)',
    r'\(([^)]+?),\s*[Bb]ook\s+(\d+(?:\.\d+)?)\)',
    r'\(([^)]+?)\s+[Bb]ook\s+(\d+(?:\.\d+)?)\)',
    r'\(([^)]+?),\s*#(\d+(?:\.\d+)?)\)',
    r'\(([^)]+?),\s*[Vv]ol(?:ume|\.)\s+(\d+(?:\.\d+)?)\)',
    r'\(([^)]+?)\s+[Vv]ol(?:ume|\.)\s+(\d+(?:\.\d+)?)\)',
]


def normalize_space(value: str) -> str:
    """Collapse runs of whitespace, strip, and normalise Unicode to NFC.

    EPUB OPF fields routinely carry the XML's own newlines and indentation, which
    otherwise leak into titles and authors and break alphabetical sorting. macOS
    filenames arrive decomposed (NFD), so "Andr\u00e9" read from a filename and
    the same name read from metadata compare unequal until both are composed.
    """
    if not value:
        return ""
    return unicodedata.normalize('NFC', re.sub(r'\s+', ' ', str(value)).strip())


def clean_description(description: str) -> str:
    """Strip HTML and publisher boilerplate, then truncate at a sentence boundary."""
    if not description:
        return ""

    try:
        description = BeautifulSoup(description, 'html.parser').get_text(separator=' ')
        description = description.replace('\r\n', '\n').replace('\r', '\n')
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', description) if p.strip()]

        filtered = []
        for para in paragraphs:
            para_lower = para.lower()
            if any(re.match(pattern, para_lower) for pattern in SECTION_CUTOFFS):
                break
            if any(re.search(pattern, para_lower) for pattern in PROMOTIONAL_PATTERNS):
                continue
            filtered.append(para)

        result = ' '.join(filtered).strip()

        if len(result) > MAX_DESCRIPTION_LENGTH:
            truncated = result[:MAX_DESCRIPTION_LENGTH]
            last_period = truncated.rfind('.')
            if last_period > MAX_DESCRIPTION_LENGTH - 300:
                result = truncated[:last_period + 1]
            else:
                result = truncated.rstrip() + '...'

        return result
    except Exception as e:
        logger.debug(f"Error cleaning description: {e}")
        return description


def parse_series_from_title(title: str) -> Tuple[Optional[str], Optional[float]]:
    """Parse a series name and position out of a title.

    Handles ``Title (Series #1)``, ``Title (Series, Book 1)``, ``Title (Series Vol. 2)``
    and a bare trailing ``Title (Series Name)``.
    """
    if not title:
        return None, None

    for pattern in NUMBERED_SERIES_PATTERNS:
        match = re.search(pattern, title)
        if match:
            series_name = match.group(1).strip()
            try:
                return series_name, float(match.group(2))
            except (ValueError, IndexError):
                return series_name, None

    # Numberless series in trailing parentheses, e.g. "Title (The Expanse)".
    numberless = re.search(r'\(([A-Z][^)]{2,40})\)\s*$', title)
    if numberless:
        candidate = numberless.group(1).strip()
        if not re.fullmatch(r'\d{4}', candidate):  # a bare year is not a series
            return candidate, None

    return None, None


def search_title_variants(title: str) -> List[str]:
    """Title forms to try when querying a metadata API, best-known first.

    Filenames often encode a series in the title — "The Witcher Saga [05] -
    Baptism of Fire", "Laundry Files- Book 02 - Jennifer Morgue" — which no API
    will match. This yields the original plus a stripped-down variant.
    """
    title = normalize_space(title)
    if not title:
        return []

    variants = [title]
    candidate = title

    # Drop trailing bracketed qualifiers: "(Imperial Radch Book 2)", "[05]".
    candidate = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*', ' ', candidate).strip()

    # "Series - Book Title" keeps only the final segment.
    if ' - ' in candidate:
        candidate = candidate.rsplit(' - ', 1)[1].strip()

    # A small leading number is a series index; a large one is probably a year.
    leading = re.match(r'^(\d{1,2})\s+(\S.*)$', candidate)
    if leading and int(leading.group(1)) <= 20:
        candidate = leading.group(2).strip()

    candidate = normalize_space(candidate)
    if candidate and candidate.lower() != title.lower() and len(candidate) > 2:
        variants.append(candidate)

    return variants


# A trailing "Jr", "III" etc. is part of the surname, not a given name.
NAME_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'phd', 'ph.d.', 'md'}


def _normalize_initials(name: str) -> str:
    """Give every single-letter initial a period and a following space.

    Turns "James S.A. Corey" and "Ursula K Le Guin" into the spaced, dotted form
    so the same person does not appear under several spellings.
    """
    # "S.A." -> "S. A."
    name = re.sub(r'\b([A-Z])\.(?=[A-Z]\.)', r'\1. ', name)
    # A bare capital standing alone between names gets a period: "K Le" -> "K. Le"
    name = re.sub(r'\b([A-Z])\b(?!\.)(?=\s)', r'\1.', name)
    return normalize_space(name)


def normalize_author(author: str) -> str:
    """Canonicalise an author string.

    Strips list punctuation, flips a single "Surname, Forename" into natural
    order, and regularises initials. Multi-author strings are left in their
    original order, since a comma there separates people rather than marking an
    inverted name.
    """
    author = normalize_space(author).strip(' ;,')
    if not author:
        return ""

    # Two or more commas means a list of people, not an inverted single name.
    if author.count(',') == 1:
        surname, forename = (part.strip() for part in author.split(','))
        forename_words = forename.split()
        looks_inverted = (
            surname and forename
            and len(forename_words) <= 3
            and ' and ' not in author.lower()
            and '&' not in author
            # A trailing suffix ("King, Martin Luther, Jr.") is handled above by
            # the comma count; here reject a forename that is only a suffix.
            and forename_words[0].lower().strip('.') not in NAME_SUFFIXES
        )
        if looks_inverted:
            author = f"{forename} {surname}"

    return _normalize_initials(author)
