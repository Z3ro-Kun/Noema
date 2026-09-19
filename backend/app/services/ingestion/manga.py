"""Manga/manhwa adapter: an AniList `Media` (type MANGA) payload -> `SourceWork`.

Deliberately the same shape as the anime adapter, because the sources are the
same shape: AniList supplies identity, structure and metadata; it supplies no
chapter text at all. What differs is only the unit of structure.

  Work           title/description/format/genres/tags/scores, plus chapter
                 and volume counts.
  Container      one per *volume*. Volumes are the published unit a reader
                 recognises and the unit Wikipedia summarises; chapters are
                 recorded as a count on the work rather than as hundreds of
                 empty containers.
  ContentUnit    none from AniList, ever -- same rule as anime. Volume
                 summaries arrive later from Wikipedia, as `summary` tier.
  Creator        staff (author, artist).
  Entity         characters, scoped to the work.
  Relation       AniList's stated relations, resolved to edges only when both
                 ends are ingested.

AniList models manga and manhwa under one type and distinguishes them by
`countryOfOrigin` (JP vs KR), which is preserved in metadata. Both live in
the single `manhwa` domain that covers the combined V1 category.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.ingestion.anime import _clean_text, _format_date, cover_image
from app.services.ingestion.normalized import (
    SourceContainer,
    SourceCreator,
    SourceEntity,
    SourceRelation,
    SourceWork,
)

ADAPTER_NAME = "manga.anilist"
ADAPTER_VERSION = 1
SOURCE_NAME = "anilist"

# The V1 domain covering both manga (JP) and manhwa (KR).
DOMAIN_SLUG = "manhwa"


@dataclass
class AniListMangaAdapter:
    """Normalizes one AniList manga/manhwa payload. Takes a dict, never the network."""

    media: dict

    domain_slug: str = DOMAIN_SLUG
    source_name: str = SOURCE_NAME

    def load(self) -> SourceWork:
        media = self.media
        anilist_id = media.get("id")
        if anilist_id is None:
            raise ValueError("AniList payload has no id; cannot build a stable source_ref")

        titles = media.get("title") or {}
        # AniList gives three titles and they are not interchangeable.
        # English first: it is the name a reader of this product is most
        # likely to recognise, and preferring romaji meant a Korean webtoon
        # arrived as "Na Honjaman Level Up" when AniList had "Solo Leveling"
        # on file the whole time. Romaji is the fallback because it is always
        # present in practice; native is the last resort.
        #
        # This is presentation only. The native title is kept verbatim as
        # `original_title`, all three stay in `extra_metadata.anilist.titles`,
        # and `source_ref` -- not the title -- is what identifies the work.
        title = titles.get("english") or titles.get("romaji") or titles.get("native")
        if not title:
            raise ValueError(f"AniList media {anilist_id} has no usable title")

        return SourceWork(
            domain_slug=self.domain_slug,
            source=self.source_name,
            source_ref=str(anilist_id),
            title=title,
            original_title=titles.get("native"),
            description=_clean_text(media.get("description")),
            containers=self._build_volumes(),
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

    def _build_volumes(self) -> list[SourceContainer]:
        """One container per volume. Never any content units -- see module docstring."""
        volume_count = self.media.get("volumes")
        if not volume_count:
            # Ongoing or uncollected series often have no volume count on
            # AniList. We assert no structure rather than inventing it; any
            # volume summaries found later will report as unmatched.
            return []

        return [
            SourceContainer(
                container_type="volume",
                sequence_number=number,
                title=None,
                content_units=[],
                extra_metadata={"volume_number": number, "has_source_text": False},
            )
            for number in range(1, volume_count + 1)
        ]

    def _build_creators(self) -> list[SourceCreator]:
        creators = []
        for edge in (self.media.get("staff") or {}).get("edges") or []:
            node = edge.get("node") or {}
            name = (node.get("name") or {}).get("full")
            if not name:
                continue
            creators.append(
                SourceCreator(
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
        country = media.get("countryOfOrigin")
        cover_url, cover_provenance = cover_image(media)

        return {
            # Read by `product_service.work_cover_image_url`; absent when
            # AniList supplied no artwork. Same field and same shape as the
            # anime adapter, because it is the same source.
            **({"cover_image_url": cover_url} if cover_url else {}),
            "provenance": {
                "adapter": ADAPTER_NAME,
                "adapter_version": ADAPTER_VERSION,
                "source_name": SOURCE_NAME,
                "source_ref": str(media["id"]),
                "source_url": media.get("siteUrl"),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "license_note": (
                    "Metadata from AniList (CC BY-SA per AniList terms). "
                    "No chapter text or scans are retrieved. Cover artwork "
                    "is referenced by URL only, never copied."
                ),
                **({"cover_image": cover_provenance} if cover_provenance else {}),
            },
            "structure": {
                "containers": len(self._build_volumes()),
                "content_units": 0,
                "content_units_available": False,
                "content_units_unavailable_reason": (
                    "AniList provides no chapter text; volume summaries, where they "
                    "exist, are attached separately from Wikipedia"
                ),
            },
            "anilist": {
                "titles": {
                    "romaji": titles.get("romaji"),
                    "english": titles.get("english"),
                    "native": titles.get("native"),
                },
                "format": media.get("format"),
                "status": media.get("status"),
                "chapter_count": media.get("chapters"),
                "volume_count": media.get("volumes"),
                "start_date": _format_date(media.get("startDate")),
                "end_date": _format_date(media.get("endDate")),
                "country_of_origin": country,
                # AniList's own JP/KR split is what separates manga from
                # manhwa; the domain holds both.
                "comic_tradition": {"JP": "manga", "KR": "manhwa", "CN": "manhua"}.get(country),
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
