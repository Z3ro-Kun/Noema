"""Contextual passages against the real schema, in rolled-back transactions."""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    OWNER_TYPE_CONTEXTUAL_PASSAGE,
    Container,
    ContentUnit,
    ContextualPassage,
    Embedding,
)
from app.services.embedding.contextual import build_passages_for_corpus, embed_passages
from app.services.embedding.grouping import GroupingConfig
from app.services.embedding.service import OWNER_TYPE_CONTENT_UNIT, embed_content_units
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from tests.fake_encoder import FakeEncoder
from tests.test_embedding_service import cleanup_work, sync_ingest, units_for

FIXTURES = Path(__file__).parent / "fixtures"


def words(text: str) -> int:
    return len(text.split())


def make_work(session: Session, source_ref: str = "ctx-test"):
    return sync_ingest(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="Contextual Test Novel",
            source_ref=source_ref,
        ).load(),
    )


def passages_for(session: Session, work_id, config: GroupingConfig | None = None):
    config = config or GroupingConfig()
    return list(
        session.execute(
            select(ContextualPassage)
            .join(Container, ContextualPassage.container_id == Container.id)
            .where(
                Container.work_id == work_id,
                ContextualPassage.grouping_config == config.key,
            )
            .order_by(Container.sequence_number, ContextualPassage.sequence_number)
        )
        .scalars()
        .all()
    )


# --- building ------------------------------------------------------------


def test_passages_are_built_without_touching_source_units(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        units_before = units_for(sync_db_session, work_id)
        texts_before = [(u.id, u.text_content) for u in units_before]

        build_passages_for_corpus(sync_db_session, words, work_id=work_id)

        units_after = units_for(sync_db_session, work_id)
        assert [(u.id, u.text_content) for u in units_after] == texts_before
        assert len(units_after) == len(units_before)
    finally:
        cleanup_work(sync_db_session, work_id)


def test_passages_never_cross_a_container_boundary(sync_db_session: Session) -> None:
    """A chapter break is a real narrative break."""
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)

        for passage in passages_for(sync_db_session, work_id):
            unit_containers = set(
                sync_db_session.execute(
                    select(ContentUnit.container_id).where(
                        ContentUnit.id.in_([str(i) for i in passage.source_unit_ids])
                    )
                )
                .scalars()
                .all()
            )
            assert unit_containers == {passage.container_id}
    finally:
        cleanup_work(sync_db_session, work_id)


def test_every_passage_traces_to_its_source_units(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)
        passages = passages_for(sync_db_session, work_id)

        assert passages
        for passage in passages:
            assert passage.source_unit_ids
            assert passage.unit_count == len(passage.source_unit_ids)
            assert passage.first_unit_sequence <= passage.last_unit_sequence
            found = sync_db_session.execute(
                select(func.count())
                .select_from(ContentUnit)
                .where(ContentUnit.id.in_([str(i) for i in passage.source_unit_ids]))
            ).scalar_one()
            assert found == passage.unit_count
    finally:
        cleanup_work(sync_db_session, work_id)


def test_passage_text_is_the_concatenation_of_its_units(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)
        passage = passages_for(sync_db_session, work_id)[0]

        units = {
            str(u.id): u
            for u in sync_db_session.execute(
                select(ContentUnit).where(
                    ContentUnit.id.in_([str(i) for i in passage.source_unit_ids])
                )
            )
            .scalars()
            .all()
        }
        for unit_id in passage.source_unit_ids:
            snippet = units[str(unit_id)].text_content.split("\n")[0][:40]
            assert snippet in passage.text_content
    finally:
        cleanup_work(sync_db_session, work_id)


def test_passages_carry_the_grouping_config(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)

        for passage in passages_for(sync_db_session, work_id):
            assert passage.grouping_config == "window=3;overlap=1;max_tokens=240"
            assert len(passage.source_hash) == 64
    finally:
        cleanup_work(sync_db_session, work_id)


def test_passages_inherit_primary_tier_from_literature_units(
    sync_db_session: Session,
) -> None:
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)

        assert {p.text_tier for p in passages_for(sync_db_session, work_id)} == {"primary"}
    finally:
        cleanup_work(sync_db_session, work_id)


# --- idempotency and staleness ------------------------------------------


def test_rebuilding_unchanged_text_creates_nothing(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        first = build_passages_for_corpus(sync_db_session, words, work_id=work_id)
        second = build_passages_for_corpus(sync_db_session, words, work_id=work_id)

        assert first.passages_created > 0
        assert second.passages_created == 0
        assert second.passages_removed == 0
        # Scoped to one work, so this is an exact count, not a lower bound.
        assert second.passages_unchanged == first.passages_created
    finally:
        cleanup_work(sync_db_session, work_id)


def test_changed_source_text_rebuilds_the_affected_passages(
    sync_db_session: Session,
) -> None:
    work_id = make_work(sync_db_session)
    try:
        build_passages_for_corpus(sync_db_session, words, work_id=work_id)
        # Key by (container, sequence): sequence numbers restart per
        # container, so keying on sequence alone would silently collapse them.
        before = {
            (p.container_id, p.sequence_number): p.source_hash
            for p in passages_for(sync_db_session, work_id)
        }

        edited = units_for(sync_db_session, work_id)[0]
        edited_container = edited.container_id
        edited.text_content = "Entirely new text for this unit, which changes its passage."
        sync_db_session.flush()

        report = build_passages_for_corpus(sync_db_session, words, work_id=work_id)
        after = {
            (p.container_id, p.sequence_number): p.source_hash
            for p in passages_for(sync_db_session, work_id)
        }

        assert report.passages_created > 0
        assert before != after
        # Only the edited container's passages changed.
        changed_containers = {
            key[0] for key, digest in after.items() if before.get(key) != digest
        }
        assert changed_containers == {edited_container}
    finally:
        cleanup_work(sync_db_session, work_id)


def test_two_grouping_configs_coexist_for_comparison(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        default = GroupingConfig()
        other = GroupingConfig(window=2, overlap=0, max_tokens=240)
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=default)
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=other)

        assert passages_for(sync_db_session, work_id, default)
        assert passages_for(sync_db_session, work_id, other)
        configs = {p.grouping_config for p in passages_for(sync_db_session, work_id, default)}
        assert configs == {default.key}
    finally:
        cleanup_work(sync_db_session, work_id)


# --- embedding -----------------------------------------------------------


def test_passage_embeddings_are_stored_under_their_own_owner_type(
    sync_db_session: Session,
) -> None:
    work_id = make_work(sync_db_session)
    try:
        config = GroupingConfig()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)
        encoder = FakeEncoder()

        report = embed_passages(
            sync_db_session, encoder, grouping_config=config.key, work_id=work_id
        )

        ids = [p.id for p in passages_for(sync_db_session, work_id)]
        # Scoped: every eligible passage is this work's, and all were embedded.
        assert report.failed == []
        assert report.eligible == report.generated == len(ids) > 0
        stored = (
            sync_db_session.execute(
                select(Embedding).where(Embedding.owner_id.in_(ids))
            )
            .scalars()
            .all()
        )
        assert len(stored) == len(ids)
        for embedding in stored:
            assert embedding.owner_type == OWNER_TYPE_CONTEXTUAL_PASSAGE
            assert embedding.dimension == encoder.dimension
            assert embedding.normalized is True
            assert len(embedding.vector) == encoder.dimension
    finally:
        cleanup_work(sync_db_session, work_id)


def test_baseline_embeddings_are_untouched_by_the_experiment(
    sync_db_session: Session,
) -> None:
    """Both representations must remain available for comparison."""
    work_id = make_work(sync_db_session)
    try:
        units = units_for(sync_db_session, work_id)
        encoder = FakeEncoder()
        embed_content_units(sync_db_session, encoder, units=units)
        baseline_before = sync_db_session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(
                Embedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
                Embedding.owner_id.in_([u.id for u in units]),
            )
        ).scalar_one()

        config = GroupingConfig()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)
        embed_passages(
            sync_db_session, encoder, grouping_config=config.key, work_id=work_id
        )

        baseline_after = sync_db_session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(
                Embedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
                Embedding.owner_id.in_([u.id for u in units]),
            )
        ).scalar_one()
        assert baseline_after == baseline_before > 0
    finally:
        cleanup_work(sync_db_session, work_id)


def test_reembedding_passages_skips_current_vectors(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        config = GroupingConfig()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)
        encoder = FakeEncoder()

        first = embed_passages(
            sync_db_session, encoder, grouping_config=config.key, work_id=work_id
        )
        second = embed_passages(
            sync_db_session, encoder, grouping_config=config.key, work_id=work_id
        )

        assert first.generated > 0
        assert second.generated == 0
        assert second.regenerated_stale == 0
        # Exact, now that the run covers only this work's passages.
        assert second.skipped_current == second.eligible == first.generated
    finally:
        cleanup_work(sync_db_session, work_id)


def test_a_different_model_regenerates_passage_vectors(sync_db_session: Session) -> None:
    work_id = make_work(sync_db_session)
    try:
        config = GroupingConfig()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)
        embed_passages(
            sync_db_session,
            FakeEncoder(model_name="model/a"),
            grouping_config=config.key,
            work_id=work_id,
        )

        report = embed_passages(
            sync_db_session,
            FakeEncoder(model_name="model/b"),
            grouping_config=config.key,
            work_id=work_id,
        )

        assert report.regenerated_stale > 0
        assert report.generated == 0
    finally:
        cleanup_work(sync_db_session, work_id)


def test_rebuilt_passages_drop_their_stale_embeddings(sync_db_session: Session) -> None:
    """A replaced passage must not leave an orphaned vector behind."""
    work_id = make_work(sync_db_session)
    try:
        config = GroupingConfig()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)
        embed_passages(
            sync_db_session, FakeEncoder(), grouping_config=config.key, work_id=work_id
        )
        old_ids = [p.id for p in passages_for(sync_db_session, work_id)]

        unit = units_for(sync_db_session, work_id)[0]
        unit.text_content = "Replacement text that forces a rebuild of this container."
        sync_db_session.flush()
        build_passages_for_corpus(sync_db_session, words, work_id=work_id, config=config)

        orphaned = sync_db_session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(
                Embedding.owner_type == OWNER_TYPE_CONTEXTUAL_PASSAGE,
                Embedding.owner_id.in_(old_ids),
            )
        ).scalar_one()
        # Only passages from untouched containers survive; the rebuilt
        # container's old vectors are gone.
        surviving_ids = {p.id for p in passages_for(sync_db_session, work_id)}
        assert orphaned == len(surviving_ids & set(old_ids))
    finally:
        cleanup_work(sync_db_session, work_id)
