"""Attaching third-party narrative summaries to what already exists.

This service never creates works or containers. AniList owns anime identity
and structure; Wikipedia only supplies text that hangs off what is already
there. If an episode container does not exist, its summary is reported as
unmatched rather than conjuring a container to hold it.

Two shapes of attachment, and the second exists because of that refusal:

    attach_episode_summaries   per-container text, matched to the container
                               by the number the source itself stated.
    attach_work_summary        one summary of the whole work, for a work the
                               canonical source catalogues no containers for
                               at all. The fallback, gated as one.

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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TEXT_TIER_SUMMARY, Container, ContentUnit, TextSource
from app.services.ingestion.rights import RightsAssessment
from app.services.ingestion.wikipedia import ParsedEpisodeList, ParsedWorkSummary

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
    # Same page revision, different cleaned text: the cleaner improved and the
    # stored prose followed it. Counted apart from `superseded`, which means
    # the source itself changed.
    refreshed: int = 0
    unmatched: list[dict] = field(default_factory=list)
    parser_skipped: list[dict] = field(default_factory=list)
    # The work-level fallback that stopped this run, when one did. Named so an
    # operator can look at it; nothing here acts on it.
    blocking_work_level_unit_id: uuid.UUID | None = None


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

    # The other half of the fallback rule. `attach_work_summary` refuses a
    # work whose containers already hold text; this refuses a work that
    # already holds the whole-work fallback. Without both halves the order of
    # two ingestion runs would decide whether a work ends up describing
    # itself twice -- once per volume and once entire -- in the same retrieval
    # space, which is the duplicate the fallback exists to avoid.
    #
    # It refuses rather than resolves. Deleting the fallback would destroy
    # stored text on the strength of an ingestion run's say-so, and
    # re-parenting it into a container would attach whole-work prose to one
    # episode. Both are decisions for a person, so the unit is named in the
    # report and nothing is touched.
    fallback = await _existing_work_summary_unit(session, work_id, source_name)
    if fallback is not None:
        report.stored = False
        report.not_stored_reason = "work_has_a_work_level_fallback_summary"
        report.blocking_work_level_unit_id = fallback.id
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
            if existing.text_content != summary.summary:
                # Same revision of the same page, different prose out of the
                # cleaner. The stored text is meant to be the current reading
                # of the recorded revision, so it follows -- and says so
                # separately, because nothing about the source changed.
                existing.text_content = summary.summary
                existing.extra_metadata = unit_metadata
                report.refreshed += 1
            else:
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


# --- work-level summaries ------------------------------------------------
#
# The list-article paths corroborate every summary twice: the number the
# source stated, and the title beside it. A whole-work summary has no
# per-entry evidence at all -- one article, one blob of prose -- so the only
# question that can be asked is whether the article is about this work, and it
# has to be asked carefully.
#
# Titles alone will not do it. "Bastard" is a Korean webtoon by Carnby Kim and
# also "Bastard!!", a Japanese manga by Kazushi Hagiwara, and the two titles
# normalize to the same string. Attaching one's plot to the other would be
# precisely the fabrication this whole feature was built to avoid.
#
# So the creators AniList already recorded are the second witness. Not by
# matching names exactly -- romanisations differ, and "Kentarou Miura" and
# "Kentaro Miura" are the same person -- but by asking whether any distinctive
# piece of any credited name appears in the article at all.

# Below this a name fragment is not distinctive enough to be evidence of
# anything: "kim", "lee" and "ito" appear in articles about everyone.
DISTINCTIVE_NAME_TOKEN_CHARS = 5


def distinctive_creator_tokens(creator_names: list[str]) -> set[str]:
    """The parts of these names that would mean something if they turned up."""
    return {
        token
        for name in creator_names
        for token in normalize_title(name).split()
        if len(token) >= DISTINCTIVE_NAME_TOKEN_CHARS
    }


def corroborate_work_article(
    *, page_title: str, page_text: str, work_titles: list[str | None], creator_names: list[str]
) -> tuple[bool, str]:
    """Is this article about this work? Returns (yes, the evidence for saying so).

    Two witnesses where two exist. The title must agree with one the source
    recorded -- a disambiguator like "(webtoon)" is not disagreement -- and
    some distinctive piece of some credited creator's name must appear in the
    article. Where the credits offer nothing distinctive enough to look for,
    the title stands alone and the caller is told so.
    """
    # "Noblesse (manhwa)" is the same title as "Noblesse", more precisely said.
    bare = page_title.split("(")[0]

    # Only titles that survive normalization can be compared. A native title
    # in Japanese or Korean normalizes to nothing, and `titles_agree` reads
    # "nothing" as "no evidence either way" -- which is the right answer for
    # an episode with no recorded title, and quite the wrong one here, where
    # it would turn the gate into a rubber stamp for every work the source
    # happens to hold a CJK title for.
    comparable = [title for title in work_titles if title and normalize_title(title)]
    if not normalize_title(bare) or not comparable:
        return False, "title_incomparable"
    if not any(titles_agree(bare, title)[0] for title in comparable):
        return False, "title_conflict"

    wanted = distinctive_creator_tokens(creator_names)
    if not wanted:
        return True, "title_only"

    present = set(normalize_title(page_text).split())
    found = wanted & present
    if found:
        # The longest match, because a credit list runs to translators and
        # studios and the reader of this label wants the most distinctive
        # thing that actually corroborated, not the alphabetically first.
        return True, f"creator_named:{max(found, key=lambda token: (len(token), token))}"
    return False, "creator_conflict"


@dataclass
class WorkSummaryIngestionReport:
    """Everything that happened for one work, including what deliberately did not."""

    work_title: str
    page_title: str | None = None
    page_url: str | None = None
    revision_ref: str | None = None
    section: str | None = None
    licence: str | None = None
    # How the article was established to be about this work, so a later
    # reader can re-check the identification rather than trust it.
    identified_by: str | None = None
    stored: bool = False
    not_stored_reason: str | None = None
    attached: int = 0
    unchanged: int = 0
    superseded: int = 0
    parser_skipped: list[dict] = field(default_factory=list)


async def _existing_work_summary_unit(
    session: AsyncSession, work_id, source_name: str
) -> ContentUnit | None:
    """The current work-level summary unit for this work from this source."""
    result = await session.execute(
        select(ContentUnit)
        .join(TextSource, ContentUnit.text_source_id == TextSource.id)
        .where(
            ContentUnit.work_id == work_id,
            ContentUnit.container_id.is_(None),
            ContentUnit.text_tier == TEXT_TIER_SUMMARY,
            TextSource.source_name == source_name,
        )
    )
    return result.scalars().first()


async def work_has_container_text(session: AsyncSession, work_id) -> bool:
    """Does any container of this work already hold text?"""
    result = await session.execute(
        select(ContentUnit.id)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(
            Container.work_id == work_id,
            ContentUnit.text_content.is_not(None),
            ContentUnit.text_content != "",
        )
        .limit(1)
    )
    return result.scalars().first() is not None


async def attach_work_summary(
    session: AsyncSession,
    *,
    work_id,
    work_title: str,
    parsed: ParsedWorkSummary,
    rights: RightsAssessment,
    page_title: str,
    page_url: str | None,
    revision_ref: str | None,
    retrieved_text: str,
    identified_by: str = "unrecorded",
    source_name: str = SOURCE_NAME_WIKIPEDIA,
) -> WorkSummaryIngestionReport:
    """Attach one whole-work summary to a work that has nowhere else to put it.

    This is the fallback, and it is gated as one. A work whose containers
    already hold text is refused: the same narrative at two granularities in
    one embedding space is a duplicate, not better coverage. The route to more
    coverage for such a work stays what it was -- more per-container summaries
    from the source that has them.

    The same three rules as the container path apply, unchanged. Rights are
    checked before a TextSource row exists, so a refusal leaves no trace of
    the text. The unit is `summary` tier with a TextSource, so it can never be
    mistaken for the work's own words. And nothing here creates a work, a
    container, or a number: it records that this text describes this work.
    """
    report = WorkSummaryIngestionReport(
        work_title=work_title,
        page_title=page_title,
        page_url=page_url,
        revision_ref=revision_ref,
        licence=rights.licence,
        section=parsed.summary.section if parsed.summary else None,
        identified_by=identified_by,
        parser_skipped=list(parsed.skipped),
    )

    if not rights.permits_storage:
        report.not_stored_reason = rights.basis
        return report

    if parsed.summary is None:
        report.not_stored_reason = "no_work_level_summary_on_the_page"
        return report

    if await work_has_container_text(session, work_id):
        report.not_stored_reason = "work_already_has_container_level_text"
        return report

    text_source, _ = await get_or_create_text_source(
        session,
        source_name=source_name,
        source_ref=page_title,
        source_url=page_url,
        revision_ref=revision_ref,
        content_hash=compute_content_hash(parsed.summary.summary),
        rights=rights,
        extra_metadata={"page_title": page_title, "section": parsed.summary.section},
    )

    unit_metadata = {
        "scope": "work",
        "source_section": parsed.summary.section,
        "source_page_title": page_title,
        "identified_by": identified_by,
    }

    existing = await _existing_work_summary_unit(session, work_id, source_name)
    if existing is None:
        session.add(
            ContentUnit(
                container_id=None,
                work_id=work_id,
                unit_type=UNIT_TYPE_SYNOPSIS,
                # There is only ever one of these per work per source, so the
                # sequence number carries no ordering -- it is 1 because the
                # column is not nullable, not because there is a series.
                sequence_number=1,
                text_content=parsed.summary.summary,
                text_tier=TEXT_TIER_SUMMARY,
                text_source_id=text_source.id,
                extra_metadata=unit_metadata,
            )
        )
        report.attached = 1
    elif existing.text_source_id == text_source.id:
        report.unchanged = 1
    else:
        existing.text_content = parsed.summary.summary
        existing.text_source_id = text_source.id
        existing.extra_metadata = unit_metadata
        report.superseded = 1

    await session.flush()
    report.stored = True
    return report
