"""Literature adapter: plain-text public-domain works -> `SourceWork`.

The transformation is deliberately explicit and inspectable:

    raw text
      -> strip Project Gutenberg boilerplate (if present)
      -> split into blocks on blank lines
      -> blocks whose first line looks like a chapter heading start a Container
      -> every other block becomes one ContentUnit (a paragraph)

It does not infer structure the source doesn't show. If a text contains no
chapter headings, it gets a single container rather than invented divisions,
flagged in that container's metadata so the absence is visible downstream.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.ingestion.normalized import (
    SourceContainer,
    SourceContentUnit,
    SourceCreator,
    SourceWork,
)

ADAPTER_NAME = "literature.plain_text"
ADAPTER_VERSION = 1

# "CHAPTER I.", "Chapter 12:", "BOOK II", "PART ONE" -- the heading forms that
# actually appear in the plain-text editions we ingest. A trailing remainder on
# the same line (or the rest of the block) is treated as the chapter title.
_HEADING_RE = re.compile(
    r"^\s*(?P<kind>chapter|book|part)\s+(?P<number>[0-9]+|[ivxlcdm]+)\b[.:]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

# Project Gutenberg wraps its texts in *** START/END OF ... *** markers. The
# enclosed text is the public-domain work; the surrounding boilerplate is PG's
# own license text, which we neither store nor redistribute.
_PG_START_RE = re.compile(r"^\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.MULTILINE)
_PG_END_RE = re.compile(r"^\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.MULTILINE)


class MalformedSourceError(ValueError):
    """Raised when a source text cannot be parsed into any content at all."""


def strip_gutenberg_boilerplate(text: str) -> tuple[str, bool]:
    """Return (body, was_wrapped). Leaves non-Gutenberg text untouched."""
    start = _PG_START_RE.search(text)
    end = _PG_END_RE.search(text)
    if start and end and start.end() < end.start():
        return text[start.end() : end.start()], True
    return text, False


def _split_blocks(text: str) -> list[str]:
    """Split into paragraph blocks on blank lines, preserving order."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]


def _flatten_paragraph(block: str) -> str:
    """Join a hard-wrapped paragraph back into one line."""
    return " ".join(line.strip() for line in block.splitlines() if line.strip())


def _heading_title(block: str) -> str | None:
    """Chapter title from a heading block: same-line remainder, else the rest."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    match = _HEADING_RE.match(lines[0])
    rest_of_line = (match.group("rest") or "").strip() if match else ""
    if rest_of_line:
        return rest_of_line
    remaining = " ".join(lines[1:]).strip()
    return remaining or None


def _is_heading(block: str) -> bool:
    first_line = block.splitlines()[0]
    return _HEADING_RE.match(first_line) is not None


_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _parse_heading_number(token: str) -> int | None:
    """The source's own chapter number, arabic or roman. None if unparseable."""
    if token.isdigit():
        return int(token)

    total = 0
    highest = 0
    for char in reversed(token.lower()):
        value = _ROMAN_VALUES.get(char)
        if value is None:
            return None
        if value < highest:
            total -= value
        else:
            total += value
            highest = value
    return total or None


@dataclass
class PlainTextLiteratureAdapter:
    """Parses one plain-text literary work into the normalized representation.

    `source_ref` is the stable identifier within `source_name` (for a Project
    Gutenberg text, its ebook id) and is what makes re-ingestion idempotent.
    """

    text: str
    title: str
    source_ref: str
    source_name: str = "gutenberg"
    author: str | None = None
    source_url: str | None = None
    source_file: str | None = None
    license_note: str | None = None

    domain_slug: str = "literature"

    def load(self) -> SourceWork:
        body, was_wrapped = strip_gutenberg_boilerplate(self.text)
        blocks = _split_blocks(body)
        if not blocks:
            raise MalformedSourceError(
                f"no readable text found in source {self.source_name}:{self.source_ref}"
            )

        containers, dropped_empty = self._build_containers(blocks)
        if not any(container.content_units for container in containers):
            raise MalformedSourceError(
                f"source {self.source_name}:{self.source_ref} contained headings but no text"
            )

        chapters_detected = any(c.container_type == "chapter" for c in containers)
        content_units = sum(len(c.content_units) for c in containers)

        return SourceWork(
            domain_slug=self.domain_slug,
            source=self.source_name,
            source_ref=self.source_ref,
            title=self.title,
            creators=[SourceCreator(name=self.author)] if self.author else [],
            containers=containers,
            external_ids={"source_ref": self.source_ref},
            extra_metadata={
                "provenance": {
                    "adapter": ADAPTER_NAME,
                    "adapter_version": ADAPTER_VERSION,
                    "source_name": self.source_name,
                    "source_ref": self.source_ref,
                    "source_url": self.source_url,
                    "source_file": self.source_file,
                    "content_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
                    "gutenberg_boilerplate_stripped": was_wrapped,
                    "license_note": self.license_note,
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
                "structure": {
                    "chapter_headings_detected": chapters_detected,
                    "containers": len(containers),
                    "content_units": content_units,
                    # Headings with no body text (a table of contents, most
                    # often). Recorded rather than silently discarded.
                    "empty_headings_dropped": dropped_empty,
                },
            },
        )

    def _build_containers(self, blocks: list[str]) -> tuple[list[SourceContainer], int]:
        """Group blocks into sections, then turn non-empty sections into containers.

        Returns the containers plus the number of headings dropped for having
        no body text. A table of contents typically looks exactly like a
        heading followed immediately by the next heading, so dropping empty
        sections is what keeps a TOC from becoming a phantom chapter.
        """
        sections: list[dict] = []
        current: dict = {"heading": None, "title": None, "number": None, "paragraphs": []}

        for block in blocks:
            if _is_heading(block):
                sections.append(current)
                first_line = block.splitlines()[0].strip()
                match = _HEADING_RE.match(first_line)
                current = {
                    "heading": first_line,
                    "title": _heading_title(block),
                    "number": _parse_heading_number(match.group("number")),
                    "paragraphs": [],
                }
                continue

            paragraph = _flatten_paragraph(block)
            if paragraph:
                current["paragraphs"].append(paragraph)

        sections.append(current)

        containers: list[SourceContainer] = []
        dropped = 0
        position = 0

        for section in sections:
            if not section["paragraphs"]:
                if section["heading"] is not None:
                    dropped += 1
                continue

            units = [
                SourceContentUnit(
                    unit_type="passage",
                    sequence_number=index,
                    text_content=paragraph,
                    extra_metadata={"char_count": len(paragraph)},
                )
                for index, paragraph in enumerate(section["paragraphs"], start=1)
            ]

            if section["heading"] is None:
                # Text before any heading: real content, kept in its own
                # labelled container rather than silently dropped.
                containers.append(
                    SourceContainer(
                        container_type="front_matter",
                        sequence_number=0,
                        title=None,
                        content_units=units,
                        extra_metadata={"note": "text preceding the first chapter heading"},
                    )
                )
                continue

            position += 1
            containers.append(
                SourceContainer(
                    container_type="chapter",
                    # Prefer the source's own chapter number; fall back to
                    # position only when the heading carries no usable number.
                    sequence_number=section["number"] or position,
                    title=section["title"],
                    content_units=units,
                    extra_metadata={
                        "heading": section["heading"],
                        "source_number": section["number"],
                    },
                )
            )

        # No headings anywhere: the schema still needs a container level, so the
        # whole text becomes one, marked so the absence of divisions is visible.
        if containers and all(c.container_type == "front_matter" for c in containers):
            return [
                SourceContainer(
                    container_type="text",
                    sequence_number=1,
                    title=None,
                    content_units=containers[0].content_units,
                    extra_metadata={"chapter_headings_detected": False},
                )
            ], dropped

        return containers, dropped
