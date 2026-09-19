"""Embeddings produced by a candidate model under evaluation.

Kept out of `embeddings` on purpose. The production table is `vector(384)`,
and widening it to an unconstrained `vector` so a 768-d model could share it
would put a real trap in the production search path: pgvector raises
`different vector dimensions` on any query that compares rows of differing
width, so a single query that forgot to filter by model would start failing
at runtime. Measured, not assumed -- an unfiltered mixed-dimension query
errors, a model-filtered one does not.

So candidate models write here instead. The production baseline stays
byte-for-byte intact, which is what makes the comparison reproducible, and
nothing is promoted to production by merely running an experiment.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExperimentEmbedding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A vector from a candidate model, for offline comparison only.

    `vector` is intentionally dimension-agnostic so successive candidates can
    be tried without a migration each time. Every read path must filter by
    `model_name`; mixing widths in one distance comparison is an error.
    """

    __tablename__ = "experiment_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "owner_type", "owner_id", "model_name", name="uq_experiment_embedding_owner_model"
        ),
    )

    owner_type: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    model_name: Mapped[str] = mapped_column(String(128), index=True)
    model_revision: Mapped[str | None] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(Vector())
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    normalized: Mapped[bool] = mapped_column(Boolean, default=True)
    prep_version: Mapped[int] = mapped_column(Integer, default=1)
