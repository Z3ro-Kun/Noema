"""Attaching third-party narrative summaries to existing containers.

This service never creates works or containers. AniList owns anime identity
and structure; Wikipedia only supplies text that hangs off what is already
there. If an episode container does not exist, its summary is reported as
unmatched rather than conjuring a container to hold it.

Three rules it enforces:

  Rights first   Nothing is written unless the recorded rights say storage is
                 permitted. Unknown counts as no.
  Numbers from   Containers are matched on the episode number the source
  the source     stated, corroborated by title. Never on list position.
  Tier always    Everything written here is `summary` tier with a TextSource,
                 so it can never be mistaken for the work's own words.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TEXT_TIER_SUMMARY, Container, ContentUnit, TextSource
from app.services.ingestion.rights import RightsAssessment
from app.services.ingestion.wikipedia import ParsedEpisodeList

SOURCE_NAME_WIKIPEDIA = "wikipedia"
# The shape of the unit: a condensed prose description of a whole episode.
# Its *role* (third-party description, not the work's words) is text_tier.
UNIT_TYPE_SYNOPSIS = "synopsis"

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Apostrophes are dropped rather than turned into separators, so "Queen's"
# and "Queens" normalize alike instead of splitting into different tokens.
_APOSTROPHE_RE = re.compile(r"['‘’ʼ]")


@dataclass
class SummaryIngestionReport:
    """Everything that happened, including what deliberately did not."""

    work_title: str
    page_title: str | None = None
    page_url: str | None = None
    revision_ref: str | None = None
    stored: bool = False
    not_stored_reason: str | None = None
    licence: str | None = None
    containers_examined: int = 0
    summaries_extracted: int = 0
    attached: int = 0
    unchanged: int = 0
    superseded: int = 0
    unmatched: list[dict] = field(default_factory=list)
    parser_skipped: list[dict] = field(default_factory=list)


def normalize_title(title: str | None) -> str:
    """Casefold and strip punctuation/accents so titles compare sanely."""
    if not title:
        return ""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_ish = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_apostrophes = _APOSTROPHE_RE.sub("", ascii_ish.lower())
    return _NON_ALNUM_RE.sub(" ", without_apostrophes).strip()


def titles_agree(left: str | None, right: str | None) -> tuple[bool, str]:
    """Compare two episode titles. Returns (agrees, evidence label).

    Missing titles are not disagreement -- AniList often has no episode
    titles at all -- but two titles that are both present and unrelated are
    strong evidence the numbering schemes differ.
    """
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return True, "title_unavailable"
    if a == b:
        return True, "title_exact"
    if a in b or b in a:
        return True, "title_contained"
    return False, "title_conflict"


def compute_content_hash(text: str) -> str:
    """SHA-256 over normalized text, so line-ending churn isn't a new version."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_or_create_text_source(
    session: AsyncSession,
    *,
    source_name: str,
    source_ref: str,
    source_url: str | None,
    revision_ref: str | None,
    content_hash: str,
    rights: RightsAssessment,
    extra_metadata: dict | None = None,
) -> tuple[TextSource, bool]:
    """Find the row for this exact retrieved version, or record a new one.

    A changed revision or hash produces a *new* row rather than mutating the
    old one, so the retrieval history of a page stays inspectable.
    """
    existing = (
        await session.execute(
            select(TextSource).where(
                TextSource.source_name == source_name,
                TextSource.source_ref == source_ref,
                TextSource.revision_ref == revision_ref,
                TextSource.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    text_source = TextSource(
        source_name=source_name,
        source_ref=source_ref,
        source_url=source_url,
        revision_ref=revision_ref,
        content_hash=content_hash,
        retrieved_at=datetime.now(timezone.utc),
        licence=rights.licence,
        licence_url=rights.licence_url,
        attribution_text=rights.attribution_text,
        requires_attribution=rights.requires_attribution,
        share_alike=rights.share_alike,
        permits_storage=rights.permits_storage,
        rights_basis=rights.basis,
        extra_metadata=extra_metadata,
    )
    session.add(text_source)
    await session.flush()
    return text_source, True


async def _existing_summary_unit(
    session: AsyncSession, container_id, source_name: str
) -> ContentUnit | None:
    """The current summary unit for this container from this source, if any."""
    result = await session.execute(
        select(ContentUnit)
        .join(TextSource, ContentUnit.text_source_id == TextSource.id)
        .where(
            ContentUnit.container_id == container_id,
            ContentUnit.text_tier == TEXT_TIER_SUMMARY,
            TextSource.source_name == source_name,
        )
    )
    return result.scalars().first()


async def attach_episode_summaries(
    session: AsyncSession,
    *,
    work_id,
    work_title: str,
    parsed: ParsedEpisodeList,
    rights: RightsAssessment,
    page_title: str,
    page_url: str | None,
    revision_ref: str | None,
    retrieved_text: str,
    source_name: str = SOURCE_NAME_WIKIPEDIA,
    container_type: str = "episode",
) -> SummaryIngestionReport:
    """Attach parsed summaries to a work's existing containers.

    `container_type` selects which structural unit the summaries describe:
    "episode" for anime, "volume" for manga. The matching rule is identical
    either way -- the number the source stated, corroborated by title.
    """
    report = SummaryIngestionReport(
        work_title=work_title,
        page_title=page_title,
        page_url=page_url,
        revision_ref=revision_ref,
        licence=rights.licence,
        summaries_extracted=len(parsed.summaries),
        parser_skipped=list(parsed.skipped),
    )

    # Rights gate. Checked before anything is written, and before a
    # TextSource row is created, so a refusal leaves no trace of the text.
    if not rights.permits_storage:
        report.stored = False
        report.not_stored_reason = rights.basis
        return report

    containers = (
        (
            await session.execute(
                select(Container)
                .where(Container.work_id == work_id, Container.container_type == container_type)
                .order_by(Container.sequence_number)
            )
        )
        .scalars()
        .all()
    )
    report.containers_examined = len(containers)
    by_number = {container.sequence_number: container for container in containers}

    text_source, _ = await get_or_create_text_source(
        session,
        source_name=source_name,
        source_ref=page_title,
        source_url=page_url,
        revision_ref=revision_ref,
        content_hash=compute_content_hash(retrieved_text),
        rights=rights,
        extra_metadata={"page_title": page_title},
    )

    for summary in parsed.summaries:
        container = by_number.get(summary.episode_number)
        if container is None:
            report.unmatched.append(
                {
                    "episode_number": summary.episode_number,
                    "title": summary.title,
                    "reason": f"no_{container_type}_container_with_that_number",
                }
            )
            continue

        agrees, evidence = titles_agree(container.title, summary.title)
        if not agrees:
            # Same number, unrelated titles: the two sources are numbering
            # differently. Attaching here would put the wrong text on the
            # wrong episode, so decline and report it.
            report.unmatched.append(
                {
                    "episode_number": summary.episode_number,
                    "title": summary.title,
                    "container_title": container.title,
                    "reason": "title_conflict",
                }
            )
            continue

        unit_metadata = {
            "match_evidence": evidence,
            "source_episode_number": summary.episode_number,
            "source_episode_title": summary.title,
        }

        existing = await _existing_summary_unit(session, container.id, source_name)
        if existing is None:
            session.add(
                ContentUnit(
                    container_id=container.id,
                    unit_type=UNIT_TYPE_SYNOPSIS,
                    sequence_number=1,
                    text_content=summary.summary,
                    text_tier=TEXT_TIER_SUMMARY,
                    text_source_id=text_source.id,
                    extra_metadata=unit_metadata,
                )
            )
            report.attached += 1
        elif existing.text_source_id == text_source.id:
            report.unchanged += 1
        else:
            # A newer revision of the page. Point the unit at the new
            # TextSource; the old row stays as retrieval history.
            existing.text_content = summary.summary
            existing.text_source_id = text_source.id
            existing.extra_metadata = unit_metadata
            report.superseded += 1

    await session.flush()
    report.stored = True
    return report
