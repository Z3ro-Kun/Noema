from pathlib import Path

import pytest

from app.services.ingestion.literature import (
    MalformedSourceError,
    PlainTextLiteratureAdapter,
    strip_gutenberg_boilerplate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def build_adapter(filename: str, **overrides) -> PlainTextLiteratureAdapter:
    defaults = dict(
        text=(FIXTURES / filename).read_text(encoding="utf-8"),
        title="The Lantern Keeper",
        source_ref="test-001",
        author="A Test Author",
        source_url="https://example.invalid/lantern",
    )
    return PlainTextLiteratureAdapter(**{**defaults, **overrides})


def test_chapters_become_containers_and_paragraphs_become_content_units() -> None:
    work = build_adapter("chaptered_work.txt").load()

    chapters = [c for c in work.containers if c.container_type == "chapter"]
    assert [c.sequence_number for c in chapters] == [1, 2]
    assert [c.title for c in chapters] == ["The Harbour", "The Storm"]

    first_chapter = chapters[0]
    assert len(first_chapter.content_units) == 2
    assert all(unit.unit_type == "passage" for unit in first_chapter.content_units)
    assert [unit.sequence_number for unit in first_chapter.content_units] == [1, 2]


def test_hard_wrapped_paragraphs_are_flattened_into_single_passages() -> None:
    work = build_adapter("chaptered_work.txt").load()
    first_passage = work.containers[1].content_units[0].text_content

    assert "\n" not in first_passage
    assert first_passage.startswith("The lantern keeper woke before the gulls did")
    assert first_passage.endswith("without counting the steps.")


def test_gutenberg_boilerplate_is_excluded_from_ingested_text() -> None:
    work = build_adapter("chaptered_work.txt").load()
    all_text = " ".join(
        unit.text_content for c in work.containers for unit in c.content_units
    )

    assert "PROJECT GUTENBERG" not in all_text.upper()
    assert "trailing licence boilerplate" not in all_text
    assert work.extra_metadata["provenance"]["gutenberg_boilerplate_stripped"] is True


def test_text_before_the_first_chapter_is_preserved_not_dropped() -> None:
    work = build_adapter("chaptered_work.txt").load()
    front = [c for c in work.containers if c.container_type == "front_matter"]

    assert len(front) == 1
    assert front[0].sequence_number == 0
    assert any("testing Noema" in unit.text_content for unit in front[0].content_units)


def test_source_without_chapters_gets_one_container_not_invented_divisions() -> None:
    work = build_adapter("unchaptered_work.txt", title="Untitled Fragment").load()

    assert len(work.containers) == 1
    container = work.containers[0]
    assert container.container_type == "text"
    assert container.extra_metadata["chapter_headings_detected"] is False
    assert len(container.content_units) == 2
    assert work.extra_metadata["structure"]["chapter_headings_detected"] is False


def test_provenance_is_recorded_for_traceability() -> None:
    work = build_adapter("chaptered_work.txt").load()
    provenance = work.extra_metadata["provenance"]

    assert work.source == "gutenberg"
    assert work.source_ref == "test-001"
    assert work.external_ids == {"source_ref": "test-001"}
    assert provenance["source_url"] == "https://example.invalid/lantern"
    assert provenance["adapter"] == "literature.plain_text"
    assert len(provenance["content_sha256"]) == 64
    assert provenance["ingested_at"].endswith("+00:00")


def test_creator_is_carried_through_from_the_source() -> None:
    work = build_adapter("chaptered_work.txt").load()

    assert [(c.name, c.role) for c in work.creators] == [("A Test Author", "author")]


def test_literature_adapter_targets_the_literature_domain() -> None:
    assert build_adapter("chaptered_work.txt").domain_slug == "literature"


@pytest.mark.parametrize("text", ["", "   \n\n  \n"])
def test_empty_source_is_rejected_rather_than_silently_ingested(text: str) -> None:
    adapter = PlainTextLiteratureAdapter(text=text, title="Nothing", source_ref="empty")

    with pytest.raises(MalformedSourceError):
        adapter.load()


def test_headings_with_no_body_text_are_rejected() -> None:
    adapter = PlainTextLiteratureAdapter(
        text="CHAPTER I.\n\nCHAPTER II.\n", title="Headings Only", source_ref="headings"
    )

    with pytest.raises(MalformedSourceError):
        adapter.load()


def test_strip_boilerplate_leaves_non_gutenberg_text_untouched() -> None:
    text = "Just a plain text with no markers."

    assert strip_gutenberg_boilerplate(text) == (text, False)


TOC_THEN_BODY = """CHAPTER I.
The First
CHAPTER II.
The Second

CHAPTER I.
The First

Actual prose belonging to the first chapter.

CHAPTER II.
The Second

Actual prose belonging to the second chapter.
"""


def test_table_of_contents_does_not_become_a_phantom_chapter() -> None:
    """A TOC is a heading immediately followed by another heading, with no body."""
    work = PlainTextLiteratureAdapter(
        text=TOC_THEN_BODY, title="Contents Test", source_ref="toc"
    ).load()

    chapters = [c for c in work.containers if c.container_type == "chapter"]
    assert len(chapters) == 2
    assert all(c.content_units for c in chapters)
    assert work.extra_metadata["structure"]["empty_headings_dropped"] == 1


def test_chapter_numbers_come_from_the_source_not_our_position() -> None:
    """A dropped TOC entry must not shift the real chapters' numbering."""
    work = PlainTextLiteratureAdapter(
        text=TOC_THEN_BODY, title="Contents Test", source_ref="toc"
    ).load()

    chapters = [c for c in work.containers if c.container_type == "chapter"]
    assert [c.sequence_number for c in chapters] == [1, 2]
    assert [c.extra_metadata["source_number"] for c in chapters] == [1, 2]


def test_arabic_and_roman_chapter_numbers_are_both_understood() -> None:
    text = "Chapter 4\n\nFourth.\n\nCHAPTER XIV.\n\nFourteenth.\n"

    work = PlainTextLiteratureAdapter(text=text, title="Numbers", source_ref="nums").load()

    assert [c.sequence_number for c in work.containers] == [4, 14]
