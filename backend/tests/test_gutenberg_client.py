"""Gutenberg catalogue parsing. Fixture-driven; no network.

The fixture is a real RDF record (Frankenstein, ebook 84) trimmed to its
subject blocks, so the parser is tested against the document shape Gutenberg
actually serves rather than one invented here.
"""

from pathlib import Path

import pytest

from app.services.ingestion.gutenberg_client import parse_subjects

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_URL = "https://www.gutenberg.org/cache/epub/84/pg84.rdf"


def parsed():
    text = (FIXTURES / "gutenberg_frankenstein.rdf").read_text(encoding="utf-8")
    return parse_subjects(text, "84", SOURCE_URL)


def test_subject_headings_are_extracted() -> None:
    metadata = parsed()

    assert "Horror tales" in metadata.subjects
    assert "Science fiction" in metadata.subjects
    assert "Gothic fiction" in metadata.subjects
    assert "Monsters -- Fiction" in metadata.subjects


def test_classification_letters_are_kept_apart_from_subjects() -> None:
    """A shelving class is not a subject heading and must not map as one."""
    metadata = parsed()

    assert "PR" in metadata.lcc_classes
    assert "PR" not in metadata.subjects
    assert all(len(code) <= 3 and code.isupper() for code in metadata.lcc_classes)


def test_provenance_is_carried_on_the_result() -> None:
    metadata = parsed()

    assert metadata.ebook_id == "84"
    assert metadata.source_url == SOURCE_URL


def test_parsing_is_stable_and_deduplicated() -> None:
    """A re-fetch of an unchanged record must produce an identical result."""
    first, second = parsed(), parsed()

    assert first.subjects == second.subjects
    assert len(first.subjects) == len(set(first.subjects))


def test_xml_entities_are_decoded() -> None:
    rdf = """<rdf:RDF><dcterms:subject><rdf:Description>
        <rdf:value>Crime &amp; punishment</rdf:value>
    </rdf:Description></dcterms:subject></rdf:RDF>"""

    assert parse_subjects(rdf, "1", SOURCE_URL).subjects == ["Crime & punishment"]


@pytest.mark.parametrize(
    "rdf",
    ["", "<rdf:RDF></rdf:RDF>", "<rdf:RDF><dcterms:title>No subjects</dcterms:title></rdf:RDF>"],
)
def test_a_record_with_no_subjects_yields_nothing(rdf: str) -> None:
    """A book with no catalogued subjects gets no concepts, not invented ones."""
    metadata = parse_subjects(rdf, "1", SOURCE_URL)

    assert metadata.subjects == []
    assert metadata.lcc_classes == []


def test_no_book_text_is_read_from_the_record() -> None:
    """Only subject values are extracted; nothing else in the record is used."""
    rdf = """<rdf:RDF>
        <pgterms:file rdf:about="https://www.gutenberg.org/files/84/84-0.txt"/>
        <dcterms:description>Full text of the novel goes here</dcterms:description>
        <dcterms:subject><rdf:Description><rdf:value>Horror tales</rdf:value>
        </rdf:Description></dcterms:subject>
    </rdf:RDF>"""

    metadata = parse_subjects(rdf, "84", SOURCE_URL)
    assert metadata.subjects == ["Horror tales"]
