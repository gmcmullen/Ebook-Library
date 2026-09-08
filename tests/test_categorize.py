"""Tests for the subject-to-category rules.

These are pure functions, so they run without a library or network access.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ebooklibrary.categorize import categorize_from_subjects  # noqa: E402
from ebooklibrary.text import normalize_space, parse_series_from_title  # noqa: E402


def primary(subjects, **kwargs):
    result = categorize_from_subjects(subjects, **kwargs)
    return result[0] if result else None


def test_explicit_nonfiction_is_not_read_as_fiction():
    # "fiction" is a substring of "nonfiction"; matching must respect word boundaries.
    assert primary(["Nonfiction", "Economics"]) == "Non-Fiction"
    assert primary(["Non-fiction", "History"]) == "Non-Fiction"
    assert primary(["Non fiction", "Biography"]) == "Non-Fiction"


def test_explicit_fiction():
    assert primary(["Fiction", "Science fiction"]) == "Fiction"
    assert primary(["Fiction, fantasy, general"]) == "Fiction"
    assert primary(["Novel"]) == "Fiction"


def test_subgenres_are_capped_at_three():
    categories = categorize_from_subjects([
        "Fiction", "Science fiction", "Fantasy", "Mystery", "Horror", "Romance",
    ])
    assert categories[0] == "Fiction"
    assert len(categories) <= 4


def test_nonfiction_subgenres():
    assert categorize_from_subjects(["Cooking", "Recipes"]) == [
        "Non-Fiction", "Food & Cooking",
    ]
    assert primary(["Biography", "Autobiography"]) == "Non-Fiction"


def test_no_signal_returns_empty():
    assert categorize_from_subjects([]) == []
    assert categorize_from_subjects([], title="", description="") == []


def test_free_text_fallback_is_stricter():
    # A blurb that merely mentions science must not become Non-Fiction.
    assert categorize_from_subjects(
        [], title="Ship of Fools",
        description="A tale in which a scientist studies the stars.",
    ) == []
    assert primary([], title="A Memoir of War", description="his memoir of the war") == "Non-Fiction"


def test_series_parsing():
    assert parse_series_from_title("Leviathan Wakes (The Expanse #1)") == ("The Expanse", 1.0)
    assert parse_series_from_title("Foundation (Foundation, Book 2)") == ("Foundation", 2.0)
    assert parse_series_from_title("Akira (Akira, Vol. 3)") == ("Akira", 3.0)
    assert parse_series_from_title("Some Book (The Expanse)") == ("The Expanse", None)
    assert parse_series_from_title("A Novel (1984)") == (None, None)
    assert parse_series_from_title("") == (None, None)


def test_normalize_space_strips_opf_whitespace():
    assert normalize_space("\n   Benjamin Bratton\n  ") == "Benjamin Bratton"
    assert normalize_space("Ann   Leckie") == "Ann Leckie"
    assert normalize_space("") == ""


def test_generic_keyword_does_not_match_inside_a_specific_phrase():
    # "POLITICAL SCIENCE" belongs to Politics & Society and must not also
    # register as "science" for Science & Nature.
    categories = categorize_from_subjects([
        "Nonfiction", "POLITICAL SCIENCE / American Government",
        "POLITICAL SCIENCE / Security", "Biography",
    ])
    assert categories[0] == "Non-Fiction"
    assert "Science & Nature" not in categories
    assert "Politics & Society" in categories

    assert "Science & Nature" not in categorize_from_subjects(
        ["Nonfiction", "Computer science", "Programming"]
    )
    # The generic term still works on its own.
    assert "Science & Nature" in categorize_from_subjects(
        ["Nonfiction", "Science", "Physics", "Astronomy"]
    )


def test_free_text_never_invents_subgenres():
    # A blurb asking whether a text is "autobiography, fantasy or fraud" is not
    # a memoir, and must not produce sub-genres either.
    assert categorize_from_subjects(
        [], title="Three Eight One",
        description="Is it autobiography, fantasy or fraud? Rowena curates an archive.",
    ) == []
    # A decisive subtitle phrase still classifies, but with no sub-genre.
    assert categorize_from_subjects([], title="My Life: A Memoir") == ["Non-Fiction"]
    assert categorize_from_subjects([], title="Birnam Wood: A Novel") == ["Fiction"]


def test_author_normalisation():
    from ebooklibrary.text import normalize_author

    assert normalize_author("Corey, James S.A.") == "James S. A. Corey"
    assert normalize_author("Corey, James S. A.;") == "James S. A. Corey"
    assert normalize_author("James S.A. Corey") == "James S. A. Corey"
    assert normalize_author("Ursula K Le Guin") == "Ursula K. Le Guin"
    assert normalize_author("Dick, Philip K.") == "Philip K. Dick"
    # A list of people is not an inverted single name.
    multi = "Julian Assange, Jacob Appelbaum, Andy Muller-Maguhn"
    assert normalize_author(multi) == multi
    assert normalize_author("") == ""


def test_author_normalisation_composes_unicode():
    import unicodedata
    from ebooklibrary.text import normalize_author

    decomposed = unicodedata.normalize('NFD', "André Alexis")
    composed = unicodedata.normalize('NFC', "André Alexis")
    assert decomposed != composed
    assert normalize_author(decomposed) == normalize_author(composed)


def test_title_only_match_is_checked_against_the_author():
    from ebooklibrary.metadata import _author_plausible

    assert _author_plausible("James S.A. Corey", ["James S. A. Corey"])
    assert not _author_plausible("Adrian Czajkowski", ["Adrian Tchaikovsky"])
    assert not _author_plausible("Ann Leckie", [])


def test_subgenres_can_settle_the_primary_category():
    # "Buddhism" and "American literature" name a shelf but appear in neither
    # coarse primary list, so the sub-genre lists have to decide.
    assert categorize_from_subjects(["Commentaries", "Buddhism, doctrines"]) == [
        "Non-Fiction", "Religion & Spirituality",
    ]
    assert categorize_from_subjects(["American literature"]) == [
        "Fiction", "Literary Fiction",
    ]
    assert primary(["Feminism", "Feminist theory", "Society"]) == "Non-Fiction"
    # Genuinely ambiguous subjects still yield nothing rather than a guess.
    assert categorize_from_subjects(["Language and languages"]) == []


def test_rename_decision_is_conservative():
    from ebooklibrary.authors import rename_decision

    # A plain misspelling of the same name is safe to correct.
    assert rename_decision("Iain M. Banks", "Ian M. Banks")[0]
    assert rename_decision("Nassim Nicholas Taleb", "Nissim Nicholas Taleb")[0]

    # These all hold something the metadata does not, so the filename stands.
    assert not rename_decision("Octavia Butler", "Octavia E. Butler")[0]
    assert not rename_decision("David Cohen", "David Cohen and Brad Feld")[0]
    assert not rename_decision("Rainbows End", "Vernor Vinge")[0]
    # Accents must never be stripped by a rename.
    assert not rename_decision("Oscar Martinez", "Óscar Martínez")[0]


def test_editing_a_book_is_not_mistaken_for_a_duplicate(tmp_path):
    """A rescanned file must not be discarded as a duplicate of its own old entry."""
    from ebooklibrary.catalog import Catalog
    from ebooklibrary.config import Config

    config = Config(base_dir=tmp_path)
    config.ensure_dirs()
    catalog = Catalog(config)
    catalog["oldhash"] = {"title": "Dune", "author": "Frank Herbert", "path": "dune.epub"}

    # Same file, new hash after an edit: not a duplicate.
    assert catalog.find_duplicate(
        "Dune", "Frank Herbert", exclude_hash="newhash", exclude_path="dune.epub"
    ) == ""

    # A genuinely different file holding the same book still is one.
    assert catalog.find_duplicate(
        "Dune", "Frank Herbert", exclude_hash="newhash", exclude_path="dune-copy.epub"
    ) == "oldhash"


def test_subjects_set_in_the_file_outrank_the_apis():
    """A tag someone set by hand must survive the next scan."""
    from ebooklibrary.metadata import MetadataService

    book = {
        "title": "The Hacker Crackdown",
        "file_subjects": ["Non-Fiction", "Science", "Technology"],
        # The APIs disagree; the human wins.
        "api_subjects": ["Fiction", "Science fiction"],
    }
    assert MetadataService.categorize(book)[0] == "Non-Fiction"

    # Without file subjects, the API list is used as before.
    del book["file_subjects"]
    assert MetadataService.categorize(book)[0] == "Fiction"
