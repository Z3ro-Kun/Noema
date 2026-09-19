"""Candidate-model experiment tests, with a fake encoder. No model download.

Every corpus-touching helper is scoped to this file's own fixture work via
`work_id`, so runtime tracks the fixture (a handful of units) rather than the
whole production corpus.
"""

import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Embedding, ExperimentEmbedding
from app.services.embedding.model_experiment import (
    build_candidate_search_query,
    candidate_search,
    embed_with_candidate,
)
from app.services.embedding.service import OWNER_TYPE_CONTENT_UNIT, embed_content_units
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from tests.fake_encoder import FakeEncoder
from tests.test_embedding_service import cleanup_work, sync_ingest, units_for

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE = "fake/candidate-768"


def make_work(session: Session, source_ref: str = "model-exp-test"):
    return sync_ingest(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="Model Experiment Novel",
            source_ref=source_ref,
        ).load(),
    )


def candidate_encoder(dimension: int = 768) -> FakeEncoder:
    return FakeEncoder(model_name=CANDIDATE, model_revision="cand-1", dimension=dimension)


def test_candidate_vectors_land_in_the_experiment_table(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        encoder = candidate_encoder()

        report = embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        assert report.generated > 0
        assert report.failed == []
        assert report.dimension == 768

        stored = (
            sync_db_session.execute(
                select(ExperimentEmbedding).where(
                    ExperimentEmbedding.model_name == CANDIDATE
                )
            )
            .scalars()
            .all()
        )
        assert stored
        for row in stored:
            assert row.owner_type == OWNER_TYPE_CONTENT_UNIT
            assert row.dimension == 768
            assert len(row.vector) == 768
            assert len(row.source_hash) == 64
    finally:
        cleanup_work(sync_db_session, work_id)


def test_production_embeddings_are_never_written_by_an_experiment(
    sync_db_session: Session,
) -> None:
    """The whole point: running an experiment must not change what is served."""
    work_id = make_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)
        embed_content_units(sync_db_session, FakeEncoder(), units=units)
        before = sync_db_session.execute(
            select(func.count()).select_from(Embedding)
        ).scalar_one()

        embed_with_candidate(sync_db_session, candidate_encoder(), work_id=work_id)

        after = sync_db_session.execute(
            select(func.count()).select_from(Embedding)
        ).scalar_one()
        assert after == before
    finally:
        cleanup_work(sync_db_session, work_id)


def test_candidate_run_is_idempotent(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        encoder = candidate_encoder()

        first = embed_with_candidate(sync_db_session, encoder, work_id=work_id)
        second = embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        assert first.generated > 0
        assert second.generated == 0
        assert second.skipped_current == second.eligible
    finally:
        cleanup_work(sync_db_session, work_id)


def test_changed_text_regenerates_the_candidate_vector(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        encoder = candidate_encoder()
        embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        unit = units_for(sync_db_session, work_id)[0]
        unit.text_content = "Different prose entirely, which must invalidate the vector."
        sync_db_session.flush()

        report = embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        assert report.regenerated == 1
    finally:
        cleanup_work(sync_db_session, work_id)


def test_wrong_dimension_is_rejected(sync_db_session: Session) -> None:
    """A model whose real width disagrees with the declared one is refused."""
    work_id = make_work(sync_db_session)
    try:
        from tests.fake_encoder import WrongDimensionEncoder

        encoder = WrongDimensionEncoder(model_name=CANDIDATE, dimension=768)

        report = embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        assert report.generated == 0
        assert report.failed
        assert "expected 768" in report.failed[0]["reason"]
    finally:
        cleanup_work(sync_db_session, work_id)


def test_candidate_search_returns_ordered_hits(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        encoder = candidate_encoder()
        embed_with_candidate(sync_db_session, encoder, work_id=work_id)

        hits = candidate_search(
            sync_db_session,
            encoder,
            query="the harbour at dawn",
            top_k=5,
            work_id=work_id,
            text_tier="primary",
        )

        assert hits
        similarities = [hit.similarity for hit in hits]
        assert similarities == sorted(similarities, reverse=True)
        assert {hit.text_tier for hit in hits} == {"primary"}
    finally:
        cleanup_work(sync_db_session, work_id)


def test_candidate_search_is_scoped_to_one_model(sync_db_session: Session) -> None:
    """Widths differ between candidates, so cross-model reads must not happen."""
    work_id = make_work(sync_db_session)
    try:
        embed_with_candidate(sync_db_session, candidate_encoder(), work_id=work_id)

        other = FakeEncoder(model_name="fake/other-candidate", dimension=768)
        hits = candidate_search(
            sync_db_session, other, query="anything", top_k=5, work_id=work_id
        )

        assert hits == []
    finally:
        cleanup_work(sync_db_session, work_id)


def test_two_candidate_widths_coexist_without_colliding(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        embed_with_candidate(
            sync_db_session, FakeEncoder(model_name="fake/wide", dimension=768),
            work_id=work_id,
        )
        embed_with_candidate(
            sync_db_session, FakeEncoder(model_name="fake/narrow", dimension=384),
            work_id=work_id,
        )

        widths = sync_db_session.execute(
            select(ExperimentEmbedding.model_name, ExperimentEmbedding.dimension)
            .where(ExperimentEmbedding.model_name.in_(["fake/wide", "fake/narrow"]))
            .distinct()
        ).all()

        assert set(widths) == {("fake/wide", 768), ("fake/narrow", 384)}
    finally:
        cleanup_work(sync_db_session, work_id)


def test_candidate_query_always_filters_by_model() -> None:
    """Every filter is applied in SQL, and the model filter is never optional."""
    statement = build_candidate_search_query(
        [0.0] * 768,
        model_name=CANDIDATE,
        top_k=5,
        domain_slug="literature",
        text_tier="primary",
        work_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "model_name" in compiled
    assert "domains.slug" in compiled
    assert "text_tier" in compiled
    assert "works.id" in compiled
    assert "LIMIT 5" in compiled
