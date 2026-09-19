"""The normalized representation every domain adapter produces.

This is the boundary between domain-specific sources (a plain-text novel, an
AniList response, a manhwa chapter index) and Noema's storage layer. An
adapter's job is to turn its own source into a `SourceWork`; the ingestion
service's job is to persist a `SourceWork`. Neither knows about the other's
domain.

The shared contract is deliberately the *output* type, not the input:
adapters take whatever their source needs (a file path here, an API id
later) via their constructor and expose a single `load()`.

Everything here represents **source-provided facts only**. Nothing in this
module carries computed scores, similarities, or interpretations -- those
belong to `Relationship`/`Evidence` and are produced by later phases.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceCreator:
    """Someone credited on the work: an author, director, composer, studio.

    Creators are shared across works (many-to-many), which is why a studio
    that animated six series is one row, not six.
    """

    name: str
    role: str = "author"
    external_ids: dict | None = None


@dataclass(frozen=True)
class SourceEntity:
    """A named thing *inside* one work: a character, location, item.

    Scoped to its work, unlike SourceCreator. "Spike Spiegel" is an entity of
    Cowboy Bebop; "Sunrise" is a creator who also made other things.
    """

    name: str
    entity_type: str
    description: str | None = None
    external_ids: dict | None = None
    extra_metadata: dict | None = None


@dataclass(frozen=True)
class SourceRelation:
    """A work-to-work relationship the source explicitly states.

    The target is named by *its* source coordinates rather than a local id,
    because the related work may not be ingested (and may never be). The
    ingestion service resolves these to edges only when both ends exist
    locally; see `resolve_source_relations`.
    """

    predicate: str
    target_source: str
    target_source_ref: str
    extra_metadata: dict | None = None


@dataclass(frozen=True)
class SourceContentUnit:
    """A passage, scene, or panel as the source presents it."""

    unit_type: str
    sequence_number: int
    text_content: str | None
    extra_metadata: dict | None = None


@dataclass(frozen=True)
class SourceContainer:
    """A chapter or episode as the source presents it.

    `content_units` may legitimately be empty: an anime episode is a real
    structural fact even when the source provides no text for it. An empty
    container means "this exists and we have no text", never "this is
    missing" -- and it must never be padded with synopsis or metadata
    standing in for dialogue.
    """

    container_type: str
    sequence_number: int
    title: str | None
    content_units: list[SourceContentUnit] = field(default_factory=list)
    extra_metadata: dict | None = None


@dataclass(frozen=True)
class SourceWork:
    """A whole work, normalized but not yet persisted.

    `source` + `source_ref` together form the idempotency key: re-ingesting
    the same source reference must not create a second Work.
    """

    domain_slug: str
    source: str
    source_ref: str
    title: str
    containers: list[SourceContainer] = field(default_factory=list)
    creators: list[SourceCreator] = field(default_factory=list)
    entities: list[SourceEntity] = field(default_factory=list)
    relations: list[SourceRelation] = field(default_factory=list)
    original_title: str | None = None
    description: str | None = None
    external_ids: dict | None = None
    extra_metadata: dict | None = None

    @property
    def content_unit_count(self) -> int:
        return sum(len(container.content_units) for container in self.containers)


class DomainAdapter(Protocol):
    """What every ingestion adapter must expose.

    Implementations live next to their domain (`literature.py` today;
    `anime.py`, `manhwa.py` later) and are constructed with whatever their
    source requires.
    """

    domain_slug: str
    source_name: str

    def load(self) -> SourceWork: ...
