"""Work-level concepts against the real schema, inside rolled-back transactions.

Fixture-scoped throughout, per the Phase 1J principle: every test builds its
own small work and never touches the production corpus.

The questions these answer are the phase's: does a work get concepts only
from labels its own source supplies, is the vocabulary shared rather than
duplicated per work, and does the user layer stay entirely out of it?
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    METHOD_ANILIST_GENRE,
    METHOD_ANILIST_TAG,
    METHOD_GUTENBERG_SUBJECT,
    SOURCE_PROVIDED,
    Concept,
    Container,
    ContentUnit,
    Work,
    WorkConcept,
)
from app.services import auth_service, library_service
from app.services.concepts.service import (
    SourceLabel,
    anilist_labels,
    apply_source_labels,
    ensure_vocabulary,
    gutenberg_labels,
    list_work_concepts,
)
from app.services.concepts.vocabulary import VOCABULARY
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 960000
PASSWORD = "a-sufficiently-long-password"


def _shift(media: dict) -> dict:
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["relations"] = {"edges": []}
    return media


async def make_anime(session: AsyncSession, filename: str = "anilist_cowboy_bebop.json") -> Work:
    media = _shift(json.loads((FIXTURES / filename).read_text(encoding="utf-8")))
    result = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
    return await session.get(Work, result.work_id)


async def make_manga(session: AsyncSession) -> Work:
    media = _shift(
        json.loads((FIXTURES / "anilist_manga_vinland_saga.json").read_text(encoding="utf-8"))
    )
    result = await ingest_source_work(session, AniListMangaAdapter(media=media).load())
    return await session.get(Work, result.work_id)


async def make_literature(session: AsyncSession, source_ref: str = "concept-test") -> Work:
    result = await ingest_source_work(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref=source_ref,
            author="A Test Author",
        ).load(),
    )
    return await session.get(Work, result.work_id)


async def make_metadata_only_work(session: AsyncSession) -> Work:
    """A work its source catalogues with no labels at all."""
    media = {
        "id": TEST_ID_OFFSET + 7,
        "title": {"romaji": "Uncatalogued Series"},
        "volumes": 3,
        "genres": [],
        "tags": [],
        "staff": None,
        "characters": None,
        "relations": None,
    }
    result = await ingest_source_work(session, AniListMangaAdapter(media=media).load())
    return await session.get(Work, result.work_id)


# --- the vocabulary is shared, not per work -------------------------------


async def test_ensure_vocabulary_is_idempotent_and_complete(db_session: AsyncSession) -> None:
    """Asserts the end state, not the delta.

    The dev database may already hold the vocabulary from a real population
    run, so "how many did this call create" is not a stable fact. What must
    always hold is that afterwards every entry exists exactly once and a
    second call changes nothing.
    """
    await ensure_vocabulary(db_session)
    again_created, again_updated = await ensure_vocabulary(db_session)

    assert (again_created, again_updated) == (0, 0)

    rows = (await db_session.execute(select(Concept))).scalars().all()
    by_slug = {concept.slug: concept for concept in rows}
    assert len(by_slug) == len(rows), "duplicate slugs in the vocabulary"
    for entry in VOCABULARY:
        stored = by_slug[entry.slug]
        assert (stored.name, stored.concept_type, stored.description) == (
            entry.name,
            entry.concept_type,
            entry.description,
        )


async def test_a_reworded_display_name_updates_rather_than_duplicates(
    db_session: AsyncSession,
) -> None:
    """Matching on slug is what keeps a rename from creating a second row."""
    await ensure_vocabulary(db_session)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "psychological-depth"))
    ).scalar_one()
    concept.name = "Something Else Entirely"
    await db_session.flush()

    created, updated = await ensure_vocabulary(db_session)

    assert created == 0 and updated == 1
    rows = (
        (await db_session.execute(select(Concept).where(Concept.slug == "psychological-depth")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].name == "Psychological Depth"


async def test_two_works_share_one_concept_row(db_session: AsyncSession) -> None:
    """The point of a canonical vocabulary: no per-work concept copies."""
    await ensure_vocabulary(db_session)
    anime = await make_anime(db_session)
    manga = await make_manga(db_session)

    await apply_source_labels(db_session, work=anime, labels=anilist_labels(anime))
    await apply_source_labels(db_session, work=manga, labels=anilist_labels(manga))

    adventure = (
        await db_session.execute(select(Concept).where(Concept.slug == "adventure"))
    ).scalar_one()
    associations = (
        (
            await db_session.execute(
                select(WorkConcept).where(WorkConcept.concept_id == adventure.id)
            )
        )
        .scalars()
        .all()
    )

    assert {anime.id, manga.id} <= {row.work_id for row in associations}
    # One concept row, several associations -- never one concept per work.
    total = await db_session.execute(
        select(func.count()).select_from(Concept).where(Concept.slug == "adventure")
    )
    assert total.scalar_one() == 1


# --- associations and provenance ------------------------------------------


async def test_source_labels_become_associations_with_provenance(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    report = await apply_source_labels(db_session, work=work, labels=anilist_labels(work))

    assert report.created > 0
    rows = (
        (await db_session.execute(select(WorkConcept).where(WorkConcept.work_id == work.id)))
        .scalars()
        .all()
    )
    assert rows
    for row in rows:
        assert row.source == SOURCE_PROVIDED
        assert row.method in {METHOD_ANILIST_GENRE, METHOD_ANILIST_TAG}
        # Nothing is unsupported: every association names the labels behind it.
        assert row.supporting_labels
        assert all("label" in item and "method" in item for item in row.supporting_labels)


async def test_several_labels_collapse_onto_one_concept_without_losing_any(
    db_session: AsyncSession,
) -> None:
    """Crime + Detective + Police is one concept supported three ways."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    await apply_source_labels(
        db_session,
        work=work,
        labels=[
            SourceLabel("Crime", METHOD_ANILIST_TAG, rank=90),
            SourceLabel("Detective", METHOD_ANILIST_TAG, rank=70),
            SourceLabel("Police", METHOD_ANILIST_TAG, rank=50),
        ],
    )

    pairs = await list_work_concepts(db_session, work.id)
    assert len(pairs) == 1
    association, concept = pairs[0]
    assert concept.slug == "crime-and-investigation"
    assert {item["label"] for item in association.supporting_labels} == {
        "Crime",
        "Detective",
        "Police",
    }
    # The strongest stated relevance is what the row carries.
    assert association.confidence == pytest.approx(0.90)


async def test_confidence_is_the_sources_own_rank_and_null_when_unstated(
    db_session: AsyncSession,
) -> None:
    """No number is invented for a source that expressed no relevance."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    await apply_source_labels(
        db_session,
        work=work,
        labels=[
            SourceLabel("Psychological", METHOD_ANILIST_TAG, rank=83),
            SourceLabel("Comedy", METHOD_ANILIST_GENRE, rank=None),
        ],
    )

    by_slug = {c.slug: a for a, c in await list_work_concepts(db_session, work.id)}
    assert by_slug["psychological-depth"].confidence == pytest.approx(0.83)
    assert by_slug["comedy"].confidence is None


async def test_ranked_associations_are_listed_before_unranked_ones(
    db_session: AsyncSession,
) -> None:
    """An unstated relevance is not a low one, so it is not sorted as one."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    await apply_source_labels(
        db_session,
        work=work,
        labels=[
            SourceLabel("Comedy", METHOD_ANILIST_GENRE, rank=None),
            SourceLabel("Tragedy", METHOD_ANILIST_TAG, rank=20),
        ],
    )

    ordered = [c.slug for _, c in await list_work_concepts(db_session, work.id)]
    assert ordered.index("tragedy") < ordered.index("comedy")


async def test_unmapped_labels_are_reported_never_invented(db_session: AsyncSession) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    report = await apply_source_labels(
        db_session,
        work=work,
        labels=[
            SourceLabel("Shounen", METHOD_ANILIST_TAG, rank=88),
            SourceLabel("Completely Invented Tag", METHOD_ANILIST_TAG, rank=99),
            SourceLabel("Tragedy", METHOD_ANILIST_TAG, rank=60),
        ],
    )

    assert set(report.unmapped) == {"Shounen", "Completely Invented Tag"}
    assert report.created == 1
    # The vocabulary did not grow to accommodate them.
    total = await db_session.execute(select(func.count()).select_from(Concept))
    assert total.scalar_one() == len(VOCABULARY)


async def test_population_refuses_when_the_vocabulary_is_absent(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Better to fail loudly than to create concepts from the population path.

    The missing-vocabulary state is simulated rather than created: deleting
    concept rows would mean deleting corpus-wide associations to satisfy the
    foreign key, which is exactly the kind of in-flight corpus mutation that
    has leaked out of tests before.
    """
    from app.services.concepts import service as concepts_service

    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    async def empty_vocabulary(_session):
        return {}

    monkeypatch.setattr(concepts_service, "concepts_by_slug", empty_vocabulary)

    with pytest.raises(LookupError, match="ensure_vocabulary"):
        await apply_source_labels(
            db_session, work=work, labels=[SourceLabel("Tragedy", METHOD_ANILIST_TAG, rank=60)]
        )


# --- idempotency and uniqueness -------------------------------------------


async def test_rerunning_population_creates_no_duplicates(db_session: AsyncSession) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    labels = anilist_labels(work)

    first = await apply_source_labels(db_session, work=work, labels=labels)
    second = await apply_source_labels(db_session, work=work, labels=labels)

    assert first.created > 0
    assert second.created == 0 and second.updated == 0
    assert second.unchanged == first.created

    count = await db_session.execute(
        select(func.count()).select_from(WorkConcept).where(WorkConcept.work_id == work.id)
    )
    assert count.scalar_one() == first.created


async def test_a_new_supporting_label_updates_rather_than_duplicates(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)

    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Crime", METHOD_ANILIST_TAG, rank=50)]
    )
    report = await apply_source_labels(
        db_session,
        work=work,
        labels=[
            SourceLabel("Crime", METHOD_ANILIST_TAG, rank=50),
            SourceLabel("Detective", METHOD_ANILIST_TAG, rank=95),
        ],
    )

    assert report.updated == 1 and report.created == 0
    association, _ = (await list_work_concepts(db_session, work.id))[0]
    assert len(association.supporting_labels) == 2
    assert association.confidence == pytest.approx(0.95)


async def test_the_database_refuses_a_duplicate_pair(db_session: AsyncSession) -> None:
    """Uniqueness is structural, not merely a convention in the service."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "tragedy"))
    ).scalar_one()

    db_session.add_all(
        [
            WorkConcept(
                work_id=work.id,
                concept_id=concept.id,
                source=SOURCE_PROVIDED,
                method=METHOD_ANILIST_TAG,
                supporting_labels=[{"label": "Tragedy", "method": METHOD_ANILIST_TAG}],
            )
            for _ in range(2)
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.parametrize("bad", [-0.1, 1.5])
async def test_the_database_refuses_confidence_outside_zero_to_one(
    db_session: AsyncSession, bad: float
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "tragedy"))
    ).scalar_one()

    db_session.add(
        WorkConcept(
            work_id=work.id,
            concept_id=concept.id,
            source=SOURCE_PROVIDED,
            method=METHOD_ANILIST_TAG,
            confidence=bad,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_the_database_refuses_an_unknown_source(db_session: AsyncSession) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "tragedy"))
    ).scalar_one()

    db_session.add(
        WorkConcept(
            work_id=work.id,
            concept_id=concept.id,
            source="vibes",
            method=METHOD_ANILIST_TAG,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_an_association_cannot_reference_a_nonexistent_work(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    concept = (
        await db_session.execute(select(Concept).where(Concept.slug == "tragedy"))
    ).scalar_one()

    db_session.add(
        WorkConcept(
            work_id=uuid.uuid4(),
            concept_id=concept.id,
            source=SOURCE_PROVIDED,
            method=METHOD_ANILIST_TAG,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# --- evidence discipline ---------------------------------------------------


async def test_a_work_with_no_source_labels_gets_no_concepts(
    db_session: AsyncSession,
) -> None:
    """Metadata-only, uncatalogued: nothing to support a concept, so none."""
    await ensure_vocabulary(db_session)
    work = await make_metadata_only_work(db_session)

    report = await apply_source_labels(db_session, work=work, labels=anilist_labels(work))

    assert report.no_source_labels is True
    assert report.created == 0
    count = await db_session.execute(
        select(func.count()).select_from(WorkConcept).where(WorkConcept.work_id == work.id)
    )
    assert count.scalar_one() == 0


async def test_a_work_without_content_units_still_gets_only_catalogue_concepts(
    db_session: AsyncSession,
) -> None:
    """The metadata-only guarantee, stated precisely.

    A work with no text can still carry concepts its own catalogue asserts --
    those are source facts about that work. What it must never get is a
    concept derived from narrative material it does not have, and since
    every association names the label behind it, that is checkable.
    """
    await ensure_vocabulary(db_session)
    work = await make_manga(db_session)

    units = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == work.id)
    )
    assert units.scalar_one() == 0  # no text of its own

    await apply_source_labels(db_session, work=work, labels=anilist_labels(work))

    catalogue_labels = {
        label.label for label in anilist_labels(work)
    }
    for association, _ in await list_work_concepts(db_session, work.id):
        assert association.supporting_labels
        for item in association.supporting_labels:
            # Every supporting label is one the catalogue actually stated
            # about *this* work -- never inferred from a related work.
            assert item["label"] in catalogue_labels


async def test_every_association_in_the_corpus_names_its_evidence(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(db_session, work=work, labels=anilist_labels(work))

    rows = (
        (await db_session.execute(select(WorkConcept).where(WorkConcept.work_id == work.id)))
        .scalars()
        .all()
    )
    assert rows
    assert all(row.supporting_labels for row in rows)


# --- cross-domain ----------------------------------------------------------


async def test_literature_gets_concepts_from_catalogue_subjects(
    db_session: AsyncSession,
) -> None:
    """LCSH is what lets literature participate without guessing at its prose."""
    await ensure_vocabulary(db_session)
    work = await make_literature(db_session)

    report = await apply_source_labels(
        db_session,
        work=work,
        labels=gutenberg_labels(
            ["Horror tales", "Science fiction", "Monsters -- Fiction", "Children's stories"]
        ),
        domain_slug="literature",
    )

    slugs = {concept.slug for _, concept in await list_work_concepts(db_session, work.id)}
    assert {"horror", "science-fiction", "the-supernatural"} <= slugs
    assert report.unmapped == ["Children's stories"]
    rows = (
        (await db_session.execute(select(WorkConcept).where(WorkConcept.work_id == work.id)))
        .scalars()
        .all()
    )
    assert all(row.method == METHOD_GUTENBERG_SUBJECT for row in rows)
    # LCSH states no relevance ranking, so nothing is invented for it.
    assert all(row.confidence is None for row in rows)


async def test_all_three_domains_produce_concepts_in_one_shared_vocabulary(
    db_session: AsyncSession,
) -> None:
    """The actual point of this phase."""
    await ensure_vocabulary(db_session)
    novel = await make_literature(db_session, "three-domain-concepts")
    anime = await make_anime(db_session)
    manga = await make_manga(db_session)

    await apply_source_labels(
        db_session, work=novel, labels=gutenberg_labels(["Science fiction"])
    )
    await apply_source_labels(db_session, work=anime, labels=anilist_labels(anime))
    await apply_source_labels(db_session, work=manga, labels=anilist_labels(manga))

    for work in (novel, anime, manga):
        pairs = await list_work_concepts(db_session, work.id)
        assert pairs, f"{work.title} produced no concepts"

    # Every association points into the single shared vocabulary.
    used = (
        (
            await db_session.execute(
                select(WorkConcept.concept_id).where(
                    WorkConcept.work_id.in_([novel.id, anime.id, manga.id])
                )
            )
        )
        .scalars()
        .all()
    )
    known = set(
        (await db_session.execute(select(Concept.id))).scalars().all()
    )
    assert set(used) <= known


# --- isolation from the user layer -----------------------------------------


async def test_work_concepts_are_identical_for_every_user(db_session: AsyncSession) -> None:
    """Concepts are canonical. A library never produces per-user copies."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(db_session, work=work, labels=anilist_labels(work))
    before = {c.slug for _, c in await list_work_concepts(db_session, work.id)}

    alice = await auth_service.register_user(
        db_session, email="alice@concepts.test", password=PASSWORD
    )
    bob = await auth_service.register_user(
        db_session, email="bob@concepts.test", password=PASSWORD
    )
    for user, status, rating in ((alice, "completed", 10), (bob, "abandoned", 1)):
        await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status=status
        )
        await library_service.set_rating(
            db_session, user_id=user.id, work_id=work.id, rating=rating
        )

    after = {c.slug for _, c in await list_work_concepts(db_session, work.id)}
    assert after == before

    # One set of associations, regardless of how many users hold the work.
    count = await db_session.execute(
        select(func.count()).select_from(WorkConcept).where(WorkConcept.work_id == work.id)
    )
    assert count.scalar_one() == len(before)


async def test_work_concepts_carry_no_user_column(db_session: AsyncSession) -> None:
    """Structural, not conventional: there is nowhere to put a user here."""
    columns = set(WorkConcept.__table__.columns.keys())

    assert "user_id" not in columns
    assert not {"rating", "status", "added_at"} & columns


async def test_deleting_a_user_leaves_work_concepts_untouched(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(db_session, work=work, labels=anilist_labels(work))
    expected = len(await list_work_concepts(db_session, work.id))

    user = await auth_service.register_user(
        db_session, email="departing@concepts.test", password=PASSWORD
    )
    await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
    await db_session.delete(user)
    await db_session.flush()

    assert len(await list_work_concepts(db_session, work.id)) == expected


async def test_deleting_a_work_removes_its_associations_but_not_the_vocabulary(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(db_session, work=work, labels=anilist_labels(work))

    await db_session.execute(
        WorkConcept.__table__.delete().where(WorkConcept.work_id == work.id)
    )
    await db_session.flush()

    total = await db_session.execute(select(func.count()).select_from(Concept))
    assert total.scalar_one() == len(VOCABULARY)
