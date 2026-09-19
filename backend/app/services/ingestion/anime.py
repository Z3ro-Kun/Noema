"""Anime adapter: an AniList `Media` payload -> `SourceWork`.

What maps where, and why:

  Work           title/description/format/season/genres/tags/scores.
                 AniList is metadata-rich at this level.
  Container      one per episode. AniList states the episode count, so the
                 episodes are a source fact even though their *content* is
                 not available. Titles are filled in from streamingEpisodes
                 where one can be matched to an episode number.
  ContentUnit    none, ever. AniList provides no episode text, and neither a
                 synopsis nor metadata is a substitute for dialogue. Anime
                 episodes are therefore containers with zero content units
                 (see docs/architecture.md, "What did not fit").
  Creator        studios and staff -- credited across works, many-to-many.
  Entity         characters -- scoped to this one work.
  Relation       AniList's own relations (SEQUEL, SIDE_STORY, ...), emitted
                 by source coordinates and resolved to edges later.

Every field is treated as optional: AniList returns nulls for unaired shows,
sparse entries, and anything its contributors haven't filled in.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.ingestion.normalized import (
    SourceContainer,
    SourceCreator,
    SourceEntity,
    SourceRelation,
    SourceWork,
)

ADAPTER_NAME = "anime.anilist"
ADAPTER_VERSION = 1
SOURCE_NAME = "anilist"

# "Episode 12 - Jupiter Jazz (Part 1)" -> 12. Anything we can't confidently
# match is left out rather than guessed at.
_EPISODE_NUMBER_RE = re.compile(r"\bepisode\s+(\d+)\b", re.IGNORECASE)


def _clean_text(value: str | None) -> str | None:
    """AniList descriptions carry <br> and stray whitespace even asHtml:false."""
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _format_date(date: dict | None) -> str | None:
    if not date or not date.get("year"):
        return None
    parts = [date["year"], date.get("month"), date.get("day")]
    if parts[1] is None:
        return f"{parts[0]:04d}"
    if parts[2] is None:
        return f"{parts[0]:04d}-{parts[1]:02d}"
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"


def _episode_titles(streaming_episodes: list[dict]) -> dict[int, dict]:
    """Map episode number -> streaming entry, for entries we can number."""
    titles: dict[int, dict] = {}
    for entry in streaming_episodes or []:
        raw_title = (entry or {}).get("title") or ""
        match = _EPISODE_NUMBER_RE.search(raw_title)
        if not match:
            continue
        number = int(match.group(1))
        # Strip the "Episode N - " prefix, keeping the episode's actual name.
        name = raw_title[match.end() :].lstrip(" -–—:").strip() or None
        titles.setdefault(number, {"title": name, "site": entry.get("site")})
    return titles


@dataclass
class AniListAnimeAdapter:
    """Normalizes one AniList `Media` payload. Takes a dict, never the network."""

    media: dict

    domain_slug: str = "anime"
    source_name: str = SOURCE_NAME

    def load(self) -> SourceWork:
        media = self.media
        anilist_id = media.get("id")
        if anilist_id is None:
            raise ValueError("AniList payload has no id; cannot build a stable source_ref")

        titles = media.get("title") or {}
        # Prefer romaji as the canonical title (always present in practice),
        # falling back rather than assuming any one variant exists.
        title = titles.get("romaji") or titles.get("english") or titles.get("native")
        if not title:
            raise ValueError(f"AniList media {anilist_id} has no usable title")

        return SourceWork(
            domain_slug=self.domain_slug,
            source=self.source_name,
            source_ref=str(anilist_id),
            title=title,
            original_title=titles.get("native"),
            description=_clean_text(media.get("description")),
            containers=self._build_episodes(),
            creators=self._build_creators(),
            entities=self._build_characters(),
            relations=self._build_relations(),
            external_ids=self._build_external_ids(),
            extra_metadata=self._build_metadata(),
        )

    def _build_external_ids(self) -> dict:
        external_ids = {
            "source_ref": str(self.media["id"]),
            "anilist_id": self.media["id"],
        }
        if self.media.get("idMal") is not None:
            external_ids["mal_id"] = self.media["idMal"]
        return external_ids

    def _build_episodes(self) -> list[SourceContainer]:
        """One container per episode. Never any content units -- see module docstring."""
        episode_count = self.media.get("episodes")
        streaming = _episode_titles(self.media.get("streamingEpisodes") or [])

        if not episode_count:
            # Unaired or unknown-length: we know of no episodes, so we assert
            # none. The raw value is still recorded in work metadata.
            return []

        containers = []
        for number in range(1, episode_count + 1):
            entry = streaming.get(number)
            metadata = {
                "episode_number": number,
                "has_source_text": False,
                "title_source": "anilist_streaming_episode" if entry else None,
            }
            if entry and entry.get("site"):
                metadata["streaming_site"] = entry["site"]
            containers.append(
                SourceContainer(
                    container_type="episode",
                    sequence_number=number,
                    title=entry["title"] if entry else None,
                    content_units=[],
                    extra_metadata=metadata,
                )
            )
        return containers

    def _build_creators(self) -> list[SourceCreator]:
        creators: list[SourceCreator] = []

        for edge in (self.media.get("studios") or {}).get("edges") or []:
            node = edge.get("node") or {}
            name = node.get("name")
            if not name:
                continue
            creators.append(
                SourceCreator(
                    name=name,
                    role="studio" if edge.get("isMain") else "production_company",
                    external_ids={"anilist_studio_id": node.get("id")},
                )
            )

        for edge in (self.media.get("staff") or {}).get("edges") or []:
            node = edge.get("node") or {}
            name = (node.get("name") or {}).get("full")
            if not name:
                continue
            creators.append(
                SourceCreator(
                    # AniList roles are free text ("Director", "Music",
                    # "Theme Song Composition (OP, ED1-ED3)"); stored as given
                    # and truncated to the column width rather than bucketed
                    # into a taxonomy we'd be inventing.
                    name=name,
                    role=(edge.get("role") or "staff")[:64],
                    external_ids={"anilist_staff_id": node.get("id")},
                )
            )

        return creators

    def _build_characters(self) -> list[SourceEntity]:
        entities = []
        for edge in (self.media.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            name = (node.get("name") or {}).get("full")
            if not name:
                continue
            entities.append(
                SourceEntity(
                    name=name,
                    entity_type="character",
                    description=_clean_text(node.get("description")),
                    external_ids={"anilist_character_id": node.get("id")},
                    extra_metadata={
                        "role": edge.get("role"),
                        "native_name": (node.get("name") or {}).get("native"),
                        "anilist_url": node.get("siteUrl"),
                    },
                )
            )
        return entities

    def _build_relations(self) -> list[SourceRelation]:
        """AniList's stated relations, including ones we may never resolve.

        Targets of any media type are emitted (a manga adaptation is still a
        fact AniList asserts); resolution to an actual edge happens only if
        that work is ingested.
        """
        relations = []
        for edge in (self.media.get("relations") or {}).get("edges") or []:
            node = edge.get("node") or {}
            relation_type = edge.get("relationType")
            if not relation_type or node.get("id") is None:
                continue
            relations.append(
                SourceRelation(
                    predicate=relation_type.lower(),
                    target_source=SOURCE_NAME,
                    target_source_ref=str(node["id"]),
                    extra_metadata={
                        "target_media_type": node.get("type"),
                        "target_format": node.get("format"),
                        "target_title": (node.get("title") or {}).get("romaji"),
                    },
                )
            )
        return relations

    def _build_metadata(self) -> dict:
        media = self.media
        titles = media.get("title") or {}

        return {
            "provenance": {
                "adapter": ADAPTER_NAME,
                "adapter_version": ADAPTER_VERSION,
                "source_name": SOURCE_NAME,
                "source_ref": str(media["id"]),
                "source_url": media.get("siteUrl"),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "license_note": (
                    "Metadata from AniList (CC BY-SA per AniList terms). "
                    "No episode text, subtitles, or media are retrieved."
                ),
            },
            "structure": {
                "containers": len(self._build_episodes()),
                "content_units": 0,
                # The single most important fact about anime in Noema today.
                "content_units_available": False,
                "content_units_unavailable_reason": (
                    "AniList provides no episode text; no dialogue source is ingested"
                ),
            },
            # Source facts, kept verbatim. Genres and tags are AniList's own
            # classifications -- they are NOT computed observations, and must
            # never be presented as something Noema inferred.
            "anilist": {
                "titles": {
                    "romaji": titles.get("romaji"),
                    "english": titles.get("english"),
                    "native": titles.get("native"),
                },
                "format": media.get("format"),
                "status": media.get("status"),
                "episode_count": media.get("episodes"),
                "episode_duration_minutes": media.get("duration"),
                "season": media.get("season"),
                "season_year": media.get("seasonYear"),
                "start_date": _format_date(media.get("startDate")),
                "end_date": _format_date(media.get("endDate")),
                "country_of_origin": media.get("countryOfOrigin"),
                "adapted_from": media.get("source"),
                "genres": media.get("genres") or [],
                "tags": [
                    {
                        "name": tag.get("name"),
                        "rank": tag.get("rank"),
                        "category": tag.get("category"),
                        "is_spoiler": tag.get("isGeneralSpoiler"),
                    }
                    for tag in media.get("tags") or []
                    if tag.get("name")
                ],
                "average_score": media.get("averageScore"),
                "popularity": media.get("popularity"),
            },
        }
