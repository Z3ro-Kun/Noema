"""Summary ingestion against the real schema, inside rolled-back transactions.

Covers the behaviour that matters architecturally: summaries land on the
containers AniList already created, carry their provenance, never impersonate
primary text, and are refused outright when rights are unclear.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    TEXT_TIER_PRIMARY,
    TEXT_TIER_SUMMARY,
    Container,
    ContentUnit,
    TextSource,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.rights import assess_mediawiki_rights, unknown_rights
from app.services.ingestion.service import ingest_source_work
from app.services.ingestion.summary_service import (
    attach_episode_summaries,
    compute_content_hash,
    normalize_title,
    titles_agree,
)
from app.services.ingestion.wikipedia import parse_episode_list, parse_volume_list

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 940000

CC_BY_SA_4 = {
    "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
    "text": "Creative Commons Attribution-Share Alike 4.0",
}

PAGE_TITLE = "List of Test Series episodes"
PAGE_URL = "https://en.wikipedia.org/wiki/List_of_Test_Series_episodes"


def cc_rights(revision="987"):
    return assess_mediawiki_rights(
        CC_BY_SA_4, page_title=PAGE_TITLE, page_url=PAGE_URL, revision_ref=revision
    )


def wikitext() -> str:
    return (FIXTURES / "wikipedia_episode_list.wikitext").read_text(encoding="utf-8")


async def make_anime_work(session: AsyncSession, *, episode_titles: dict[int, str] | None = None):
    """An anime work whose episode containers mirror the fixture's episodes."""
    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["episodes"] = 3
    titles = episode_titles or {1: "The Departure", 2: "Salt and Iron", 3: "The Return"}
    media["streamingEpisodes"] = [
        {"title": f"Episode {number} - {title}", "site": "Test"} for number, title in titles.items()
    ]
    media["relations"] = {"edges": []}

    result = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
    return result.work_id


async def ingest_summaries(session, work_id, *, rights=None, revision="987", text=None):
    return await attach_episode_summaries(
        session,
        work_id=work_id,
        work_title="Test Series",
        parsed=parse_episode_list(text if text is not None else wikitext()),
        rights=rights if rights is not None else cc_rights(revision),
        page_title=PAGE_TITLE,
        page_url=PAGE_URL,
        revision_ref=revision,
        retrieved_text=text if text is not None else wikitext(),
    )


# --- attaching to existing structure -------------------------------------


async def test_summaries_attach_to_existing_episode_containers(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)

    before = (
        await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work_id)
        )
    ).scalar_one()

    report = await ingest_summaries(db_session, work_id)

    after = (
        await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work_id)
        )
    ).scalar_one()

    assert report.stored is True
    assert report.attached == 2
    # Wikipedia adds text; it must never add structure.
    assert before == after == 3


async def test_attached_units_are_summary_tier_with_a_source(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    units = (
        (
            await db_session.execute(
                select(ContentUnit)
                .join(Container, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
            )
        )
        .scalars()
        .all()
    )

    assert units
    for unit in units:
        assert unit.text_tier == TEXT_TIER_SUMMARY
        assert unit.text_source_id is not None
        assert unit.unit_type == "synopsis"


async def test_summary_text_is_the_parsed_prose(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    unit = (
        await db_session.execute(
            select(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == work_id, Container.sequence_number == 1)
        )
    ).scalar_one()

    assert unit.text_content.startswith("The crew of the Kestrel")
    assert "[[" not in unit.text_content


async def test_literature_units_remain_primary(db_session: AsyncSession) -> None:
    """The tier column must not disturb anything already ingested."""
    result = await ingest_source_work(
        db_session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref="tier-regression",
        ).load(),
    )

    tiers = (
        (
            await db_session.execute(
                select(ContentUnit.text_tier)
                .join(Container, ContentUnit.container_id == Container.id)
                .where(Container.work_id == result.work_id)
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    assert tiers == [TEXT_TIER_PRIMARY]


async def test_primary_and_summary_text_never_merge(db_session: AsyncSession) -> None:
    """Both tiers can coexist and stay separately queryable."""
    anime_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, anime_id)
    novel = await ingest_source_work(
        db_session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref="tier-separation",
        ).load(),
    )

    summaries = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == anime_id, ContentUnit.text_tier == TEXT_TIER_SUMMARY)
    )
    primaries = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == novel.work_id, ContentUnit.text_tier == TEXT_TIER_PRIMARY)
    )

    assert summaries.scalar_one() == 2
    assert primaries.scalar_one() > 0


# --- matching ------------------------------------------------------------


async def test_matching_uses_source_episode_numbers(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    rows = (
        (
            await db_session.execute(
                select(Container.sequence_number, ContentUnit.extra_metadata)
                .join(ContentUnit, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
                .order_by(Container.sequence_number)
            )
        )
        .all()
    )

    for sequence_number, metadata in rows:
        assert metadata["source_episode_number"] == sequence_number


async def test_episode_without_a_container_is_reported_not_attached(
    db_session: AsyncSession,
) -> None:
    work_id = await make_anime_work(db_session)
    # Drop episode 2's container so its summary has nowhere to go.
    container = (
        await db_session.execute(
            select(Container).where(
                Container.work_id == work_id, Container.sequence_number == 2
            )
        )
    ).scalar_one()
    await db_session.delete(container)
    await db_session.flush()

    report = await ingest_summaries(db_session, work_id)

    assert report.attached == 1
    assert [u["episode_number"] for u in report.unmatched] == [2]
    assert report.unmatched[0]["reason"] == "no_episode_container_with_that_number"


async def test_conflicting_titles_block_attachment(db_session: AsyncSession) -> None:
    """Same number, unrelated titles means the sources number differently."""
    work_id = await make_anime_work(
        db_session, episode_titles={1: "Something Else Entirely", 2: "Salt and Iron", 3: "x"}
    )

    report = await ingest_summaries(db_session, work_id)

    assert report.attached == 1
    assert [u["reason"] for u in report.unmatched] == ["title_conflict"]
    assert report.unmatched[0]["episode_number"] == 1


async def test_match_evidence_is_recorded_on_the_unit(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    unit = (
        await db_session.execute(
            select(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == work_id, Container.sequence_number == 1)
        )
    ).scalar_one()

    assert unit.extra_metadata["match_evidence"] in ("title_exact", "title_contained")
    assert unit.extra_metadata["source_episode_title"] == "The Departure"


def test_title_normalization_ignores_case_punctuation_and_accents() -> None:
    assert normalize_title("The Queen’s Croquet-Ground") == normalize_title("the queens croquet ground")
    assert titles_agree("Asteroid Blues", "asteroid blues")[0] is True
    assert titles_agree("Session 1: Asteroid Blues", "Asteroid Blues")[1] == "title_contained"
    assert titles_agree("Asteroid Blues", "Stray Dog Strut")[0] is False


def test_absent_titles_are_not_treated_as_conflict() -> None:
    agrees, evidence = titles_agree(None, "Deep Water")

    assert agrees is True
    assert evidence == "title_unavailable"


# --- provenance ----------------------------------------------------------


async def test_provenance_row_records_url_revision_hash_and_licence(
    db_session: AsyncSession,
) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    source = (
        await db_session.execute(
            select(TextSource).where(TextSource.source_ref == PAGE_TITLE)
        )
    ).scalars().first()

    assert source.source_name == "wikipedia"
    assert source.source_url == PAGE_URL
    assert source.revision_ref == "987"
    assert source.content_hash == compute_content_hash(wikitext())
    assert len(source.content_hash) == 64
    assert source.retrieved_at is not None


async def test_provenance_row_records_licence_and_attribution(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id)

    source = (
        await db_session.execute(select(TextSource).where(TextSource.source_ref == PAGE_TITLE))
    ).scalars().first()

    assert source.licence == "CC-BY-SA-4.0"
    assert source.licence_url == CC_BY_SA_4["url"]
    assert source.requires_attribution is True
    assert source.share_alike is True
    assert source.permits_storage is True
    assert "Wikipedia contributors" in source.attribution_text
    assert source.rights_basis


async def test_content_hash_ignores_line_ending_churn() -> None:
    assert compute_content_hash("a\r\nb") == compute_content_hash("a\nb")
    assert compute_content_hash("a\nb") != compute_content_hash("a\nc")


# --- idempotency ---------------------------------------------------------


async def test_reingesting_the_same_revision_changes_nothing(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    first = await ingest_summaries(db_session, work_id)
    second = await ingest_summaries(db_session, work_id)

    assert first.attached == 2
    assert second.attached == 0
    assert second.unchanged == 2

    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == work_id)
    )
    sources = await db_session.execute(
        select(func.count()).select_from(TextSource).where(TextSource.source_ref == PAGE_TITLE)
    )
    assert units.scalar_one() == 2
    assert sources.scalar_one() == 1


async def test_a_new_revision_supersedes_the_text_and_keeps_history(
    db_session: AsyncSession,
) -> None:
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id, revision="987")

    edited = wikitext().replace("carrying a cargo", "carrying a different cargo")
    report = await ingest_summaries(db_session, work_id, revision="1000", text=edited)

    assert report.superseded == 2
    assert report.attached == 0

    # One unit per episode still, now pointing at the newer source.
    units = (
        (
            await db_session.execute(
                select(ContentUnit)
                .join(Container, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(units) == 2
    assert any("different cargo" in u.text_content for u in units)

    # Both retrievals remain inspectable.
    sources = (
        (
            await db_session.execute(
                select(TextSource).where(TextSource.source_ref == PAGE_TITLE)
            )
        )
        .scalars()
        .all()
    )
    assert {s.revision_ref for s in sources} == {"987", "1000"}


async def test_unchanged_content_under_a_new_revision_id_is_a_new_version(
    db_session: AsyncSession,
) -> None:
    """Identity includes the revision, so a re-edit-to-identical is still tracked."""
    work_id = await make_anime_work(db_session)
    await ingest_summaries(db_session, work_id, revision="987")
    report = await ingest_summaries(db_session, work_id, revision="988")

    assert report.superseded == 2


# --- rights gating -------------------------------------------------------


async def test_unknown_rights_persist_nothing(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)

    report = await ingest_summaries(
        db_session, work_id, rights=unknown_rights("licence could not be established")
    )

    assert report.stored is False
    assert "could not be established" in report.not_stored_reason

    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == work_id)
    )
    sources = await db_session.execute(
        select(func.count()).select_from(TextSource).where(TextSource.source_ref == PAGE_TITLE)
    )
    # Not even a provenance row: a refusal leaves no trace of the text.
    assert units.scalar_one() == 0
    assert sources.scalar_one() == 0


async def test_unrecognised_licence_persists_nothing(db_session: AsyncSession) -> None:
    work_id = await make_anime_work(db_session)
    rights = assess_mediawiki_rights(
        {"url": "https://example.invalid/all-rights-reserved", "text": "Proprietary"},
        page_title=PAGE_TITLE,
        page_url=PAGE_URL,
        revision_ref="987",
    )

    report = await ingest_summaries(db_session, work_id, rights=rights)

    assert report.stored is False
    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == work_id)
    )
    assert units.scalar_one() == 0


async def test_report_still_describes_what_was_parsed_when_refused(
    db_session: AsyncSession,
) -> None:
    """A refusal must be diagnosable, not silent."""
    work_id = await make_anime_work(db_session)

    report = await ingest_summaries(db_session, work_id, rights=unknown_rights("no licence"))

    assert report.summaries_extracted == 2
    assert report.parser_skipped
    assert report.not_stored_reason


@pytest.mark.parametrize("reason", ["unparseable_episode_number", "no_summary"])
async def test_parser_refusals_are_carried_into_the_report(
    db_session: AsyncSession, reason: str
) -> None:
    work_id = await make_anime_work(db_session)

    report = await ingest_summaries(db_session, work_id)

    assert reason in {item["reason"] for item in report.parser_skipped}


# --- volume summaries ----------------------------------------------------
#
# Manga reuses this whole path with `container_type="volume"`. These tests
# cover what changes (which containers are looked at) and confirm what does
# not (tier, provenance, matching, refusals).

VOLUME_PAGE_TITLE = "List of Test Manga chapters"
VOLUME_PAGE_URL = "https://en.wikipedia.org/wiki/List_of_Test_Manga_chapters"


def volume_wikitext() -> str:
    return (FIXTURES / "wikipedia_volume_list.wikitext").read_text(encoding="utf-8")


async def make_manga_work(session: AsyncSession, *, volumes: int = 4):
    """A manga work whose volume containers mirror the fixture's volumes."""
    media = json.loads(
        (FIXTURES / "anilist_manga_vinland_saga.json").read_text(encoding="utf-8")
    )
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["volumes"] = volumes
    media["relations"] = {"edges": []}

    result = await ingest_source_work(session, AniListMangaAdapter(media=media).load())
    return result.work_id


async def ingest_volume_summaries(session, work_id, *, rights=None, revision="654"):
    return await attach_episode_summaries(
        session,
        work_id=work_id,
        work_title="Test Manga",
        parsed=parse_volume_list(volume_wikitext()),
        rights=rights
        if rights is not None
        else assess_mediawiki_rights(
            CC_BY_SA_4,
            page_title=VOLUME_PAGE_TITLE,
            page_url=VOLUME_PAGE_URL,
            revision_ref=revision,
        ),
        page_title=VOLUME_PAGE_TITLE,
        page_url=VOLUME_PAGE_URL,
        revision_ref=revision,
        retrieved_text=volume_wikitext(),
        container_type="volume",
    )


async def test_volume_summaries_attach_to_existing_volume_containers(
    db_session: AsyncSession,
) -> None:
    work_id = await make_manga_work(db_session)

    before = (
        await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work_id)
        )
    ).scalar_one()

    report = await ingest_volume_summaries(db_session, work_id)

    after = (
        await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work_id)
        )
    ).scalar_one()

    assert report.stored is True
    assert report.containers_examined == 4
    assert report.attached == 3  # volumes 1, 2 and 4; volume 3 has no summary
    # Wikipedia adds text; it must never add structure.
    assert before == after == 4


async def test_volume_summaries_land_on_the_volume_their_number_names(
    db_session: AsyncSession,
) -> None:
    """Volume 3 is skipped by the source, so volume 4's text must not shift onto it."""
    work_id = await make_manga_work(db_session)
    await ingest_volume_summaries(db_session, work_id)

    rows = (
        (
            await db_session.execute(
                select(Container.sequence_number, ContentUnit.text_content)
                .join(ContentUnit, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
                .order_by(Container.sequence_number)
            )
        )
        .all()
    )

    by_volume = dict(rows)
    assert sorted(by_volume) == [1, 2, 4]
    assert "beyond the sea" in by_volume[1]
    assert by_volume[4].startswith("The crossing ends")


async def test_volume_summaries_are_summary_tier_with_provenance(
    db_session: AsyncSession,
) -> None:
    """Same guarantee as episodes: a synopsis can never pass as the work's words."""
    work_id = await make_manga_work(db_session)
    await ingest_volume_summaries(db_session, work_id)

    units = (
        (
            await db_session.execute(
                select(ContentUnit)
                .join(Container, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
            )
        )
        .scalars()
        .all()
    )

    assert units
    for unit in units:
        assert unit.text_tier == TEXT_TIER_SUMMARY
        assert unit.unit_type == "synopsis"
        # The match-evidence fields keep the episode names across both
        # container types; the number is the volume number here.
        assert unit.extra_metadata["source_episode_number"]

    source = await db_session.get(TextSource, units[0].text_source_id)
    assert source.source_name == "wikipedia"
    assert source.source_ref == VOLUME_PAGE_TITLE
    assert source.permits_storage is True
    assert source.licence == "CC-BY-SA-4.0"


async def test_volume_summaries_are_refused_when_rights_are_unknown(
    db_session: AsyncSession,
) -> None:
    work_id = await make_manga_work(db_session)

    report = await ingest_volume_summaries(
        db_session, work_id, rights=unknown_rights("no licence")
    )

    assert report.stored is False
    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == work_id)
    )
    assert units.scalar_one() == 0


async def test_reattaching_the_same_volume_page_changes_nothing(
    db_session: AsyncSession,
) -> None:
    work_id = await make_manga_work(db_session)

    first = await ingest_volume_summaries(db_session, work_id)
    second = await ingest_volume_summaries(db_session, work_id)

    assert first.attached == 3
    assert second.attached == 0
    assert second.unchanged == 3


async def test_volume_summaries_beyond_the_known_volume_count_are_unmatched(
    db_session: AsyncSession,
) -> None:
    """AniList says 2 volumes, Wikipedia summarises 4: report, never invent."""
    work_id = await make_manga_work(db_session, volumes=2)

    report = await ingest_volume_summaries(db_session, work_id)

    assert report.attached == 2
    assert [item["episode_number"] for item in report.unmatched] == [4]
    assert report.unmatched[0]["reason"] == "no_volume_container_with_that_number"


async def test_episode_and_volume_containers_do_not_cross_contaminate(
    db_session: AsyncSession,
) -> None:
    """A work's volume summaries must not be looked for among episode containers."""
    anime_id = await make_anime_work(db_session)

    report = await ingest_volume_summaries(db_session, anime_id)

    assert report.containers_examined == 0
    assert len(report.unmatched) == 3
    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == anime_id)
    )
    assert units.scalar_one() == 0
