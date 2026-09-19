"""Widen the `manhwa` domain to cover manga as well as manhwa.

Phase 0 seeded the domain as "Korean webtoons and comics." Ingestion shows
the source does not divide the category that way: AniList models manga,
manhwa and manhua as one media type and distinguishes them only by
`countryOfOrigin`, and the structural shape (volumes containing chapters) is
identical across all three. Splitting them into separate domains would mean
maintaining a boundary the source does not draw and readers do not use.

So the slug stays `manhwa` -- renaming it would break every ingested work's
domain association for a cosmetic gain -- while the display name and
description are corrected to say what the domain actually holds. The
tradition a work belongs to remains visible per work, in
`extra_metadata.anilist.comic_tradition`.

Data only; no schema change.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SLUG = "manhwa"

NEW_NAME = "Manga & Manhwa"
NEW_DESCRIPTION = (
    "Serialized comics -- Japanese manga, Korean manhwa, and Chinese manhua -- "
    "published in volumes."
)

OLD_NAME = "Manhwa"
OLD_DESCRIPTION = "Korean webtoons and comics."


def _set(name: str, description: str) -> None:
    op.execute(
        f"""
        UPDATE domains
        SET name = '{name.replace(chr(39), chr(39) * 2)}',
            description = '{description.replace(chr(39), chr(39) * 2)}'
        WHERE slug = '{SLUG}'
        """
    )


def upgrade() -> None:
    _set(NEW_NAME, NEW_DESCRIPTION)


def downgrade() -> None:
    _set(OLD_NAME, OLD_DESCRIPTION)
