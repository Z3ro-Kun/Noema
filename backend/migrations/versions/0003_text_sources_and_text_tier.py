"""Text provenance: text_sources table, plus text tier/source on content units.

Existing content units are literature passages -- the work's own words -- so
they backfill to 'primary' via the column default. Their provenance is still
carried by their Work, hence the nullable text_source_id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-17
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE text_sources (
            source_name VARCHAR(64) NOT NULL,
            source_ref VARCHAR(512) NOT NULL,
            source_url TEXT,
            revision_ref VARCHAR(128),
            content_hash VARCHAR(64) NOT NULL,
            retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
            licence VARCHAR(64) NOT NULL,
            licence_url TEXT,
            attribution_text TEXT,
            requires_attribution BOOLEAN NOT NULL,
            share_alike BOOLEAN NOT NULL,
            permits_storage BOOLEAN NOT NULL,
            rights_basis TEXT,
            extra_metadata JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_text_source_identity
                UNIQUE (source_name, source_ref, revision_ref, content_hash)
        )
    """)
    op.execute("CREATE INDEX ix_text_sources_source_name ON text_sources (source_name)")
    op.execute("CREATE INDEX ix_text_sources_source_ref ON text_sources (source_ref)")
    op.execute("CREATE INDEX ix_text_sources_content_hash ON text_sources (content_hash)")
    op.execute("CREATE INDEX ix_text_sources_permits_storage ON text_sources (permits_storage)")

    op.execute(
        "ALTER TABLE content_units "
        "ADD COLUMN text_tier VARCHAR(16) NOT NULL DEFAULT 'primary'"
    )
    op.execute(
        "ALTER TABLE content_units ADD CONSTRAINT ck_content_unit_text_tier "
        "CHECK (text_tier IN ('primary', 'summary'))"
    )
    op.execute("ALTER TABLE content_units ADD COLUMN text_source_id UUID")
    op.execute(
        "ALTER TABLE content_units ADD CONSTRAINT content_units_text_source_id_fkey "
        "FOREIGN KEY (text_source_id) REFERENCES text_sources (id)"
    )
    op.execute("CREATE INDEX ix_content_units_text_tier ON content_units (text_tier)")
    op.execute("CREATE INDEX ix_content_units_text_source_id ON content_units (text_source_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_content_units_text_source_id")
    op.execute("DROP INDEX IF EXISTS ix_content_units_text_tier")
    op.execute(
        "ALTER TABLE content_units DROP CONSTRAINT IF EXISTS content_units_text_source_id_fkey"
    )
    op.execute("ALTER TABLE content_units DROP COLUMN IF EXISTS text_source_id")
    op.execute("ALTER TABLE content_units DROP CONSTRAINT IF EXISTS ck_content_unit_text_tier")
    op.execute("ALTER TABLE content_units DROP COLUMN IF EXISTS text_tier")
    op.execute("DROP TABLE IF EXISTS text_sources")
