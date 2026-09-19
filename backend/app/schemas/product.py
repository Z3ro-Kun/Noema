"""The user-facing product surface.

Everything the internal catalogue schemas expose that a reader has no use for
-- adapter names, ingestion provenance, structure flags, raw source tags with
community ranks, container counts, content units -- stops here. These shapes
are the contract the eventual V1 frontend is built against.

The separation the whole phase turns on is structural rather than
conventional: `ProductWork` is canonical and identical for every viewer,
`UserWorkState` belongs to exactly one person, and `WorkPresentation` holds
them as two distinct objects. A response can carry both for convenience
without either becoming able to change the other.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProductCreator(BaseModel):
    """A credit worth showing a reader.

    Not every credit: a work carries up to 33 of them, most being translators,
    letterers and per-episode animators. See `product_service` for which
    roles are considered product-facing and why.
    """

    name: str
    # Normalized for display -- "Author", "Story & Art", "Director", "Studio".
    role: str


class ProductConcept(BaseModel):
    """A concept as the product shows it.

    Deliberately not the association row. `supporting_labels`, the raw source
    wording and the community rank behind `confidence` are ingestion
    provenance; they stay internal until a product requirement asks for them.
    """

    slug: str
    name: str
    # "theme" | "motif" | "genre" -- lets a client group themes separately
    # from genre-like concepts without hardcoding the vocabulary.
    concept_type: str


class ProductDomain(BaseModel):
    slug: str
    name: str


class ProductWork(BaseModel):
    """Canonical work data. Identical for every user, and for anonymous ones.

    Nothing on this model depends on who is asking.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_title: str | None
    domain: ProductDomain

    # The source's own words about the work. Null where no source supplied a
    # description -- never the opening of the work's own text, which would be
    # corpus content rather than a synopsis.
    synopsis: str | None
    # Null throughout the current corpus: no ingested source recorded cover
    # art. Represented as a clean absence, never a guessed URL.
    cover_image_url: str | None

    # Source-native genre labels, e.g. AniList's "Psychological". Empty when
    # the source states none -- Gutenberg has no genre field.
    genres: list[str]
    # Noema's normalized cross-domain vocabulary, which covers every domain
    # including literature. Distinct from `genres`, which is one source's
    # wording rather than a shared vocabulary.
    concepts: list[ProductConcept]
    creators: list[ProductCreator]

    # Publication facts a reader recognises. Null where unknown.
    media_format: str | None
    year: int | None

    # Where this work's record came from ("anilist", "gutenberg"). Kept
    # because attribution is owed; internal ids and adapter details are not.
    source: str | None


class UserWorkState(BaseModel):
    """One user's own relationship with a work. Never shown to anyone else."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    # Null means unrated, which is not a low rating.
    rating: int | None
    rated_at: datetime | None

    added_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    abandoned_at: datetime | None
    removed_at: datetime | None

    times_started: int
    times_completed: int
    in_library: bool


class WorkPresentation(BaseModel):
    """A work, plus the requesting user's state where there is one.

    Two objects rather than a flattened one, so nothing user-specific can
    ever be mistaken for a property of the work. `user_state` is null for an
    anonymous request and for a user who has no interaction with this work;
    `work` is byte-identical either way.
    """

    work: ProductWork
    user_state: UserWorkState | None = None
