"""Project Gutenberg catalogue metadata.

Fetches one small RDF record per book and reads only its subject headings.
No book text is retrieved here -- the texts were already ingested in Phase
1A, and this adds nothing to them.

The subjects are Library of Congress Subject Headings: an externally curated,
controlled vocabulary maintained by librarians. That is what makes literature
able to participate in the concept layer on the same footing as AniList's
curated tags, rather than needing its concepts guessed from its prose.

Catalogue metadata only, ~18KB per book, one request per work, rate-limited
by the caller. This is the same shape as the AniList and MediaWiki clients:
the only module in its area that touches the network, returning plain data so
every parsing test runs offline against fixtures.
"""

import re
from dataclasses import dataclass, field

import httpx

from app import __version__

RDF_URL_TEMPLATE = "https://www.gutenberg.org/cache/epub/{ebook_id}/pg{ebook_id}.rdf"
USER_AGENT = f"Noema/{__version__} (research project; catalogue metadata only)"

# Subjects appear as <dcterms:subject><rdf:Description><rdf:value>TEXT.
# Both LCSH headings and LCC class letters use this shape; the class letters
# are one-to-three uppercase characters and are not subject headings.
_SUBJECT_RE = re.compile(
    r"<dcterms:subject>.*?<rdf:value>(.*?)</rdf:value>", re.DOTALL | re.IGNORECASE
)
_LCC_RE = re.compile(r"^[A-Z]{1,3}$")

# Cover art is a <pgterms:file> whose dcterms:format value is an image type.
# Gutenberg publishes the same cover at two sizes and states both; we read
# the element rather than assembling a URL from the ebook id, so a book with
# no cover on record simply has none.
_COVER_FILE_RE = re.compile(
    r"<pgterms:file[^>]*rdf:about=\"([^\"]*)\".*?</pgterms:file>",
    re.DOTALL | re.IGNORECASE,
)
_IMAGE_FORMAT_RE = re.compile(r"<rdf:value[^>]*>\s*image/(\w+)\s*</rdf:value>", re.IGNORECASE)
# Gutenberg's own naming. `medium` is ~400px wide and is what the product
# renders; `small` is a thumbnail and is kept only as a fallback.
_COVER_PREFERENCE = (".cover.medium.", ".cover.small.")
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


class GutenbergError(RuntimeError):
    """Gutenberg could not be reached or returned an error."""


class BookNotFoundError(GutenbergError):
    """No catalogue record exists for the requested ebook id."""


@dataclass
class GutenbergMetadata:
    """What the catalogue says about one book."""

    ebook_id: str
    source_url: str
    # Library of Congress Subject Headings, e.g. "Horror tales",
    # "Monsters -- Fiction".
    subjects: list[str] = field(default_factory=list)
    # Library of Congress Classification letters, e.g. "PR". Kept separate:
    # a shelving class is not a subject heading and must not be mapped as one.
    lcc_classes: list[str] = field(default_factory=list)
    # Cover art URL as the catalogue record states it, or None when the
    # record lists no cover image. Phase 1AA.
    cover_image_url: str | None = None
    # Which `pgterms:file` entry the URL came from, for provenance.
    cover_image_source_field: str | None = None


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return text


def parse_cover_image(rdf_text: str) -> tuple[str | None, str | None]:
    """The cover art URL a catalogue record states, and which entry it came from.

    Phase 1AA. Gutenberg lists every downloadable file for a book as a
    `pgterms:file`, each with its media type; the cover is the one whose
    type is an image. Returns `(url, source_field)`, both `None` when the
    record lists no cover -- which is a real answer for plenty of older
    texts, and is left as "no cover" rather than patched over.

    The URL is read out of the record. It is never assembled from the ebook
    id, so this cannot invent a cover for a book that has none.
    """
    candidates: dict[str, str] = {}
    for match in _COVER_FILE_RE.finditer(rdf_text):
        url = _unescape(match.group(1)).strip()
        if not url or not _IMAGE_FORMAT_RE.search(match.group(0)):
            continue
        for marker in _COVER_PREFERENCE:
            if marker in url:
                candidates.setdefault(marker, url)

    for marker in _COVER_PREFERENCE:
        if marker in candidates:
            return candidates[marker], f"pgterms:file{marker}"
    return None, None


def parse_subjects(rdf_text: str, ebook_id: str, source_url: str) -> GutenbergMetadata:
    """Read subject headings and cover art out of a Gutenberg RDF record.

    Order is preserved and duplicates are dropped, so a re-fetch of an
    unchanged record produces an identical result.
    """
    metadata = GutenbergMetadata(ebook_id=str(ebook_id), source_url=source_url)
    metadata.cover_image_url, metadata.cover_image_source_field = parse_cover_image(
        rdf_text
    )
    seen: set[str] = set()

    for raw in _SUBJECT_RE.findall(rdf_text):
        value = _unescape(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        if _LCC_RE.match(value):
            metadata.lcc_classes.append(value)
        else:
            metadata.subjects.append(value)

    return metadata


class GutenbergClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def fetch_metadata(self, ebook_id: str | int) -> GutenbergMetadata:
        """Catalogue subjects for one ebook id. Never fetches book text."""
        url = RDF_URL_TEMPLATE.format(ebook_id=ebook_id)
        try:
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as exc:
            raise GutenbergError(f"could not reach Gutenberg: {exc}") from exc

        if response.status_code == 404:
            raise BookNotFoundError(f"no Gutenberg catalogue record for ebook {ebook_id}")
        if response.status_code >= 400:
            raise GutenbergError(
                f"Gutenberg returned HTTP {response.status_code} for ebook {ebook_id}"
            )

        return parse_subjects(response.text, str(ebook_id), url)
