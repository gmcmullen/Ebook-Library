"""Write corrected metadata back into EPUB files.

An EPUB is a ZIP with two rules that a naive rewrite breaks: the ``mimetype``
entry must come first and must be stored uncompressed. Every write here goes to
a temporary file that is parsed and checked before it atomically replaces the
original, so a failure partway through leaves the original untouched.
"""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from lxml import etree

from .logging_setup import get_logger

logger = get_logger()

DC_NS = "http://purl.org/dc/elements/1.1/"
OPF_NS = "http://www.idpf.org/2007/opf"
MIMETYPE = "application/epub+zip"


def sort_name(author: str) -> str:
    """"Surname, Forename" form, which is what OPF's file-as attribute wants."""
    parts = author.split()
    if len(parts) < 2:
        return author
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def find_opf_name(archive: zipfile.ZipFile) -> Optional[str]:
    """Locate the OPF package document, preferring what container.xml declares."""
    try:
        container = etree.fromstring(archive.read("META-INF/container.xml"))
        rootfiles = container.findall(
            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        for rootfile in rootfiles:
            path = rootfile.get("full-path")
            if path and path in archive.namelist():
                return path
    except (KeyError, etree.XMLSyntaxError) as e:
        logger.debug(f"container.xml unusable ({e}); falling back to a name scan")

    candidates = [n for n in archive.namelist() if n.endswith(".opf")]
    return candidates[0] if candidates else None


def read_creator(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(dc:creator, dc:title)`` from an EPUB, either possibly None."""
    try:
        with zipfile.ZipFile(path) as archive:
            opf_name = find_opf_name(archive)
            if not opf_name:
                return None, None
            root = etree.fromstring(archive.read(opf_name))
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, OSError) as e:
        logger.debug(f"Cannot read {path}: {e}")
        return None, None

    def text_of(tag):
        node = root.find(f".//{{{DC_NS}}}{tag}")
        return node.text if node is not None and node.text else None

    return text_of("creator"), text_of("title")


def _updated_opf(opf_bytes: bytes, author: str) -> Optional[bytes]:
    """Return the OPF with dc:creator set to ``author``, or None if unchanged."""
    root = etree.fromstring(opf_bytes)
    creator = root.find(f".//{{{DC_NS}}}creator")

    if creator is None:
        metadata = root.find(f".//{{{OPF_NS}}}metadata")
        if metadata is None:
            logger.debug("No <metadata> element; cannot add a creator")
            return None
        creator = etree.SubElement(metadata, f"{{{DC_NS}}}creator")

    if (creator.text or "") == author:
        return None

    creator.text = author
    # Keep the sort form in step with the display name.
    if creator.get(f"{{{OPF_NS}}}file-as") is not None:
        creator.set(f"{{{OPF_NS}}}file-as", sort_name(author))

    return etree.tostring(
        root, xml_declaration=True, encoding="utf-8", standalone=None
    )


def set_author(path: Path, author: str, dry_run: bool = False) -> bool:
    """Set an EPUB's dc:creator. Returns True if the file was (or would be) changed.

    The archive is rebuilt entry by entry so that ``mimetype`` stays first and
    uncompressed; the result is reopened and re-parsed before it replaces the
    original.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            opf_name = find_opf_name(archive)
            if not opf_name:
                logger.warning(f"No OPF in {path.name}; skipped")
                return False

            new_opf = _updated_opf(archive.read(opf_name), author)
            if new_opf is None:
                return False
            if dry_run:
                return True

            entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, OSError) as e:
        logger.warning(f"Cannot rewrite {path.name}: {e}")
        return False

    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".epub.tmp")
    os.close(handle)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
            # The mimetype entry must be first and stored, per the EPUB spec.
            out.writestr(
                zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED
            )
            for info, data in entries:
                if info.filename == "mimetype":
                    continue
                payload = new_opf if info.filename == opf_name else data
                # Preserve each entry's original timestamp and compression.
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                out.writestr(new_info, payload)

        _verify(tmp_path, opf_name, author, expected_entries=len(entries))
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        logger.error(f"Rewrite of {path.name} failed verification, original kept: {e}")
        return False

    # Carry over the original file's permissions before swapping it in.
    try:
        shutil.copystat(path, tmp_path)
    except OSError:
        pass
    os.replace(tmp_path, path)
    return True


def _verify(path: Path, opf_name: str, author: str, expected_entries: int) -> None:
    """Raise unless the rewritten EPUB is well-formed and carries the new author."""
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"corrupt entry: {bad}")

        names = archive.namelist()
        if names[0] != "mimetype":
            raise ValueError("mimetype is not the first entry")
        if archive.read("mimetype").decode("ascii").strip() != MIMETYPE:
            raise ValueError("mimetype content is wrong")
        if len(names) != expected_entries:
            raise ValueError(f"entry count changed: {len(names)} vs {expected_entries}")

        root = etree.fromstring(archive.read(opf_name))
        creator = root.find(f".//{{{DC_NS}}}creator")
        if creator is None or (creator.text or "") != author:
            raise ValueError("dc:creator was not written")
