"""AniList adapter tests. Fixture-driven -- these never touch the network."""

import json
from pathlib import Path

import pytest

from app.services.ingestion.anime import AniListAnimeAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def load_media(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def bebop():
    return AniListAnimeAdapter(media=load_media("anilist_cowboy_bebop.json")).load()


@pytest.fixture
def movie():
    return AniListAnimeAdapter(media=load_media("anilist_cowboy_bebop_movie.json")).load()


@pytest.fixture
def sparse():
    return AniListAnimeAdapter(media=load_media("anilist_sparse.json")).load()


# --- how a title is chosen ------------------------------------------------


def test_the_english_title_is_the_one_shown(bebop) -> None:
    """Phase 1AB: english -> romaji -> native, not romaji first."""
    titles = bebop.extra_metadata["anilist"]["titles"]

    assert titles["english"] == "Cowboy Bebop"
    assert bebop.title == titles["english"]


def test_the_title_falls_back_through_the_variants() -> None:
    media = load_media("anilist_cowboy_bebop.json")

    media["title"]["english"] = None
    assert AniListAnimeAdapter(media=media).load().title == "Cowboy Bebop"

    media["title"]["romaji"] = None
    assert AniListAnimeAdapter(media=media).load().title == "カウボーイビバップ"


def test_a_payload_with_no_title_at_all_is_refused() -> None:
    media = load_media("anilist_cowboy_bebop.json")
    media["title"] = {"english": None, "romaji": None, "native": None}

    with pytest.raises(ValueError):
        AniListAnimeAdapter(media=media).load()


def test_the_native_title_survives_the_choice(bebop) -> None:
    assert bebop.original_title == "カウボーイビバップ"


# --- work normalization -------------------------------------------------


def test_work_is_normalized_into_the_anime_domain(bebop) -> None:
    assert bebop.domain_slug == "anime"
    assert bebop.source == "anilist"
    assert bebop.source_ref == "1"
    assert bebop.title == "Cowboy Bebop"
    assert bebop.original_title == "カウボーイビバップ"
    assert bebop.description and "bounty" in bebop.description.lower()


def test_title_variants_are_preserved_without_becoming_separate_works(bebop) -> None:
    titles = bebop.extra_metadata["anilist"]["titles"]

    assert titles["romaji"] == "Cowboy Bebop"
    assert titles["english"] == "Cowboy Bebop"
    assert titles["native"] == "カウボーイビバップ"


def test_anilist_identifiers_are_preserved(bebop) -> None:
    assert bebop.external_ids["anilist_id"] == 1
    assert bebop.external_ids["source_ref"] == "1"
    assert bebop.external_ids["mal_id"] == 1


def test_broadcast_metadata_is_kept_as_source_facts(bebop) -> None:
    anilist = bebop.extra_metadata["anilist"]

    assert anilist["format"] == "TV"
    assert anilist["season"] == "SPRING"
    assert anilist["season_year"] == 1998
    assert anilist["episode_count"] == 26
    assert anilist["start_date"] == "1998-04-03"


def test_genres_and_tags_are_recorded_as_source_facts(bebop) -> None:
    anilist = bebop.extra_metadata["anilist"]

    assert "Action" in anilist["genres"]
    assert all("name" in tag and "rank" in tag for tag in anilist["tags"])


def test_description_html_is_stripped(bebop) -> None:
    assert "<br>" not in bebop.description
    assert "<i>" not in bebop.description


# --- episodes / containers ----------------------------------------------


def test_every_episode_becomes_a_container(bebop) -> None:
    assert len(bebop.containers) == 26
    assert all(c.container_type == "episode" for c in bebop.containers)
    assert [c.sequence_number for c in bebop.containers] == list(range(1, 27))


def test_episode_titles_come_from_streaming_metadata_when_matchable(bebop) -> None:
    first = bebop.containers[0]

    assert first.title == "Asteroid Blues"
    assert first.extra_metadata["title_source"] == "anilist_streaming_episode"


def test_episodes_without_streaming_titles_stay_untitled(movie) -> None:
    """The movie has one episode and no streaming entries -- no invented title."""
    assert len(movie.containers) == 1
    container = movie.containers[0]

    assert container.title is None
    assert container.extra_metadata["title_source"] is None
    assert container.sequence_number == 1


def test_no_content_units_are_ever_fabricated(bebop, movie) -> None:
    """AniList has no episode text; nothing may stand in for it."""
    for work in (bebop, movie):
        assert work.content_unit_count == 0
        assert all(container.content_units == [] for container in work.containers)
        assert all(
            container.extra_metadata["has_source_text"] is False for container in work.containers
        )


def test_absence_of_episode_text_is_recorded_explicitly(bebop) -> None:
    structure = bebop.extra_metadata["structure"]

    assert structure["content_units"] == 0
    assert structure["content_units_available"] is False
    assert "no episode text" in structure["content_units_unavailable_reason"]


def test_synopsis_is_work_description_not_a_content_unit(bebop) -> None:
    """A synopsis is metadata about the work, not dialogue inside an episode."""
    assert bebop.description is not None
    assert bebop.content_unit_count == 0


# --- creators and entities ----------------------------------------------


def test_studios_become_creators(bebop) -> None:
    studios = [c for c in bebop.creators if c.role in ("studio", "production_company")]

    assert any(c.name == "Sunrise" and c.role == "studio" for c in studios)
    assert all(c.external_ids.get("anilist_studio_id") for c in studios)


def test_staff_become_creators_with_their_source_role(bebop) -> None:
    staff = [c for c in bebop.creators if c.role not in ("studio", "production_company")]

    assert staff
    assert all(c.external_ids.get("anilist_staff_id") for c in staff)
    assert all(len(c.role) <= 64 for c in staff)


def test_characters_become_work_scoped_entities(bebop) -> None:
    characters = [e for e in bebop.entities if e.entity_type == "character"]

    assert any(e.name == "Spike Spiegel" for e in characters)
    spike = next(e for e in characters if e.name == "Spike Spiegel")
    assert spike.extra_metadata["role"] == "MAIN"
    assert spike.external_ids["anilist_character_id"]


# --- source relations ---------------------------------------------------


def test_anilist_relations_are_emitted_by_source_coordinates(bebop) -> None:
    """Targets are named by source ref, since they may not be ingested."""
    predicates = {(r.predicate, r.target_source_ref) for r in bebop.relations}

    assert ("side_story", "5") in predicates
    assert all(r.target_source == "anilist" for r in bebop.relations)


def test_relations_to_other_media_types_are_kept(bebop) -> None:
    """A manga adaptation is still a fact AniList asserts."""
    manga = [r for r in bebop.relations if r.extra_metadata["target_media_type"] == "MANGA"]

    assert manga
    assert any(r.predicate == "adaptation" for r in manga)


# --- provenance ---------------------------------------------------------


def test_provenance_identifies_adapter_and_source(bebop) -> None:
    provenance = bebop.extra_metadata["provenance"]

    assert provenance["adapter"] == "anime.anilist"
    assert provenance["source_name"] == "anilist"
    assert provenance["source_ref"] == "1"
    assert provenance["source_url"] == "https://anilist.co/anime/1"
    assert "AniList" in provenance["license_note"]
    assert provenance["ingested_at"].endswith("+00:00")


# --- missing / partial data ---------------------------------------------


def test_sparse_payload_falls_back_to_an_available_title(sparse) -> None:
    assert sparse.title == "テスト作品"
    assert sparse.original_title == "テスト作品"
    assert sparse.description is None


def test_unknown_episode_count_produces_no_containers(sparse) -> None:
    """We assert no episodes rather than guessing at an unaired show."""
    assert sparse.containers == []
    assert sparse.extra_metadata["anilist"]["episode_count"] is None


def test_sparse_payload_produces_no_creators_entities_or_relations(sparse) -> None:
    assert sparse.creators == []
    assert sparse.entities == []
    assert sparse.relations == []


def test_missing_mal_id_is_simply_absent(sparse) -> None:
    assert "mal_id" not in sparse.external_ids
    assert sparse.external_ids["anilist_id"] == 999999


def test_partial_dates_are_formatted_to_the_precision_given(sparse) -> None:
    assert sparse.extra_metadata["anilist"]["start_date"] == "2027"
    assert sparse.extra_metadata["anilist"]["end_date"] is None


def test_payload_without_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="no id"):
        AniListAnimeAdapter(media={"title": {"romaji": "x"}}).load()


def test_payload_without_any_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="no usable title"):
        AniListAnimeAdapter(media={"id": 1, "title": {}}).load()


def test_completely_empty_optional_blocks_do_not_crash() -> None:
    """AniList omits whole blocks for sparse entries; None must be tolerated."""
    work = AniListAnimeAdapter(
        media={
            "id": 42,
            "title": {"romaji": "Minimal"},
            "episodes": 2,
            "studios": None,
            "staff": None,
            "characters": None,
            "relations": None,
            "streamingEpisodes": None,
            "tags": None,
            "genres": None,
        }
    ).load()

    assert work.title == "Minimal"
    assert len(work.containers) == 2
    assert work.creators == [] and work.entities == [] and work.relations == []
