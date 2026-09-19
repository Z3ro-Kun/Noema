"""AniList manga/manhwa adapter tests. Fixture-driven -- these never touch the network."""

import json
from pathlib import Path

import pytest

from app.services.ingestion.manga import AniListMangaAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def load_media(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def vinland():
    """A Japanese manga: 29 volumes, 224 chapters."""
    return AniListMangaAdapter(media=load_media("anilist_manga_vinland_saga.json")).load()


@pytest.fixture
def solo_leveling():
    """A Korean manhwa, which AniList still types as MANGA."""
    return AniListMangaAdapter(media=load_media("anilist_manga_solo_leveling.json")).load()


# --- work normalization -------------------------------------------------


def test_work_is_normalized_into_the_comics_domain(vinland) -> None:
    assert vinland.domain_slug == "manhwa"
    assert vinland.source == "anilist"
    assert vinland.source_ref == "30642"
    assert vinland.title == "Vinland Saga"
    assert vinland.original_title == "ヴィンランド・サガ"
    assert vinland.description and "thorfinn" in vinland.description.lower()


def test_manga_and_manhwa_share_one_domain(vinland, solo_leveling) -> None:
    """AniList draws no type-level line between them, so neither does the domain."""
    assert vinland.domain_slug == solo_leveling.domain_slug == "manhwa"


def test_comic_tradition_is_recorded_from_country_of_origin(vinland, solo_leveling) -> None:
    """AniList reports both as format MANGA; countryOfOrigin is what separates them."""
    assert vinland.extra_metadata["anilist"]["format"] == "MANGA"
    assert solo_leveling.extra_metadata["anilist"]["format"] == "MANGA"

    assert vinland.extra_metadata["anilist"]["country_of_origin"] == "JP"
    assert vinland.extra_metadata["anilist"]["comic_tradition"] == "manga"
    assert solo_leveling.extra_metadata["anilist"]["country_of_origin"] == "KR"
    assert solo_leveling.extra_metadata["anilist"]["comic_tradition"] == "manhwa"


def test_unknown_country_leaves_the_tradition_unstated() -> None:
    """An unmapped country is left null rather than defaulted to 'manga'."""
    work = AniListMangaAdapter(
        media={"id": 7, "title": {"romaji": "Somewhere Else"}, "countryOfOrigin": "US"}
    ).load()

    assert work.extra_metadata["anilist"]["country_of_origin"] == "US"
    assert work.extra_metadata["anilist"]["comic_tradition"] is None


def test_title_variants_are_preserved_without_becoming_separate_works(solo_leveling) -> None:
    titles = solo_leveling.extra_metadata["anilist"]["titles"]

    assert titles["romaji"] == "Na Honjaman Level Up"
    assert titles["english"] == "Solo Leveling"
    assert titles["native"] == "나 혼자만 레벨업"
    assert solo_leveling.title == "Na Honjaman Level Up"


def test_anilist_identifiers_are_preserved(vinland) -> None:
    assert vinland.external_ids["anilist_id"] == 30642
    assert vinland.external_ids["source_ref"] == "30642"
    assert vinland.external_ids["mal_id"] == 642


def test_publication_metadata_is_kept_as_source_facts(vinland) -> None:
    anilist = vinland.extra_metadata["anilist"]

    assert anilist["status"] == "FINISHED"
    assert anilist["chapter_count"] == 224
    assert anilist["volume_count"] == 29
    assert anilist["start_date"] == "2005-04-27"
    assert anilist["end_date"] == "2025-07-25"


def test_genres_and_tags_are_recorded_as_source_facts(vinland) -> None:
    anilist = vinland.extra_metadata["anilist"]

    assert "Adventure" in anilist["genres"]
    assert all("name" in tag and "rank" in tag for tag in anilist["tags"])


def test_description_html_is_stripped(vinland) -> None:
    assert "<br>" not in vinland.description
    assert "<i>" not in vinland.description


# --- volumes / containers -----------------------------------------------


def test_every_volume_becomes_a_container(vinland) -> None:
    assert len(vinland.containers) == 29
    assert all(c.container_type == "volume" for c in vinland.containers)
    assert [c.sequence_number for c in vinland.containers] == list(range(1, 30))


def test_volume_containers_carry_their_number_and_no_invented_title(vinland) -> None:
    first = vinland.containers[0]

    assert first.title is None
    assert first.extra_metadata["volume_number"] == 1


def test_chapters_are_counted_not_turned_into_containers(vinland) -> None:
    """224 chapters, 29 containers: the chapter count is a fact, not structure.

    AniList gives chapter counts but no chapter titles, boundaries or text, so
    hundreds of empty chapter containers would assert a structure the source
    never described.
    """
    assert vinland.extra_metadata["anilist"]["chapter_count"] == 224
    assert len(vinland.containers) == 29
    assert not any(c.container_type == "chapter" for c in vinland.containers)


def test_no_content_units_are_ever_fabricated(vinland, solo_leveling) -> None:
    """AniList has no chapter text; nothing may stand in for it."""
    for work in (vinland, solo_leveling):
        assert work.content_unit_count == 0
        assert all(container.content_units == [] for container in work.containers)
        assert all(
            container.extra_metadata["has_source_text"] is False for container in work.containers
        )


def test_absence_of_chapter_text_is_recorded_explicitly(vinland) -> None:
    structure = vinland.extra_metadata["structure"]

    assert structure["containers"] == 29
    assert structure["content_units"] == 0
    assert structure["content_units_available"] is False
    assert "no chapter text" in structure["content_units_unavailable_reason"]


def test_unknown_volume_count_produces_no_containers() -> None:
    """Uncollected or ongoing series: assert no structure rather than guess."""
    work = AniListMangaAdapter(
        media={"id": 8, "title": {"romaji": "Ongoing"}, "chapters": 400, "volumes": None}
    ).load()

    assert work.containers == []
    assert work.extra_metadata["anilist"]["volume_count"] is None
    assert work.extra_metadata["structure"]["containers"] == 0


# --- creators and entities ----------------------------------------------


def test_staff_become_creators_with_their_source_role(vinland) -> None:
    assert vinland.creators
    assert any(c.name == "Makoto Yukimura" and c.role == "Story & Art" for c in vinland.creators)
    assert all(c.external_ids.get("anilist_staff_id") for c in vinland.creators)
    assert all(len(c.role) <= 64 for c in vinland.creators)


def test_long_staff_roles_are_truncated_to_the_column_width() -> None:
    work = AniListMangaAdapter(
        media={
            "id": 9,
            "title": {"romaji": "Credited"},
            "staff": {"edges": [{"role": "R" * 200, "node": {"id": 1, "name": {"full": "Someone"}}}]},
        }
    ).load()

    assert len(work.creators[0].role) == 64


def test_characters_become_work_scoped_entities(solo_leveling) -> None:
    characters = [e for e in solo_leveling.entities if e.entity_type == "character"]

    assert any(e.name == "Jin-U Seong" for e in characters)
    protagonist = next(e for e in characters if e.name == "Jin-U Seong")
    assert protagonist.extra_metadata["role"] == "MAIN"
    assert protagonist.external_ids["anilist_character_id"]


# --- source relations ---------------------------------------------------


def test_anilist_relations_are_emitted_by_source_coordinates(vinland) -> None:
    """Targets are named by source ref, since they may not be ingested."""
    predicates = {(r.predicate, r.target_source_ref) for r in vinland.relations}

    assert ("adaptation", "101348") in predicates
    assert all(r.target_source == "anilist" for r in vinland.relations)


def test_relations_across_media_types_are_kept(vinland) -> None:
    """The anime adaptation is a fact AniList asserts about the manga."""
    anime = [r for r in vinland.relations if r.extra_metadata["target_media_type"] == "ANIME"]

    assert anime
    assert any(r.predicate == "adaptation" for r in anime)
    assert any(r.extra_metadata["target_title"] for r in anime)


# --- provenance ---------------------------------------------------------


def test_provenance_identifies_adapter_and_source(vinland) -> None:
    provenance = vinland.extra_metadata["provenance"]

    assert provenance["adapter"] == "manga.anilist"
    assert provenance["source_name"] == "anilist"
    assert provenance["source_ref"] == "30642"
    assert provenance["source_url"] == "https://anilist.co/manga/30642"
    assert provenance["ingested_at"].endswith("+00:00")


def test_provenance_states_that_no_chapter_content_is_retrieved(vinland) -> None:
    """The rights position is recorded on the work, not only in a docstring."""
    note = vinland.extra_metadata["provenance"]["license_note"]

    assert "AniList" in note
    assert "No chapter text, scans, or artwork" in note


# --- missing / malformed data -------------------------------------------


def test_payload_without_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="no id"):
        AniListMangaAdapter(media={"title": {"romaji": "x"}}).load()


def test_payload_without_any_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="no usable title"):
        AniListMangaAdapter(media={"id": 1, "title": {}}).load()


def test_missing_mal_id_is_simply_absent() -> None:
    work = AniListMangaAdapter(media={"id": 10, "title": {"romaji": "No MAL"}}).load()

    assert "mal_id" not in work.external_ids
    assert work.external_ids["anilist_id"] == 10


def test_completely_empty_optional_blocks_do_not_crash() -> None:
    """AniList omits whole blocks for sparse entries; None must be tolerated."""
    work = AniListMangaAdapter(
        media={
            "id": 11,
            "title": {"romaji": "Minimal"},
            "volumes": 2,
            "staff": None,
            "characters": None,
            "relations": None,
            "tags": None,
            "genres": None,
        }
    ).load()

    assert work.title == "Minimal"
    assert len(work.containers) == 2
    assert work.creators == [] and work.entities == [] and work.relations == []
    assert work.extra_metadata["anilist"]["genres"] == []
