"""Tests for the literature CLI's source-text resolution.

These exist because of a real bug: a Gutenberg edition that uses CRLF line
endings was written back with newline translation on, turning every "\\n"
into "\\r\\r\\n". Read back with universal newlines, each of those became two
line breaks -- so every *line* became its own paragraph and one work ingested
as 6,371 content units instead of 773. Alice's edition happened to use LF,
which is why it went unnoticed across five phases.
"""

import httpx
import pytest

from scripts.ingest_literature import resolve_source_text

CRLF_SOURCE = (
    "CHAPTER I.\r\n"
    "The Harbour\r\n"
    "\r\n"
    "The keeper woke before the gulls did, and climbed\r\n"
    "the stair without counting the steps.\r\n"
    "\r\n"
    "Below her the harbour was the colour of slate.\r\n"
)
LF_SOURCE = CRLF_SOURCE.replace("\r\n", "\n")


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def fake_download(monkeypatch):
    def install(text: str):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(text))

    return install


def paragraph_breaks(text: str) -> int:
    return text.count("\n\n")


def test_crlf_source_keeps_its_paragraph_structure(tmp_path, fake_download) -> None:
    """The regression: CRLF input must not double its line breaks."""
    fake_download(CRLF_SOURCE)
    target = tmp_path / "crlf.txt"

    resolved = resolve_source_text(target, "https://example.invalid/book.txt")

    # Two blank lines in the source, two after the round trip -- not four.
    assert paragraph_breaks(resolved) == 2
    assert "\r\r" not in target.read_bytes().decode("utf-8", "replace")


def test_crlf_and_lf_sources_resolve_identically(tmp_path, fake_download) -> None:
    fake_download(CRLF_SOURCE)
    from_crlf = resolve_source_text(tmp_path / "a.txt", "https://example.invalid/a.txt")

    fake_download(LF_SOURCE)
    from_lf = resolve_source_text(tmp_path / "b.txt", "https://example.invalid/b.txt")

    assert from_crlf == from_lf


def test_crlf_source_parses_into_the_expected_structure(tmp_path, fake_download) -> None:
    """End to end: the adapter sees paragraphs, not one unit per line."""
    from app.services.ingestion.literature import PlainTextLiteratureAdapter

    fake_download(CRLF_SOURCE)
    text = resolve_source_text(tmp_path / "book.txt", "https://example.invalid/book.txt")

    work = PlainTextLiteratureAdapter(text=text, title="Harbour", source_ref="crlf-1").load()

    assert work.content_unit_count == 2
    first = work.containers[0].content_units[0]
    # The wrapped lines rejoin into one passage rather than splitting.
    assert first.text_content.startswith("The keeper woke before the gulls did")
    assert first.text_content.endswith("without counting the steps.")


def test_an_existing_file_is_reused_without_downloading(tmp_path, monkeypatch) -> None:
    target = tmp_path / "already.txt"
    target.write_text("Already here.\n", encoding="utf-8")

    def explode(*args, **kwargs):
        raise AssertionError("must not download when the file already exists")

    monkeypatch.setattr(httpx, "get", explode)

    assert resolve_source_text(target, "https://example.invalid/x.txt") == "Already here.\n"


def test_missing_file_without_a_url_is_an_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_source_text(tmp_path / "nope.txt", None)
