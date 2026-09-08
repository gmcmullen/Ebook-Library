"""Map API subject strings onto bookstore-style categories.

Pure functions with no I/O, so categories can be recomputed offline from the
``api_subjects`` stored in books.json without touching the network.
"""
import re
from typing import List

# "fiction" is a substring of "nonfiction", so both markers need word-boundary
# matching or every non-fiction book also trips the fiction test.
NONFICTION_MARKER = re.compile(r'\bnon[\s-]?fiction\b')
FICTION_MARKER = re.compile(r'(?<!non-)(?<!non )\bfiction\b|\bnovels?\b|\bnovella\b')

# Genre-level signals that reliably indicate fiction in both API subjects and free text.
FICTION_GENRES = [
    'thriller', 'mystery', 'detective', 'crime fiction', 'romance',
    'fantasy', 'horror', 'science fiction', 'sci-fi', 'cyberpunk',
    'dystopian', 'utopian', 'alternate history', 'time travel',
    'graphic novel', 'comics', 'manga', 'juvenile fiction',
    'young adult fiction', 'adventure fiction', 'steampunk', 'biopunk',
]

# Broad set — safe against clean categorical terms returned by APIs.
NONFICTION_SUBJECTS_API = [
    'biography', 'autobiography', 'memoir', 'history', 'military history',
    'science', 'physics', 'chemistry', 'biology', 'astronomy', 'mathematics',
    'nature', 'natural history', 'ecology', 'evolution',
    'technology', 'computers', 'programming', 'software', 'engineering',
    'business', 'economics', 'finance', 'management', 'investing',
    'self-help', 'personal development', 'psychology', 'mental health',
    'philosophy', 'ethics', 'logic', 'metaphysics',
    'religion', 'spirituality', 'theology', 'faith',
    'politics', 'political science', 'government', 'social science', 'law',
    'sociology', 'anthropology', 'cultural studies',
    'travel', 'geography', 'exploration',
    'cooking', 'food', 'recipes', 'culinary',
    'art', 'music', 'photography', 'architecture', 'design', 'film',
    'sports', 'fitness', 'recreation',
    'health', 'medicine', 'nutrition',
    'education', 'reference', 'textbook', 'academic',
    'true crime', 'humor', 'satire', 'essays',
]

# Strict set — only unambiguous phrases, for matching against a blurb where
# common words like "science" or "art" appear constantly in fiction.
NONFICTION_SUBJECTS_TEXT = [
    'biography', 'autobiography', 'memoir',
    'military history', 'world history', 'ancient history',
    'natural history', 'natural science',
    'computer science', 'software development',
    'business management', 'personal finance', 'investing',
    'self-help', 'personal development',
    'philosophy of ', 'political theory', 'political science',
    'sociology', 'anthropology',
    'travel writing', 'travel guide',
    'cookbook', 'recipe', 'culinary arts',
    'true crime',
    'popular science', 'popular history',
]

FICTION_SUBGENRES = [
    ('Science Fiction', ['science fiction', 'sci-fi', 'cyberpunk', 'space opera',
                         'dystopian', 'utopian', 'alternate history', 'time travel',
                         'hard sf', 'military sf', 'biopunk', 'steampunk']),
    ('Fantasy', ['fantasy', 'epic fantasy', 'high fantasy', 'urban fantasy',
                 'dark fantasy', 'sword and sorcery', 'fairy tale', 'mythological',
                 'magic realism', 'magical realism']),
    ('Mystery & Thriller', ['mystery', 'thriller', 'detective', 'crime fiction',
                            'suspense', 'noir', 'hardboiled', 'espionage', 'spy fiction',
                            'police procedural', 'legal thriller']),
    ('Horror', ['horror', 'gothic fiction', 'supernatural fiction', 'occult fiction',
                'dark fiction', 'psychological horror']),
    ('Romance', ['romance', 'love stories', 'romantic fiction', 'erotic']),
    ('Historical Fiction', ['historical fiction', 'historical novel']),
    ('Literary Fiction', ['literary fiction', 'contemporary fiction', 'general fiction',
                          'american fiction', 'british fiction', 'literary',
                          'psychological fiction', 'social fiction',
                          # OpenLibrary files many novels under a nationality
                          # plus "literature" and nothing else.
                          'american literature', 'english literature',
                          'literature, collections']),
    ('Young Adult', ['juvenile fiction', 'young adult', 'teen fiction',
                     "children's fiction", 'middle grade']),
    ('Graphic Novel', ['graphic novel', 'comics', 'manga', 'illustrated']),
    ('Short Stories', ['short stories', 'short fiction', 'anthology', 'collected stories']),
    ('Poetry', ['poetry', 'poems', 'verse', 'poetic']),
    ('Plays & Drama', ['drama', 'plays', 'theater', 'theatre', 'playwrights']),
    ('Adventure', ['adventure fiction', 'action adventure', 'adventure story']),
]

NONFICTION_SUBGENRES = [
    ('Biography & Memoir', ['biography', 'autobiography', 'memoir',
                            'personal narrative', 'diaries', 'autobiographical']),
    ('History', ['history', 'world war', 'military history', 'ancient history',
                 'medieval', 'modern history', 'social history', 'cultural history',
                 'political history', 'economic history']),
    ('Science & Nature', ['science', 'physics', 'chemistry', 'biology', 'astronomy',
                          'nature', 'ecology', 'evolution', 'mathematics', 'geology',
                          'natural history', 'zoology', 'botany', 'climate']),
    ('Technology & Computers', ['technology', 'computers', 'programming', 'software',
                                'internet', 'computing', 'engineering',
                                'artificial intelligence', 'machine learning',
                                # Listed so "computer science" is claimed here and
                                # not also counted as generic "science".
                                'computer science', 'data science']),
    ('Business & Economics', ['business', 'economics', 'finance', 'management',
                              'entrepreneurship', 'investing', 'marketing',
                              'accounting', 'leadership', 'corporate',
                              'cryptocurrencies', 'cryptocurrency', 'right of property']),
    ('Self-Help & Psychology', ['self-help', 'personal development', 'psychology',
                                'motivation', 'mental health', 'well-being',
                                'relationships', 'mindfulness', 'productivity']),
    ('Philosophy', ['philosophy', 'ethics', 'logic', 'metaphysics', 'epistemology',
                    'political philosophy', 'moral philosophy']),
    ('Religion & Spirituality', ['religion', 'spirituality', 'theology', 'faith',
                                 'christianity', 'islam', 'buddhism', 'hinduism',
                                 'judaism', 'meditation', 'mysticism']),
    ('Politics & Society', ['politics', 'political science', 'government',
                            'social science', 'law', 'sociology', 'anthropology',
                            'cultural studies', 'current events', 'public policy',
                            'feminism', 'feminist theory', 'democracy', 'capitalism',
                            'free enterprise', 'radicalism', 'society']),
    ('Art, Music & Photography', ['art', 'music', 'photography', 'architecture',
                                  'design', 'film', 'cinema', 'visual arts',
                                  'performing arts']),
    ('Travel', ['travel', 'geography', 'exploration', 'adventure travel']),
    ('Food & Cooking', ['cooking', 'food', 'recipes', 'culinary', 'baking',
                        'cuisine', 'gastronomy']),
    ('Sports & Recreation', ['sports', 'fitness', 'recreation', 'outdoor activities',
                             'games', 'athletics']),
    ('Health & Medicine', ['health', 'medicine', 'medical', 'nutrition', 'wellness',
                           'disease', 'public health', 'drug addiction', 'opioids',
                           'narcotics', 'attention-deficit hyperactivity disorder',
                           'attention-deficit disorder in adults']),
    ('True Crime', ['true crime', 'criminal investigation', 'forensic']),
    ('Humor', ['humor', 'comedy', 'satire', 'wit']),
    ('Education & Reference', ['education', 'reference', 'textbook', 'academic',
                               'study guides', 'teaching']),
]


def get_subgenres(primary: str, combined: str) -> List[str]:
    """Sub-genre labels whose keywords appear in ``combined``.

    Longer keywords are matched first and their text is consumed, so a generic
    term cannot re-match inside a more specific phrase that already claimed it —
    "political science" belongs to Politics & Society, and must not also count
    as "science" for Science & Nature.
    """
    genre_map = FICTION_SUBGENRES if primary == 'Fiction' else NONFICTION_SUBGENRES

    pairs = sorted(
        ((genre, kw) for genre, keywords in genre_map for kw in keywords),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )

    remaining = combined
    matched = set()
    for genre, keyword in pairs:
        pattern = re.compile(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])')
        # Blank every occurrence, so a repeated phrase cannot leave one behind
        # for a shorter generic keyword to claim.
        remaining, hits = pattern.subn(lambda m: ' ' * len(m.group()), remaining)
        if hits:
            matched.add(genre)

    # Preserve the declared genre order rather than match order.
    return [genre for genre, _ in genre_map if genre in matched]


# Phrases decisive enough on their own in prose — the kind of thing that appears
# in a subtitle ("A Memoir", "A Novel") rather than incidentally in a blurb.
STRONG_NONFICTION_TEXT = re.compile(
    r'\ba (?:memoir|biography|history|true story)\b'
    r'|\ban autobiography\b'
    r'|\bthe (?:biography|autobiography)\b'
    r'|\bhis|her memoir\b'
    r'|\btrue crime\b'
)
STRONG_FICTION_TEXT = re.compile(r'\ba (?:novel|novella)\b|\bshort stories\b')


def _categorize_from_free_text(combined: str) -> List[str]:
    """Primary category only, from a title and blurb, and only on strong evidence.

    A blurb is not a subject heading: a novel whose description asks whether a
    text is "autobiography, fantasy or fraud" is not a memoir. So this needs an
    explicit marker or two independent signals, and never emits sub-genres —
    guessing those from prose produced labels like "Travel" on a novel.
    """
    is_nonfiction = bool(
        NONFICTION_MARKER.search(combined) or STRONG_NONFICTION_TEXT.search(combined)
    )
    is_fiction = bool(
        FICTION_MARKER.search(combined) or STRONG_FICTION_TEXT.search(combined)
    )

    if is_nonfiction and not is_fiction:
        return ['Non-Fiction']
    if is_fiction and not is_nonfiction:
        return ['Fiction']

    # No explicit marker: require corroboration from two distinct phrases.
    nonfiction_hits = {s for s in NONFICTION_SUBJECTS_TEXT if s in combined}
    fiction_hits = {g for g in FICTION_GENRES if g in combined}

    if len(nonfiction_hits) >= 2 and not fiction_hits:
        return ['Non-Fiction']
    if len(fiction_hits) >= 2 and not nonfiction_hits:
        return ['Fiction']

    return []


def categorize_from_subjects(
    subjects: List[str], title: str = '', description: str = ''
) -> List[str]:
    """Determine bookstore-style categories from API subjects, or failing that, free text.

    Returns ``['Fiction'|'Non-Fiction', *up to three sub-genres]``, or ``[]`` when
    nothing matches. API subjects use broad keyword matching; the free-text
    fallback uses a stricter phrase list to avoid false positives from
    incidental word use in a blurb.
    """
    using_api_subjects = bool(subjects)
    combined = ' '.join(s.lower() for s in subjects)
    if not combined.strip():
        combined = f"{title} {description}".lower()
    if not combined.strip():
        return []

    if not using_api_subjects:
        return _categorize_from_free_text(combined)

    is_explicit_nonfiction = bool(NONFICTION_MARKER.search(combined))
    is_explicit_fiction = bool(FICTION_MARKER.search(combined))

    has_fiction_genre = any(g in combined for g in FICTION_GENRES)
    has_nonfiction_subject = any(s in combined for s in NONFICTION_SUBJECTS_API)

    if is_explicit_nonfiction and not is_explicit_fiction:
        primary = 'Non-Fiction'
    elif is_explicit_fiction and not (is_explicit_nonfiction or has_nonfiction_subject):
        primary = 'Fiction'
    elif has_fiction_genre and not (is_explicit_nonfiction or has_nonfiction_subject):
        primary = 'Fiction'
    elif has_nonfiction_subject and not has_fiction_genre and not is_explicit_fiction:
        primary = 'Non-Fiction'
    elif is_explicit_fiction:
        primary = 'Fiction'
    elif has_nonfiction_subject:
        primary = 'Non-Fiction'
    else:
        # Nothing in the primary lists matched. The sub-genre lists are far more
        # detailed, so let them decide: "Buddhism" or "American literature" name
        # a shelf even though neither appears in the coarse lists above.
        fiction_hits = get_subgenres('Fiction', combined)
        nonfiction_hits = get_subgenres('Non-Fiction', combined)
        if len(fiction_hits) > len(nonfiction_hits):
            primary = 'Fiction'
        elif len(nonfiction_hits) > len(fiction_hits):
            primary = 'Non-Fiction'
        else:
            return []

    return [primary] + get_subgenres(primary, combined)[:3]
