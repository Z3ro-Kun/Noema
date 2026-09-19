"""Phase 1AA: cover art, from each work's own source.

`ProductWork.cover_image_url` existed from Phase 1Y and was null for every
work, because neither ingestion path asked its source for artwork. Nothing
about the product model changes here -- what changes is that the field now
holds something, taken from the record Noema was already reading:

    anime, manga, manhwa   AniList `Media.coverImage`
    literature             the `pgterms:file` image entry in the Gutenberg
                           catalogue record

What is under test:

    it is read, never built    both readers take a URL out of the record. No
                               CDN path is assembled from an id, so a work
                               whose source has no cover gets none rather
                               than a link to a 404.

    absence is an answer       no cover is an ordinary outcome and must not
                               raise, must not write a null, and must not
                               stop the rest of the record being ingested.

    nothing else moves         enrichment writes the URL and its provenance,
                               and leaves every other field of the record
                               exactly as it found it.

    it is repeatable           running it twice reports the second run as
                               unchanged and writes the same value.

    the provenance answers     "where did this cover come from?" is
                               answerable from the stored record alone.

Fixture-driven throughout. Nothing here touches the network.
"""

import json
from pathlib import Path

import pytest

from app.services.ingestion.anime import AniListAnimeAdapter, cover_image
from app.services.ingestion.gutenberg_client import parse_cover_image, parse_subjects
from app.services.ingestion.manga import AniListMangaAdapter
from scripts.enrich_covers import _apply

FIXTURES = Path(__file__).parent / "fixtures"

ANILIST_COVER = {
    "extraLarge": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1.png",
    "large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1.png",
    "medium": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/small/bx1.png",
}


def load_media(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def with_cover(name: str, cover: dict | None) -> dict:
    media = load_media(name)
    if cover is not None:
        media["coverImage"] = cover
    return media


class FakeWork:
    """Just the two attributes the enrichment script writes."""

    def __init__(self, extra_metadata: dict | None = None) -> None:
        self.extra_metadata = extra_metadata


@pytest.fixture(autouse=True)
def _no_flag_modified(monkeypatch):
    """`_apply` marks a real ORM attribute dirty; these works are not ORM rows."""
    monkeypatch.setattr("scripts.enrich_covers.flag_modified", lambda *_: None)


# --- AniList: reading the field --------------------------------------------


def test_the_largest_available_variant_is_chosen() -> None:
    url, provenance = cover_image({"coverImage": ANILIST_COVER})

    assert url == ANILIST_COVER["extraLarge"]
    assert provenance["source_field"] == "Media.coverImage.extraLarge"


def test_it_falls_back_through_the_sizes_anilist_actually_returned() -> None:
    url, provenance = cover_image(
        {"coverImage": {"extraLarge": None, "large": ANILIST_COVER["large"]}}
    )

    assert url == ANILIST_COVER["large"]
    assert provenance["source_field"] == "Media.coverImage.large"
    assert provenance["variants_available"] == ["large"]


def test_no_cover_image_block_is_not_an_error() -> None:
    assert cover_image({"id": 1}) == (None, None)


def test_an_empty_cover_block_yields_no_cover() -> None:
    assert cover_image({"coverImage": {}}) == (None, None)
    assert cover_image({"coverImage": None}) == (None, None)


def test_blank_and_non_string_urls_are_not_covers() -> None:
    """A source that returns "" or a number has not supplied a cover."""
    assert cover_image({"coverImage": {"extraLarge": "   ", "large": ""}}) == (None, None)
    assert cover_image({"coverImage": {"extraLarge": 12345}}) == (None, None)


def test_surrounding_whitespace_is_stripped() -> None:
    url, _ = cover_image({"coverImage": {"extraLarge": "  https://example.test/a.png "}})
    assert url == "https://example.test/a.png"


def test_the_provenance_names_the_source_and_its_terms() -> None:
    _, provenance = cover_image({"coverImage": ANILIST_COVER})

    assert "AniList" in provenance["license_note"]
    # The artwork is referenced, not copied, and the record has to say so.
    assert "never" in provenance["license_note"] or "not copied" in (
        provenance["license_note"].lower()
    )


# --- AniList: through the adapters -----------------------------------------


def test_the_anime_adapter_carries_the_cover_into_work_metadata() -> None:
    work = AniListAnimeAdapter(
        media=with_cover("anilist_cowboy_bebop.json", ANILIST_COVER)
    ).load()

    assert work.extra_metadata["cover_image_url"] == ANILIST_COVER["extraLarge"]
    assert (
        work.extra_metadata["provenance"]["cover_image"]["source_field"]
        == "Media.coverImage.extraLarge"
    )


def test_the_manga_adapter_carries_it_the_same_way() -> None:
    work = AniListMangaAdapter(
        media=with_cover("anilist_manga_vinland_saga.json", ANILIST_COVER)
    ).load()

    assert work.extra_metadata["cover_image_url"] == ANILIST_COVER["extraLarge"]
    assert "cover_image" in work.extra_metadata["provenance"]


@pytest.mark.parametrize(
    ("fixture", "adapter"),
    [
        ("anilist_cowboy_bebop.json", AniListAnimeAdapter),
        ("anilist_sparse.json", AniListAnimeAdapter),
        ("anilist_manga_vinland_saga.json", AniListMangaAdapter),
    ],
)
def test_a_payload_without_a_cover_ingests_unchanged(fixture, adapter) -> None:
    """The key is absent, not null: "never had one" stays distinguishable.

    These are the real fixtures, which carry no `coverImage` -- so this is
    also the regression test that adding the field broke no existing record.
    """
    work = adapter(media=load_media(fixture)).load()

    assert "cover_image_url" not in work.extra_metadata
    assert "cover_image" not in work.extra_metadata["provenance"]
    # And the rest of the record is still there.
    assert work.title
    assert work.extra_metadata["provenance"]["source_name"] == "anilist"


def test_adding_a_cover_changes_nothing_else_about_the_record() -> None:
    without = AniListAnimeAdapter(media=load_media("anilist_cowboy_bebop.json")).load()
    with_art = AniListAnimeAdapter(
        media=with_cover("anilist_cowboy_bebop.json", ANILIST_COVER)
    ).load()

    assert with_art.title == without.title
    assert with_art.source_ref == without.source_ref
    assert with_art.description == without.description
    assert len(with_art.containers) == len(without.containers)
    assert with_art.creators == without.creators
    assert with_art.entities == without.entities
    assert with_art.extra_metadata["anilist"] == without.extra_metadata["anilist"]
    assert with_art.extra_metadata["structure"] == without.extra_metadata["structure"]


# --- Gutenberg -------------------------------------------------------------


def test_the_cover_is_read_out_of_the_catalogue_record() -> None:
    rdf = (FIXTURES / "gutenberg_alice_cover.rdf").read_text(encoding="utf-8")
    url, source_field = parse_cover_image(rdf)

    assert url == "https://www.gutenberg.org/cache/epub/11/pg11.cover.medium.jpg"
    assert source_field == "pgterms:file.cover.medium."


def test_the_medium_cover_is_preferred_over_the_thumbnail() -> None:
    rdf = (FIXTURES / "gutenberg_alice_cover.rdf").read_text(encoding="utf-8")
    url, _ = parse_cover_image(rdf)

    assert ".cover.small." not in url


def test_non_image_files_are_never_mistaken_for_covers() -> None:
    rdf = (FIXTURES / "gutenberg_alice_cover.rdf").read_text(encoding="utf-8")
    url, _ = parse_cover_image(rdf)

    assert ".txt" not in url and ".epub" not in url


def test_a_record_with_no_cover_reports_none() -> None:
    """The real Frankenstein fixture lists no files at all."""
    rdf = (FIXTURES / "gutenberg_frankenstein.rdf").read_text(encoding="utf-8")

    assert parse_cover_image(rdf) == (None, None)


def test_malformed_and_empty_records_do_not_raise() -> None:
    for text in ("", "<rdf:RDF></rdf:RDF>", "<pgterms:file rdf:about=", "not xml at all"):
        assert parse_cover_image(text) == (None, None)


def test_reading_a_cover_does_not_disturb_the_subject_headings() -> None:
    """Subjects were what this parser was for; they must still come out."""
    rdf = (FIXTURES / "gutenberg_frankenstein.rdf").read_text(encoding="utf-8")
    metadata = parse_subjects(rdf, "84", "https://example.test/pg84.rdf")

    assert metadata.subjects
    assert metadata.cover_image_url is None
    assert metadata.cover_image_source_field is None


def test_a_record_with_a_cover_reports_both() -> None:
    rdf = (FIXTURES / "gutenberg_alice_cover.rdf").read_text(encoding="utf-8")
    metadata = parse_subjects(rdf, "11", "https://example.test/pg11.rdf")

    assert metadata.subjects == ["Fantasy fiction"]
    assert metadata.cover_image_url.endswith("pg11.cover.medium.jpg")


# --- enrichment: writing it onto an existing work --------------------------

PROVENANCE = {"source_field": "Media.coverImage.extraLarge", "license_note": "..."}


def test_enrichment_sets_the_url_and_its_provenance() -> None:
    work = FakeWork({"provenance": {"adapter": "anime.anilist"}})

    assert _apply(work, "https://example.test/a.png", PROVENANCE) == "set"
    assert work.extra_metadata["cover_image_url"] == "https://example.test/a.png"
    assert work.extra_metadata["provenance"]["cover_image"] == PROVENANCE


def test_enrichment_keeps_the_existing_provenance_block() -> None:
    """The cover goes *inside* the record's provenance, not beside it."""
    work = FakeWork({"provenance": {"adapter": "anime.anilist", "source_ref": "1"}})

    _apply(work, "https://example.test/a.png", PROVENANCE)

    assert work.extra_metadata["provenance"]["adapter"] == "anime.anilist"
    assert work.extra_metadata["provenance"]["source_ref"] == "1"


def test_enrichment_leaves_every_other_field_alone() -> None:
    before = {
        "provenance": {"adapter": "anime.anilist"},
        "structure": {"containers": 26, "content_units": 0},
        "anilist": {"genres": ["Action"], "average_score": 86},
    }
    work = FakeWork(dict(before))

    _apply(work, "https://example.test/a.png", PROVENANCE)

    assert work.extra_metadata["structure"] == before["structure"]
    assert work.extra_metadata["anilist"] == before["anilist"]


def test_running_enrichment_twice_reports_the_second_as_unchanged() -> None:
    work = FakeWork({"provenance": {}})
    url = "https://example.test/a.png"

    assert _apply(work, url, PROVENANCE) == "set"
    assert _apply(work, url, PROVENANCE) == "unchanged"
    assert work.extra_metadata["cover_image_url"] == url


def test_a_source_that_now_returns_a_different_url_is_reported_as_changed() -> None:
    work = FakeWork({"cover_image_url": "https://example.test/old.png", "provenance": {}})

    assert _apply(work, "https://example.test/new.png", PROVENANCE) == "changed"
    assert work.extra_metadata["cover_image_url"] == "https://example.test/new.png"


def test_no_cover_writes_nothing_at_all() -> None:
    """Not a null, not an empty string: the record is left as it was."""
    work = FakeWork({"provenance": {"adapter": "literature.gutenberg"}})

    assert _apply(work, None, None) == "none"
    assert "cover_image_url" not in work.extra_metadata
    assert "cover_image" not in work.extra_metadata["provenance"]


def test_a_work_with_no_metadata_at_all_can_still_be_enriched() -> None:
    work = FakeWork(None)

    assert _apply(work, "https://example.test/a.png", PROVENANCE) == "set"
    assert work.extra_metadata["cover_image_url"] == "https://example.test/a.png"
