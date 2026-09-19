"""Initial domain schema: domains, works, content hierarchy, semantic layer.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE concepts (
            name VARCHAR(255) NOT NULL,
            concept_type VARCHAR(32) NOT NULL,
            description TEXT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("CREATE UNIQUE INDEX ix_concepts_name ON concepts (name)")

    op.execute("""
        CREATE TABLE creators (
            name VARCHAR(255) NOT NULL,
            role VARCHAR(64),
            external_ids JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("CREATE INDEX ix_creators_name ON creators (name)")

    op.execute("""
        CREATE TABLE domains (
            slug VARCHAR(32) NOT NULL,
            name VARCHAR(64) NOT NULL,
            description TEXT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("CREATE UNIQUE INDEX ix_domains_slug ON domains (slug)")

    op.execute("""
        CREATE TABLE embeddings (
            owner_type VARCHAR(32) NOT NULL,
            owner_id UUID NOT NULL,
            model_name VARCHAR(128) NOT NULL,
            vector VECTOR(384) NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_embedding_owner_model UNIQUE (owner_type, owner_id, model_name)
        )
    """)
    op.execute("CREATE INDEX ix_embeddings_owner_type ON embeddings (owner_type)")
    op.execute("CREATE INDEX ix_embeddings_owner_id ON embeddings (owner_id)")

    op.execute("""
        CREATE TABLE relationships (
            subject_type VARCHAR(32) NOT NULL,
            subject_id UUID NOT NULL,
            predicate VARCHAR(128) NOT NULL,
            object_type VARCHAR(32) NOT NULL,
            object_id UUID NOT NULL,
            source VARCHAR(32) NOT NULL,
            method VARCHAR(128),
            score FLOAT,
            confidence FLOAT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("CREATE INDEX ix_relationships_predicate ON relationships (predicate)")
    op.execute("CREATE INDEX ix_relationships_subject_type ON relationships (subject_type)")
    op.execute("CREATE INDEX ix_relationships_object_type ON relationships (object_type)")
    op.execute("CREATE INDEX ix_relationships_subject_id ON relationships (subject_id)")
    op.execute("CREATE INDEX ix_relationships_object_id ON relationships (object_id)")

    op.execute("""
        CREATE TABLE works (
            domain_id UUID NOT NULL,
            title VARCHAR(512) NOT NULL,
            original_title VARCHAR(512),
            description TEXT,
            source VARCHAR(64),
            external_ids JSONB,
            extra_metadata JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(domain_id) REFERENCES domains (id)
        )
    """)
    op.execute("CREATE INDEX ix_works_domain_id ON works (domain_id)")
    op.execute("CREATE INDEX ix_works_title ON works (title)")

    op.execute("""
        CREATE TABLE containers (
            work_id UUID NOT NULL,
            container_type VARCHAR(32) NOT NULL,
            sequence_number INTEGER NOT NULL,
            title VARCHAR(512),
            extra_metadata JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(work_id) REFERENCES works (id)
        )
    """)
    op.execute("CREATE INDEX ix_containers_work_id ON containers (work_id)")

    op.execute("""
        CREATE TABLE entities (
            work_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            description TEXT,
            extra_metadata JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(work_id) REFERENCES works (id)
        )
    """)
    op.execute("CREATE INDEX ix_entities_name ON entities (name)")
    op.execute("CREATE INDEX ix_entities_work_id ON entities (work_id)")

    op.execute("""
        CREATE TABLE work_creators (
            work_id UUID NOT NULL,
            creator_id UUID NOT NULL,
            role VARCHAR(64) NOT NULL,
            PRIMARY KEY (work_id, creator_id, role),
            FOREIGN KEY(work_id) REFERENCES works (id),
            FOREIGN KEY(creator_id) REFERENCES creators (id)
        )
    """)

    op.execute("""
        CREATE TABLE content_units (
            container_id UUID NOT NULL,
            unit_type VARCHAR(32) NOT NULL,
            sequence_number INTEGER NOT NULL,
            text_content TEXT,
            extra_metadata JSONB,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(container_id) REFERENCES containers (id)
        )
    """)
    op.execute("CREATE INDEX ix_content_units_container_id ON content_units (container_id)")

    op.execute("""
        CREATE TABLE content_concepts (
            content_unit_id UUID NOT NULL,
            concept_id UUID NOT NULL,
            method VARCHAR(64),
            confidence FLOAT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(content_unit_id) REFERENCES content_units (id),
            FOREIGN KEY(concept_id) REFERENCES concepts (id)
        )
    """)
    op.execute("CREATE INDEX ix_content_concepts_concept_id ON content_concepts (concept_id)")
    op.execute(
        "CREATE INDEX ix_content_concepts_content_unit_id ON content_concepts (content_unit_id)"
    )

    op.execute("""
        CREATE TABLE content_entities (
            content_unit_id UUID NOT NULL,
            entity_id UUID NOT NULL,
            method VARCHAR(64),
            confidence FLOAT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(content_unit_id) REFERENCES content_units (id),
            FOREIGN KEY(entity_id) REFERENCES entities (id)
        )
    """)
    op.execute(
        "CREATE INDEX ix_content_entities_content_unit_id ON content_entities (content_unit_id)"
    )
    op.execute("CREATE INDEX ix_content_entities_entity_id ON content_entities (entity_id)")

    op.execute("""
        CREATE TABLE evidence (
            relationship_id UUID NOT NULL,
            content_unit_id UUID,
            excerpt TEXT,
            notes TEXT,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(relationship_id) REFERENCES relationships (id),
            FOREIGN KEY(content_unit_id) REFERENCES content_units (id)
        )
    """)
    op.execute("CREATE INDEX ix_evidence_relationship_id ON evidence (relationship_id)")
    op.execute("CREATE INDEX ix_evidence_content_unit_id ON evidence (content_unit_id)")


def downgrade() -> None:
    for table in (
        "evidence",
        "content_entities",
        "content_concepts",
        "content_units",
        "work_creators",
        "entities",
        "containers",
        "works",
        "relationships",
        "embeddings",
        "domains",
        "creators",
        "concepts",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
