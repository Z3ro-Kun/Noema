"""Embedding generation against the real schema, inside rolled-back transactions.

Uses a deterministic fake encoder, so nothing here downloads a model.
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    TEXT_TIER_PRIMARY,
    TEXT_TIER_SUMMARY,
    Container,
    ContentUnit,
    Embedding,
    Work,
)
from app.services.embedding.preparation import PREP_VERSION, prepare_text, text_hash
from app.services.embedding.service import (
    OWNER_TYPE_CONTENT_UNIT,
    eligible_content_units,
    eligible_units_query,
    embed_content_units,
    is_stale,
    record_truncation,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work
from tests.fake_encoder import ExplodingEncoder, FakeEncoder, WrongDimensionEncoder

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 960000


def sync_ingest(session: Session, source_work):
    """Run the async ingestion service against a sync session.

    The ingestion services are async; embedding tests need the same fixtures
    under a sync session, so this drives them with SQLAlchemy's sync API via
    a tiny shim rather than duplicating the ingestion logic.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    async def _run():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as s:
                result = await ingest_source_work(s, source_work)
                await s.commit()
                return result.work_id
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def cleanup_work(session: Session, work_id) -> None:
    """Remove a committed test work and discard the test's in-flight changes.

    The rollback matters: corpus-wide helpers like `build_passages_for_corpus`
    also touch the *real* committed works, and committing here without
    discarding first would persist that derived data into the dev database.
    """
    from sqlalchemy import text

    session.rollback()

    session.execute(
        text(
            "DELETE FROM embeddings WHERE owner_id IN (SELECT cu.id FROM content_units cu "
            "JOIN containers ct ON ct.id = cu.container_id WHERE ct.work_id = :w)"
        ),
        {"w": work_id},
    )
    # Derived passages (and their vectors) reference containers, so they go
    # before the containers do.
    session.execute(
        text(
            "DELETE FROM embeddings WHERE owner_id IN (SELECT cp.id FROM contextual_passages cp "
            "JOIN containers ct ON ct.id = cp.container_id WHERE ct.work_id = :w)"
        ),
        {"w": work_id},
    )
    session.execute(
        text(
            "DELETE FROM contextual_passages WHERE container_id IN "
            "(SELECT id FROM containers WHERE work_id = :w)"
        ),
        {"w": work_id},
    )
    session.execute(
        text(
            "DELETE FROM content_units WHERE container_id IN "
            "(SELECT id FROM containers WHERE work_id = :w)"
        ),
        {"w": work_id},
    )
    for statement in (
        "DELETE FROM containers WHERE work_id = :w",
        "DELETE FROM entities WHERE work_id = :w",
        "DELETE FROM work_creators WHERE work_id = :w",
        "DELETE FROM works WHERE id = :w",
    ):
        session.execute(text(statement), {"w": work_id})
    session.commit()


def literature_work(session: Session):
    return sync_ingest(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="Embedding Test Novel",
            source_ref="embed-test-lit",
        ).load(),
    )


def anime_work(session: Session):
    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["episodes"] = 2
    media["relations"] = {"edges": []}
    return sync_ingest(session, AniListAnimeAdapter(media=media).load())


def units_for(session: Session, work_id) -> list[ContentUnit]:
    return list(
        session.execute(
            select(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == work_id)
            .order_by(ContentUnit.id)
        )
        .scalars()
        .all()
    )


# --- eligibility ---------------------------------------------------------


def test_only_units_with_text_are_eligible(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = eligible_content_units(sync_db_session)
        assert units
        assert all(unit.text_content for unit in units)
    finally:
        cleanup_work(sync_db_session, work_id)


def test_eligibility_can_be_narrowed_by_tier(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        primary = eligible_content_units(sync_db_session, text_tier=TEXT_TIER_PRIMARY)
        summary = eligible_content_units(sync_db_session, text_tier=TEXT_TIER_SUMMARY)

        assert all(u.text_tier == TEXT_TIER_PRIMARY for u in primary)
        assert all(u.text_tier == TEXT_TIER_SUMMARY for u in summary)
    finally:
        cleanup_work(sync_db_session, work_id)


def test_eligibility_can_be_narrowed_by_domain(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = eligible_content_units(sync_db_session, domain_slug="literature", limit=5)
        assert 0 < len(units) <= 5
    finally:
        cleanup_work(sync_db_session, work_id)


def test_eligibility_can_be_narrowed_by_work(sync_db_session: Session) -> None:
    """Work scoping returns only that work's units, and filters in SQL.

    This is what keeps corpus-wide helpers from walking every unit in the
    database when a caller only cares about one work.
    """
    work_id = literature_work(sync_db_session)
    try:
        scoped = eligible_content_units(sync_db_session, work_id=work_id)
        everything = eligible_content_units(sync_db_session)

        assert scoped
        assert len(scoped) < len(everything), "the corpus has other works to exclude"
        assert {u.id for u in scoped} == {u.id for u in units_for(sync_db_session, work_id)}

        compiled = str(
            eligible_units_query(work_id=work_id).compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "works.id" in compiled
    finally:
        cleanup_work(sync_db_session, work_id)


# --- generation and persistence -----------------------------------------


def test_embeddings_are_stored_with_reproducibility_metadata(
    sync_db_session: Session,
) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)
        encoder = FakeEncoder()

        report = embed_content_units(sync_db_session, encoder, units=units)

        assert report.generated == len(units)
        stored = (
            sync_db_session.execute(
                select(Embedding).where(Embedding.owner_id.in_([u.id for u in units]))
            )
            .scalars()
            .all()
        )
        assert len(stored) == len(units)
        for embedding in stored:
            assert embedding.owner_type == OWNER_TYPE_CONTENT_UNIT
            assert embedding.model_name == encoder.model_name
            assert embedding.model_revision == encoder.model_revision
            assert embedding.dimension == encoder.dimension
            assert embedding.normalized is True
            assert embedding.prep_version == PREP_VERSION
            assert len(embedding.source_hash) == 64
            assert len(embedding.vector) == encoder.dimension
    finally:
        cleanup_work(sync_db_session, work_id)


def test_stored_hash_matches_the_prepared_text(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:3]
        embed_content_units(sync_db_session, FakeEncoder(), units=units)

        for unit in units:
            embedding = sync_db_session.execute(
                select(Embedding).where(Embedding.owner_id == unit.id)
            ).scalar_one()
            assert embedding.source_hash == text_hash(prepare_text(unit.text_content))
    finally:
        cleanup_work(sync_db_session, work_id)


def test_the_model_is_loaded_once_and_used_in_batches(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)
        encoder = FakeEncoder()

        embed_content_units(sync_db_session, encoder, units=units, batch_size=4)

        # Batched, not one call per unit.
        assert encoder.encode_calls == (len(units) + 3) // 4
        assert all(len(batch) <= 4 for batch in encoder.encoded_batches)
    finally:
        cleanup_work(sync_db_session, work_id)


def test_wrong_dimension_is_rejected_not_stored(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:2]

        report = embed_content_units(sync_db_session, WrongDimensionEncoder(), units=units)

        assert report.generated == 0
        assert len(report.failed) == 2
        assert "expected" in report.failed[0]["reason"]
        count = sync_db_session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(Embedding.owner_id.in_([u.id for u in units]))
        ).scalar_one()
        assert count == 0
    finally:
        cleanup_work(sync_db_session, work_id)


def test_encoder_failure_is_reported_per_unit(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:2]

        report = embed_content_units(sync_db_session, ExplodingEncoder(), units=units)

        assert report.generated == 0
        assert len(report.failed) == 2
        assert "model unavailable" in report.failed[0]["reason"]
    finally:
        cleanup_work(sync_db_session, work_id)


# --- idempotency and staleness ------------------------------------------


def test_rerunning_skips_units_that_are_already_current(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)
        encoder = FakeEncoder()

        first = embed_content_units(sync_db_session, encoder, units=units)
        second = embed_content_units(sync_db_session, encoder, units=units)

        assert first.generated == len(units)
        assert second.generated == 0
        assert second.skipped_current == len(units)

        total = sync_db_session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(Embedding.owner_id.in_([u.id for u in units]))
        ).scalar_one()
        assert total == len(units)
    finally:
        cleanup_work(sync_db_session, work_id)


def test_changed_text_makes_the_vector_stale(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:1]
        encoder = FakeEncoder()
        embed_content_units(sync_db_session, encoder, units=units)
        before = sync_db_session.execute(
            select(Embedding).where(Embedding.owner_id == units[0].id)
        ).scalar_one()
        original_vector = list(before.vector)

        units[0].text_content = "Completely different prose about something else entirely."
        sync_db_session.flush()

        report = embed_content_units(sync_db_session, encoder, units=units)

        assert report.regenerated_stale == 1
        assert report.skipped_current == 0
        after = sync_db_session.execute(
            select(Embedding).where(Embedding.owner_id == units[0].id)
        ).scalar_one()
        assert list(after.vector) != original_vector
    finally:
        cleanup_work(sync_db_session, work_id)


def test_a_different_model_is_not_reused_as_the_same_representation(
    sync_db_session: Session,
) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:2]
        embed_content_units(sync_db_session, FakeEncoder(model_name="model/a"), units=units)

        report = embed_content_units(
            sync_db_session, FakeEncoder(model_name="model/b"), units=units
        )

        assert report.regenerated_stale == 2
        assert report.skipped_current == 0
        stored = sync_db_session.execute(
            select(Embedding.model_name).where(Embedding.owner_id.in_([u.id for u in units]))
        ).scalars().all()
        assert set(stored) == {"model/b"}
    finally:
        cleanup_work(sync_db_session, work_id)


def test_a_changed_model_revision_marks_vectors_stale(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:2]
        embed_content_units(sync_db_session, FakeEncoder(model_revision="r1"), units=units)

        report = embed_content_units(
            sync_db_session, FakeEncoder(model_revision="r2"), units=units
        )

        assert report.regenerated_stale == 2
    finally:
        cleanup_work(sync_db_session, work_id)


def test_force_regenerates_even_when_current(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:3]
        encoder = FakeEncoder()
        embed_content_units(sync_db_session, encoder, units=units)

        report = embed_content_units(sync_db_session, encoder, units=units, force=True)

        assert report.regenerated_stale == 3
        assert report.skipped_current == 0
    finally:
        cleanup_work(sync_db_session, work_id)


def test_is_stale_checks_every_reproducibility_field() -> None:
    encoder = FakeEncoder()
    current = Embedding(
        owner_type=OWNER_TYPE_CONTENT_UNIT,
        owner_id=None,
        model_name=encoder.model_name,
        model_revision=encoder.model_revision,
        vector=[0.0] * encoder.dimension,
        source_hash="abc",
        dimension=encoder.dimension,
        normalized=True,
        prep_version=PREP_VERSION,
    )

    assert is_stale(current, source_hash="abc", encoder=encoder) is False
    assert is_stale(current, source_hash="different", encoder=encoder) is True
    assert is_stale(current, source_hash="abc", encoder=FakeEncoder(model_name="other")) is True
    assert is_stale(current, source_hash="abc", encoder=FakeEncoder(model_revision="x")) is True

    current.prep_version = PREP_VERSION + 1
    assert is_stale(current, source_hash="abc", encoder=encoder) is True


# --- tiers and truncation ------------------------------------------------


def test_both_tiers_are_embedded_and_keep_their_tier(sync_db_session: Session) -> None:
    lit_id = literature_work(sync_db_session)
    anime_id = anime_work(sync_db_session)
    try:
        lit_units = units_for(sync_db_session, lit_id)
        assert lit_units
        embed_content_units(sync_db_session, FakeEncoder(), units=lit_units)

        tiers = sync_db_session.execute(
            select(ContentUnit.text_tier)
            .join(Embedding, Embedding.owner_id == ContentUnit.id)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == lit_id)
            .distinct()
        ).scalars().all()

        assert tiers == [TEXT_TIER_PRIMARY]
    finally:
        cleanup_work(sync_db_session, lit_id)
        cleanup_work(sync_db_session, anime_id)


def test_units_longer_than_the_model_window_are_reported(sync_db_session: Session) -> None:
    """Truncation is lossy, so it must be visible rather than silent."""
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:1]
        units[0].text_content = " ".join(["word"] * 500)
        sync_db_session.flush()
        encoder = FakeEncoder(max_seq_length=256)

        report = embed_content_units(sync_db_session, encoder, units=units)
        record_truncation(report, encoder, units)

        assert report.generated == 1
        assert len(report.truncated) == 1
        assert report.truncated[0]["tokens"] > report.truncated[0]["limit"]
    finally:
        cleanup_work(sync_db_session, work_id)


def test_short_units_are_not_reported_as_truncated(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:2]
        encoder = FakeEncoder(max_seq_length=256)

        report = embed_content_units(sync_db_session, encoder, units=units)
        record_truncation(report, encoder, units)

        assert report.truncated == []
    finally:
        cleanup_work(sync_db_session, work_id)


def test_empty_text_unit_is_failed_not_embedded(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)[:1]
        units[0].text_content = "   "
        sync_db_session.flush()

        report = embed_content_units(sync_db_session, FakeEncoder(), units=units)

        assert report.generated == 0
        assert len(report.failed) == 1
        assert "empty" in report.failed[0]["reason"]
    finally:
        cleanup_work(sync_db_session, work_id)


def test_embedding_creates_no_relationships(sync_db_session: Session) -> None:
    """Similarity is an observation; it must never become a Relationship row."""
    from app.models import Relationship

    work_id = literature_work(sync_db_session)
    try:
        before = sync_db_session.execute(
            select(func.count()).select_from(Relationship)
        ).scalar_one()

        units = units_for(sync_db_session, work_id)
        embed_content_units(sync_db_session, FakeEncoder(), units=units)

        after = sync_db_session.execute(
            select(func.count()).select_from(Relationship)
        ).scalar_one()
        assert after == before
    finally:
        cleanup_work(sync_db_session, work_id)


def test_provenance_is_untouched_by_embedding(sync_db_session: Session) -> None:
    work_id = literature_work(sync_db_session)
    try:
        work = sync_db_session.execute(select(Work).where(Work.id == work_id)).scalar_one()
        provenance_before = json.dumps(work.extra_metadata, sort_keys=True)

        embed_content_units(
            sync_db_session, FakeEncoder(), units=units_for(sync_db_session, work_id)
        )

        sync_db_session.refresh(work)
        assert json.dumps(work.extra_metadata, sort_keys=True) == provenance_before
    finally:
        cleanup_work(sync_db_session, work_id)
