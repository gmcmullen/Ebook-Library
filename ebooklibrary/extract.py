"""Read hashes, metadata, text and embedded covers out of ebook files."""
import hashlib
import mmap
import warnings
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from .logging_setup import get_logger
from .text import (clean_description, normalize_author, normalize_space,
                   parse_series_from_title)

warnings.filterwarnings('ignore', category=UserWarning, module='ebooklib')
warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib')

logger = get_logger()

# Files above this size are hashed via mmap rather than buffered reads.
MMAP_THRESHOLD = 10 * 1024 * 1024
HASH_CHUNK = 1024 * 1024
WORDS_PER_PAGE = 250
MAX_PAGES = 100
MIN_EMBEDDED_COVER_BYTES = 5000
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def calculate_file_hash(file_path: Path) -> str:
    """SHA-256 of a file, using mmap for large ones. Returns '' on failure."""
    try:
        sha256 = hashlib.sha256()
        size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            if size > MMAP_THRESHOLD:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    for chunk in iter(lambda: mm.read(HASH_CHUNK), b""):
                        sha256.update(chunk)
            else:
                for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
                    sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating hash for {file_path}: {e}")
        return ""


def _paginate(text: str, limit: int = MAX_PAGES) -> list:
    words = text.split()
    return [
        ' '.join(words[i:i + WORDS_PER_PAGE])
        for i in range(0, len(words), WORDS_PER_PAGE)
    ][:limit]


def extract_epub_content(file_path: Path) -> str:
    """Approximately the first 100 pages of text from an EPUB."""
    try:
        book = epub.read_epub(file_path)
        content = []
        for item in book.get_items():
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            text = BeautifulSoup(item.get_content(), 'html.parser').get_text()
            content.extend(_paginate(text))
            if len(content) >= MAX_PAGES:
                break
        return ' '.join(content[:MAX_PAGES])
    except Exception as e:
        logger.debug(f"Error extracting EPUB content from {file_path}: {e}")
        return ""


def extract_pdf_content(file_path: Path) -> str:
    """Text from the first 100 pages of a PDF."""
    try:
        try:
            from pypdf import PdfReader
        except ImportError:  # older installs still ship PyPDF2
            from PyPDF2 import PdfReader
        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            pages = [
                reader.pages[i].extract_text()
                for i in range(min(MAX_PAGES, len(reader.pages)))
            ]
        return ' '.join(p for p in pages if p)
    except Exception as e:
        logger.debug(f"Error extracting PDF content from {file_path}: {e}")
        return ""


def extract_text_content(file_path: Path) -> str:
    """Text from the start of a plain-text-ish file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return ' '.join(_paginate(f.read()))
    except Exception as e:
        logger.debug(f"Error extracting text content from {file_path}: {e}")
        return ""


def extract_book_content(file_path: Path) -> str:
    """Dispatch content extraction on file type; '' for unsupported formats."""
    suffix = file_path.suffix.lower()
    if suffix == '.epub':
        return extract_epub_content(file_path)
    if suffix == '.pdf':
        return extract_pdf_content(file_path)
    if suffix in {'.txt', '.rtf', '.doc', '.docx'}:
        return extract_text_content(file_path)
    return ""


def _find_opf(epub_zip: zipfile.ZipFile) -> Optional[str]:
    opf_files = [n for n in epub_zip.namelist() if n.endswith('.opf')]
    return opf_files[0] if opf_files else None


def metadata_from_filename(file_path: Path) -> Dict:
    """Title and author from the ``Author - Title.ext`` naming convention.

    Applies to every format, not just EPUB: a PDF or MOBI with no embedded
    metadata would otherwise get no title, and so no lookup, cover or category.
    """
    stem = normalize_space(file_path.stem)
    if ' - ' not in stem:
        return {'title': stem} if stem else {}

    author, title = stem.split(' - ', 1)
    author, title = normalize_author(author), normalize_space(title)
    if not (author and title):
        return {}
    return {'author': author, 'title': title}


def extract_epub_metadata(file_path: Path) -> Dict:
    """Title, author, language, description and series from an EPUB.

    Reads DC metadata via ebooklib, then parses the OPF directly to fill gaps
    and pick up Calibre / EPUB3 series fields, falling back to the
    ``Author - Title`` filename convention.
    """
    metadata: Dict = {}

    try:
        book = epub.read_epub(file_path)
        for key, dc_name in (
            ('title', 'title'), ('author', 'creator'),
            ('language', 'language'), ('description', 'description'),
        ):
            values = book.get_metadata('DC', dc_name)
            if values:
                metadata[key] = values[0][0]
    except Exception as e:
        logger.debug(f"ebooklib extraction failed for {file_path}: {e}")

    try:
        with zipfile.ZipFile(file_path, 'r') as epub_zip:
            opf_name = _find_opf(epub_zip)
            if opf_name:
                soup = BeautifulSoup(epub_zip.read(opf_name), 'xml')

                for key, tag in (
                    ('title', 'dc:title'), ('author', 'dc:creator'),
                    ('language', 'dc:language'), ('description', 'dc:description'),
                ):
                    if not metadata.get(key):
                        elem = soup.find(tag)
                        if elem:
                            metadata[key] = elem.text

                # dc:subject holds the tags set in a reader or editor. These are
                # a deliberate human choice, so they are kept separate from — and
                # later preferred over — whatever the APIs suggest.
                subjects = [
                    normalize_space(e.text) for e in soup.find_all('dc:subject')
                    if e.text and normalize_space(e.text)
                ]
                if subjects:
                    seen = set()
                    metadata['file_subjects'] = [
                        x for x in subjects
                        if not (x.lower() in seen or seen.add(x.lower()))
                    ]

                # Calibre series metadata
                series_elem = soup.find('meta', {'name': 'calibre:series'})
                if series_elem and series_elem.get('content', '').strip():
                    metadata['series'] = series_elem['content'].strip()

                index_elem = soup.find('meta', {'name': 'calibre:series_index'})
                if index_elem:
                    try:
                        metadata['series_index'] = float(index_elem.get('content', '0'))
                    except (ValueError, TypeError):
                        pass

                # EPUB3 collection metadata
                if not metadata.get('series'):
                    collection = soup.find('meta', {'property': 'belongs-to-collection'})
                    if collection:
                        metadata['series'] = collection.get_text(strip=True)
                    position = soup.find('meta', {'property': 'group-position'})
                    if position and not metadata.get('series_index'):
                        try:
                            metadata['series_index'] = float(position.get_text(strip=True))
                        except (ValueError, TypeError):
                            pass
    except Exception as e:
        logger.debug(f"OPF extraction failed for {file_path}: {e}")

    for key, value in metadata_from_filename(file_path).items():
        metadata.setdefault(key, value)

    for field in ('title', 'language', 'series'):
        if metadata.get(field):
            metadata[field] = normalize_space(metadata[field])
    if metadata.get('author'):
        metadata['author'] = normalize_author(metadata['author'])

    if metadata.get('description'):
        metadata['description'] = clean_description(metadata['description'])

    if not metadata.get('series') and metadata.get('title'):
        series_name, series_index = parse_series_from_title(metadata['title'])
        if series_name:
            metadata['series'] = series_name
            if series_index is not None:
                metadata['series_index'] = series_index

    return metadata


def _find_cover_href(soup: BeautifulSoup) -> Optional[str]:
    """Locate the cover image href in an OPF manifest (EPUB3, then EPUB2)."""
    item = soup.find('item', {'properties': lambda x: x and 'cover-image' in x.split()})
    if item and item.get('href'):
        return item['href']

    cover_meta = soup.find('meta', {'name': 'cover'})
    if cover_meta and cover_meta.get('content'):
        ref = soup.find('item', {'id': cover_meta['content']})
        if ref and ref.get('media-type', '').startswith('image/'):
            return ref.get('href')

    for id_val in ('cover-image', 'cover', 'cover_image', 'coverimage', 'img-cover'):
        ref = soup.find('item', {'id': id_val})
        if ref and ref.get('media-type', '').startswith('image/'):
            return ref.get('href')

    return None


def read_epub_cover(epub_path: Path) -> Optional[Tuple[bytes, str]]:
    """Return the EPUB's embedded cover as ``(image_bytes, extension)``, or None.

    Reading rather than writing keeps the decision about where — and whether —
    to save it with the caller, which can then skip a write when the bytes match
    what is already on disk.
    """
    try:
        with zipfile.ZipFile(epub_path, 'r') as epub_zip:
            opf_name = _find_opf(epub_zip)
            if not opf_name:
                return None

            soup = BeautifulSoup(epub_zip.read(opf_name), 'xml')
            cover_href = _find_cover_href(soup)
            if not cover_href:
                return None

            opf_dir = str(Path(opf_name).parent)
            img_path = cover_href if opf_dir == '.' else f"{opf_dir}/{cover_href}"
            img_path = str(Path(img_path.lstrip('/')))

            # EPUB internals are inconsistently cased; match case-insensitively.
            actual = {n.lower(): n for n in epub_zip.namelist()}.get(img_path.lower())
            if not actual:
                return None

            img_data = epub_zip.read(actual)
            if len(img_data) < MIN_EMBEDDED_COVER_BYTES:
                return None

            ext = Path(actual).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                ext = '.jpg'
            return img_data, ext
    except Exception as e:
        logger.debug(f"Failed to read embedded cover from {epub_path}: {e}")
        return None
