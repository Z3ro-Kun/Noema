"""CLI wiring for manga ingestion. No network, no database.

These cover the joins between pieces that are individually tested elsewhere:
which AniList query a manga fetch sends, and which parser / container type the
summary CLI selects for `--content volumes`. Both are places where a wrong
default would silently do the anime thing to a manga.
"""

import pytest

from app.services.ingestion import wikipedia
from app.services.ingestion.anilist_client import MANGA_QUERY, MEDIA_QUERY, AniListClient
from scripts.ingest_manga import parse_args as manga_parse_args
from scripts.ingest_wikipedia_summaries import CONTENT_MODES
from scripts.ingest_wikipedia_summaries import parse_args as summary_parse_args


# --- the AniList query ---------------------------------------------------


def test_manga_query_asks_for_the_manga_media_type() -> None:
    assert "type: MANGA" in MANGA_QUERY
    assert "type: ANIME" not in MANGA_QUERY


def test_manga_query_requests_the_fields_the_adapter_maps() -> None:
    for field in ("chapters", "volumes", "countryOfOrigin", "staff", "characters", "relations"):
        assert field in MANGA_QUERY


def test_manga_query_requests_no_episode_fields() -> None:
    """Episodes and streaming links are meaningless for a manga."""
    for field in ("episodes", "streamingEpisodes", "season"):
        assert field not in MANGA_QUERY


def test_fetch_manga_sends_the_manga_query_not_the_anime_one(monkeypatch) -> None:
    sent = {}

    def capture(self, anilist_id, query):
        sent["id"], sent["query"] = anilist_id, query
        return {"id": anilist_id}

    monkeypatch.setattr(AniListClient, "_fetch", capture)

    AniListClient().fetch_manga(30642)
    assert sent == {"id": 30642, "query": MANGA_QUERY}

    AniListClient().fetch_media(1)
    assert sent["query"] == MEDIA_QUERY


# --- ingest_manga arguments ----------------------------------------------


def test_manga_cli_accepts_several_ids() -> None:
    args = manga_parse_args(["30642", "105398"])

    assert args.anilist_ids == [30642, 105398]
    assert args.delay == 1.0


def test_manga_cli_requires_at_least_one_id() -> None:
    with pytest.raises(SystemExit):
        manga_parse_args([])


# --- summary CLI content modes -------------------------------------------


def test_summary_cli_defaults_to_episodes() -> None:
    """Existing invocations keep their meaning; volumes are opt-in."""
    args = summary_parse_args(["--source-ref", "1"])

    assert args.content == "episodes"


def test_volumes_mode_selects_the_volume_parser_and_container_type() -> None:
    container_type, candidates, parse, label = CONTENT_MODES["volumes"]

    assert container_type == "volume"
    assert parse is wikipedia.parse_volume_list
    assert candidates is wikipedia.volume_list_candidates
    assert label == "vol"


def test_episodes_mode_is_unchanged() -> None:
    container_type, candidates, parse, label = CONTENT_MODES["episodes"]

    assert container_type == "episode"
    assert parse is wikipedia.parse_episode_list
    assert candidates is wikipedia.episode_list_candidates


def test_unknown_content_mode_is_rejected() -> None:
    with pytest.raises(SystemExit):
        summary_parse_args(["--source-ref", "1", "--content", "chapters"])
