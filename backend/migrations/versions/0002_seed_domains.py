"""Seed the three first-class domains.

Literature, Anime, and Manhwa all exist from the start -- Literature is
merely the first domain with an ingestion adapter, not a privileged one.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DOMAINS = (
    ("literature", "Literature", "Novels, short stories, and other prose works."),
    ("anime", "Anime", "Animated series and films."),
    ("manhwa", "Manhwa", "Korean webtoons and comics."),
)


def upgrade() -> None:
    for slug, name, description in DOMAINS:
        op.execute(
            f"""
            INSERT INTO domains (id, slug, name, description, created_at)
            VALUES (gen_random_uuid(), '{slug}', '{name}', '{description}', now())
            ON CONFLICT (slug) DO NOTHING
            """
        )


def downgrade() -> None:
    slugs = ", ".join(f"'{slug}'" for slug, _, _ in DOMAINS)
    op.execute(f"DELETE FROM domains WHERE slug IN ({slugs})")
