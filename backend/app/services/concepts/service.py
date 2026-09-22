"""Populating work-level concepts from source-provided labels.

One rule governs this whole module: **a work is characterized only by labels
its own source actually supplies about it.** Nothing is inferred from the
franchise, from a related work, or from general knowledge, and nothing is
derived from text a work does not have. A work with no source labels ends up
with no concepts, which is the correct outcome and is reported as such.

Two label sources, one per kind of work:

  AniList genres and tags   anime, manga and manhwa. Already stored on the
                            work by the ingestion adapters, so this reads
                            what is in the database and touches no network.
  Gutenberg LCSH subjects   literature. Fetched from the catalogue by the
                            caller and passed in; the service never fetches.

Both are externally curated controlled vocabularies, which is what lets the
two sit in one concept space without either being guessed at.

Labels outside the vocabulary are *reported*, never invented into concepts.
Most AniList tags are demographic or advisory ("Shounen", "Gore") and most
unmapped LCSH headings name characters or readerships ("Alice (Fictitious
character...)", "Children's stories"); neither is a narrative feature.

Re-running is a no-op: an existing association is updated in place with any
newly supporting labels rather than duplicated, and the unique constraint on
(work_id, concept_id) makes that structural rather than conventional.

**Convergence runs both ways.** An association whose supporting labels no
longer map to it is withdrawn rather than left behind. Without that, the
vocabulary stops being the single source of truth the moment an alias is
corrected: the label stops producing new rows while every row it already
produced stays, and the database keeps asserting something the vocabulary no
longer says. Withdrawal is reported per work and is the only path here that
removes anything.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    METHOD_ANILIST_GENRE,
    METHOD_ANILIST_TAG,
    METHOD_GUTENBERG_SUBJECT,
    SOURCE_PROVIDED,
    Concept,
    Work,
    WorkConcept,
)
from app.services.concepts.vocabulary import VOCABULARY, ConceptDefinition, resolve_source_label


@dataclass
class SourceLabel:
    """One label a source stated about a work, before mapping."""

    label: str
    method: str
    # The source's own relevance for this label, 0-100, where it states one.
    # AniList tags carry a community rank; genres and LCSH subjects do not.
    rank: int | None = None


@dataclass
class WorkConceptReport:
    """What happened for one work, including what deliberately did not."""

    work_id: uuid.UUID
    work_title: str
    domain_slug: str | None = None
    labels_examined: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # Labels the vocabulary has no entry for. A coverage gap made visible
    # rather than filled in with a guess.
    unmapped: list[str] = field(default_factory=list)
    # Set when a work supplies no labels at all -- e.g. a metadata-only work
    # whose source catalogues no narrative labels.
    no_source_labels: bool = False
    # Associations withdrawn because the vocabulary no longer maps any of the
    # labels that supported them. Named, never silent: this is the one path
    # that removes a characterization, and a run that removes something has
    # to say what and why.
    withdrawn: list[str] = field(default_factory=list)


async def ensure_vocabulary(session: AsyncSession) -> tuple[int, int]:
    """Make every vocabulary entry exist as a Concept row. Idempotent.

    Matched on `slug`, never on `name`, so rewording a display name updates
    the existing row instead of creating a second one.
    """
    created = updated = 0
    existing = {
        concept.slug: concept
        for concept in (await session.execute(select(Concept))).scalars().all()
    }

    for entry in VOCABULARY:
        concept = existing.get(entry.slug)
        if concept is None:
            session.add(
                Concept(
                    slug=entry.slug,
                    name=entry.name,
                    concept_type=entry.concept_type,
                    description=entry.description,
                )
            )
            created += 1
            continue

        if (concept.name, concept.concept_type, concept.description) != (
            entry.name,
            entry.concept_type,
            entry.description,
        ):
            concept.name = entry.name
            concept.concept_type = entry.concept_type
            concept.description = entry.description
            updated += 1

    await session.flush()
    return created, updated


async def concepts_by_slug(session: AsyncSession) -> dict[str, Concept]:
    rows = (await session.execute(select(Concept))).scalars().all()
    return {concept.slug: concept for concept in rows}


def anilist_labels(work: Work) -> list[SourceLabel]:
    """Genre and tag labels AniList stated about this work.

    Reads what ingestion already stored. Genres carry no relevance; tags
    carry AniList's community `rank`, which is preserved as stated.
    """
    anilist = ((work.extra_metadata or {}).get("anilist")) or {}
    labels = [
        SourceLabel(label=genre, method=METHOD_ANILIST_GENRE)
        for genre in (anilist.get("genres") or [])
        if genre
    ]
    for tag in anilist.get("tags") or []:
        name = tag.get("name")
        if not name:
            continue
        rank = tag.get("rank")
        labels.append(
            SourceLabel(
                label=name,
                method=METHOD_ANILIST_TAG,
                rank=int(rank) if isinstance(rank, (int, float)) else None,
            )
        )
    return labels


def gutenberg_labels(subjects: list[str]) -> list[SourceLabel]:
    """LCSH subject headings, which state no relevance ranking."""
    return [
        SourceLabel(label=subject, method=METHOD_GUTENBERG_SUBJECT)
        for subject in subjects
        if subject
    ]


def _confidence(rank: int | None) -> float | None:
    """Rescale a source's stated relevance to 0-1, or None if it stated none.

    Never substitutes a default. A missing rank means the source expressed no
    opinion about relevance, and inventing one would turn an absence into a
    measurement.
    """
    if rank is None:
        return None
    return max(0.0, min(100.0, float(rank))) / 100.0


def _evidence(label: SourceLabel) -> dict:
    return {"label": label.label, "method": label.method, "rank": label.rank}


def _merge(existing: list | None, addition: dict) -> tuple[list, bool]:
    """Append an evidence entry unless an identical one is already present."""
    entries = list(existing or [])
    if addition in entries:
        return entries, False
    entries.append(addition)
    # Stable order so a re-run cannot reshuffle stored provenance.
    entries.sort(key=lambda item: (item.get("method") or "", item.get("label") or ""))
    return entries, True


async def apply_source_labels(
    session: AsyncSession,
    *,
    work: Work,
    labels: list[SourceLabel],
    domain_slug: str | None = None,
) -> WorkConceptReport:
    """Associate a work with the concepts its source labels map to.

    Several labels can support one concept (Death Note carries "Crime",
    "Detective" and "Police"). They collapse onto a single association whose
    `supporting_labels` records each of them, so nothing is overwritten. The
    stored `confidence` is the highest relevance any supporting label
    carried, and stays NULL while no supporting label carries one.
    """
    report = WorkConceptReport(
        work_id=work.id,
        work_title=work.title,
        domain_slug=domain_slug,
        labels_examined=len(labels),
        no_source_labels=not labels,
    )

    resolved: dict[str, list[SourceLabel]] = {}
    for label in labels:
        definition: ConceptDefinition | None = resolve_source_label(label.label)
        if definition is None:
            report.unmapped.append(label.label)
            continue
        resolved.setdefault(definition.slug, []).append(label)

    vocabulary = await concepts_by_slug(session)
    existing_rows = {
        row.concept_id: row
        for row in (
            await session.execute(select(WorkConcept).where(WorkConcept.work_id == work.id))
        )
        .scalars()
        .all()
    }

    await _withdraw_unsupported(session, report, existing_rows, vocabulary)

    if not resolved:
        await session.flush()
        return report

    for slug, supporting in resolved.items():
        concept = vocabulary.get(slug)
        if concept is None:
            # ensure_vocabulary() has not been run. Refusing is better than
            # creating a concept row from inside the population path, which
            # is how uncontrolled vocabularies start.
            raise LookupError(
                f"concept {slug!r} is not in the database; run ensure_vocabulary() first"
            )

        ranks = [label.rank for label in supporting if label.rank is not None]
        confidence = _confidence(max(ranks)) if ranks else None
        # The method recorded on the row is the one that supplied the
        # best-ranked label, falling back to the first seen; every method is
        # preserved in supporting_labels regardless.
        primary = max(supporting, key=lambda item: (item.rank is not None, item.rank or 0))

        row = existing_rows.get(concept.id)
        if row is None:
            entries: list[dict] = []
            for label in supporting:
                entries, _ = _merge(entries, _evidence(label))
            session.add(
                WorkConcept(
                    work_id=work.id,
                    concept_id=concept.id,
                    source=SOURCE_PROVIDED,
                    method=primary.method,
                    confidence=confidence,
                    supporting_labels=entries,
                )
            )
            report.created += 1
            continue

        # Evidence the vocabulary has since stopped reading as this concept is
        # dropped from the row for the same reason the row itself would be
        # dropped if nothing were left: `supporting_labels` is the list of
        # labels supporting *this* concept, and a label that no longer maps
        # here is not one of them. Nothing is lost that the source still says
        # -- the label is still on the work, and still reported as unmapped or
        # attributed to whatever it does mean now.
        recorded = list(row.supporting_labels or [])
        entries = [
            entry
            for entry in recorded
            if (resolve_source_label(entry.get("label") or "") or _NOTHING).slug == slug
        ]
        changed = len(entries) != len(recorded)
        for label in supporting:
            entries, added = _merge(entries, _evidence(label))
            changed = changed or added
        if changed:
            row.supporting_labels = entries
        if row.confidence != confidence:
            row.confidence = confidence
            changed = True
        if row.method != primary.method:
            row.method = primary.method
            changed = True

        if changed:
            report.updated += 1
        else:
            report.unchanged += 1

    await session.flush()
    return report


async def _withdraw_unsupported(
    session: AsyncSession,
    report: WorkConceptReport,
    existing_rows: dict[uuid.UUID, WorkConcept],
    vocabulary: dict[str, Concept],
) -> None:
    """Remove associations the vocabulary no longer supports.

    The test is deliberately narrow: a row goes only when **every** label
    recorded as supporting it now resolves somewhere other than this concept,
    or nowhere at all. So correcting an alias withdraws exactly the rows that
    alias produced, and a row still backed by one good label survives with
    its other evidence intact.

    A row with no recorded supporting labels is left alone. Those predate the
    provenance field or were written by something other than this path, and
    deleting on an absence of evidence is the opposite of what this module is
    for.
    """
    by_id = {concept.id: slug for slug, concept in vocabulary.items()}

    for concept_id, row in list(existing_rows.items()):
        labels = row.supporting_labels or []
        if not labels:
            continue

        slug = by_id.get(concept_id)
        if slug is None:
            # The concept itself is gone from the vocabulary. Not this
            # function's business -- removing a Concept row is a separate,
            # deliberate act -- so the association is left for a human.
            continue

        still_supported = any(
            (resolve_source_label(entry.get("label") or "") or _NOTHING).slug == slug
            for entry in labels
        )
        if still_supported:
            continue

        report.withdrawn.append(slug)
        await session.delete(row)
        del existing_rows[concept_id]


class _Nothing:
    """A definition-shaped absence, so the check above needs no None branch."""

    slug = None


_NOTHING = _Nothing()


async def list_work_concepts(
    session: AsyncSession, work_id: uuid.UUID
) -> list[tuple[WorkConcept, Concept]]:
    """A work's concepts, strongest stated relevance first.

    Ranked rows come before unranked ones rather than being mixed with them:
    a NULL confidence means the source stated no relevance, which is not the
    same as stating a low one.
    """
    rows = await session.execute(
        select(WorkConcept, Concept)
        .join(Concept, WorkConcept.concept_id == Concept.id)
        .where(WorkConcept.work_id == work_id)
        .order_by(WorkConcept.confidence.desc().nullslast(), Concept.name)
    )
    return [(row[0], row[1]) for row in rows.all()]
