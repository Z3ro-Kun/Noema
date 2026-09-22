from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from tests.test_models import EXPECTED_TABLES

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_creates_expected_schema(database_available: bool) -> None:
    """Runs `alembic upgrade head` and checks the resulting schema.

    Deliberately does *not* downgrade afterwards: this may run against a
    shared local dev database, and `downgrade("base")` would drop every
    table in it. Upgrading to head is safe to repeat (a no-op once already
    there), which is why that's the only mutation this test performs.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))

    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables
    finally:
        engine.dispose()


def test_domains_are_seeded_for_all_three_v1_domains(database_available: bool) -> None:
    """The domain table is the ingestion contract: an unseeded slug is refused.

    Also pins the widened comics domain from 0008. The slug stays `manhwa`
    for continuity with already-ingested works, but it holds manga too, and
    the name has to say so or the catalogue misdescribes half its contents.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        with engine.connect() as connection:
            rows = dict(
                connection.execute(text("SELECT slug, name FROM domains")).all()
            )
    finally:
        engine.dispose()

    assert {"literature", "anime", "manhwa"} <= set(rows)
    assert rows["manhwa"] == "Manga & Manhwa"


def test_user_library_tables_enforce_the_canonical_boundary(database_available: bool) -> None:
    """Migration 0009's constraints, checked on the real schema.

    These are the structural guarantees the phase rests on, so they are
    asserted against the database rather than trusted to the ORM:
    one interaction per (user, work), a rating that is nullable and
    range-checked independently of status, and a controlled status
    vocabulary.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "users",
            "user_sessions",
            "user_content_interactions",
            "user_content_events",
        } <= tables

        constraints = {
            c["name"] for c in inspector.get_unique_constraints("user_content_interactions")
        }
        assert "uq_user_content_interaction" in constraints

        checks = {
            c["name"] for c in inspector.get_check_constraints("user_content_interactions")
        }
        assert {
            "ck_user_content_interaction_status",
            "ck_user_content_interaction_rating",
        } <= checks

        columns = {c["name"]: c for c in inspector.get_columns("user_content_interactions")}
        # Unrated must be representable, and is not the same as a low rating.
        assert columns["rating"]["nullable"] is True
        # Status and rating are separate columns, never one derived field.
        assert "status" in columns and "rating" in columns
        # Every signal the taste layer must tell apart has somewhere to live.
        assert {
            "added_at",
            "started_at",
            "completed_at",
            "abandoned_at",
            "removed_at",
            "times_started",
            "times_completed",
        } <= set(columns)

        # The canonical side is referenced, never copied: the interaction
        # carries a work_id and no content columns of its own.
        assert not {"text_content", "description", "extra_metadata"} & set(columns)
        work_fk = [
            fk
            for fk in inspector.get_foreign_keys("user_content_interactions")
            if fk["referred_table"] == "works"
        ]
        assert len(work_fk) == 1
        # No cascade from user data into the corpus.
        assert (work_fk[0].get("options") or {}).get("ondelete") in (None, "", "NO ACTION")
    finally:
        engine.dispose()


def test_canonical_tables_were_not_altered_by_the_user_migration(
    database_available: bool,
) -> None:
    """0009 adds tables; it must not have added a user column to the corpus.

    A user's rating living on `works` would be the exact failure this phase
    exists to prevent, so it is asserted rather than assumed.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        for table in ("works", "containers", "content_units", "embeddings", "text_sources"):
            columns = {c["name"] for c in inspector.get_columns(table)}
            assert not {
                "user_id",
                "rating",
                "status",
                "added_at",
                "completed_at",
            } & columns, f"{table} gained user-specific columns"
    finally:
        engine.dispose()


def test_every_migration_runs_from_base_into_an_empty_schema(
    database_available: bool,
) -> None:
    """base -> head with nothing pre-existing, isolated from the dev database.

    Upgrading an already-migrated database only proves the *latest* migration
    applies. This proves the whole chain still builds the schema from
    nothing, which is what a new deployment actually does.

    Isolation is a temporary schema rather than a temporary database, because
    the application role cannot CREATE DATABASE. The Alembic version table is
    placed inside that schema too -- otherwise Alembic reads the dev
    database's version, concludes it is already at head, and silently does
    nothing.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    schema = "migration_test_1l"
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    alembic_cfg.attributes["version_table_schema"] = schema

    engine = create_engine(get_settings().sync_database_url)

    def migrate(action: str, target: str) -> None:
        with engine.connect() as connection:
            connection.execute(text(f"SET search_path TO {schema}, public"))
            alembic_cfg.attributes["connection"] = connection
            getattr(command, action)(alembic_cfg, target)
            connection.commit()

    try:
        with engine.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.commit()

        migrate("upgrade", "head")

        tables = set(inspect(engine).get_table_names(schema=schema))
        assert EXPECTED_TABLES <= tables
        assert {"users", "user_content_interactions"} <= tables
        assert {
            "user_preference_feedback",
            "user_preference_feedback_events",
        } <= tables

        with engine.connect() as connection:
            connection.execute(text(f"SET search_path TO {schema}"))
            # Seeded reference data arrives, user and content tables do not.
            domains = {
                row[0] for row in connection.execute(text("SELECT slug FROM domains"))
            }
            assert domains == {"literature", "anime", "manhwa"}
            for table in ("works", "content_units", "embeddings", "users"):
                count = connection.execute(
                    text(f"SELECT count(*) FROM {table}")
                ).scalar_one()
                assert count == 0, f"{table} should be empty in a fresh schema"

        # 0011 is reversible on its own, and reverses cleanly.
        migrate("downgrade", "0010")
        after_0011 = set(inspect(engine).get_table_names(schema=schema))
        assert not {
            "user_preference_feedback",
            "user_preference_feedback_events",
        } & after_0011
        # Nothing it added was load-bearing for the library.
        assert {"users", "user_content_interactions", "work_concepts"} <= after_0011
        migrate("upgrade", "head")

        # 0009 is reversible, and reversing it leaves the corpus tables alone.
        migrate("downgrade", "0008")
        after_downgrade = set(inspect(engine).get_table_names(schema=schema))
        assert not {
            "users",
            "user_sessions",
            "user_content_interactions",
            "user_content_events",
        } & after_downgrade
        assert {"works", "content_units", "embeddings", "text_sources"} <= after_downgrade

        migrate("upgrade", "head")
        assert {"users", "user_content_interactions"} <= set(
            inspect(engine).get_table_names(schema=schema)
        )
    finally:
        with engine.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.commit()
        engine.dispose()


def test_work_concepts_table_enforces_its_provenance_contract(
    database_available: bool,
) -> None:
    """Migration 0010's constraints, checked against the real schema.

    These are the guarantees the concept layer rests on, so they are asserted
    against the database rather than trusted to the ORM: one association per
    (work, concept), attribution that cannot be null, and a confidence that
    is either a real 0-1 value or absent.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        assert "work_concepts" in set(inspector.get_table_names())

        uniques = {c["name"] for c in inspector.get_unique_constraints("work_concepts")}
        assert "uq_work_concept" in uniques

        checks = {c["name"] for c in inspector.get_check_constraints("work_concepts")}
        assert {"ck_work_concept_source", "ck_work_concept_confidence"} <= checks

        columns = {c["name"]: c for c in inspector.get_columns("work_concepts")}
        # Attribution is mandatory: an unattributed association would be a
        # bare claim about a work.
        assert columns["source"]["nullable"] is False
        assert columns["method"]["nullable"] is False
        # Confidence is optional, because most sources state no relevance.
        assert columns["confidence"]["nullable"] is True
        # Nothing user-specific belongs here.
        assert not {"user_id", "rating", "status"} & set(columns)

        # A concept has a stable slug independent of its display name.
        concept_columns = {c["name"] for c in inspector.get_columns("concepts")}
        assert "slug" in concept_columns
        concept_uniques = {
            tuple(c["column_names"]) for c in inspector.get_unique_constraints("concepts")
        }
        concept_indexes = {
            tuple(i["column_names"]) for i in inspector.get_indexes("concepts") if i["unique"]
        }
        assert ("slug",) in concept_uniques | concept_indexes
    finally:
        engine.dispose()


def test_preference_feedback_tables_enforce_their_contract(
    database_available: bool,
) -> None:
    """Migration 0011's constraints, checked against the real schema.

    The guarantees the feedback layer rests on: one current verdict per
    (user, target kind, concept), a closed set of verdicts, a target that
    cannot be absent for the kind that exists today, and an append-only
    history keyed to the verdict it produced.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"user_preference_feedback", "user_preference_feedback_events"} <= tables

        uniques = {
            c["name"] for c in inspector.get_unique_constraints("user_preference_feedback")
        }
        assert "uq_user_preference_feedback" in uniques

        checks = {
            c["name"] for c in inspector.get_check_constraints("user_preference_feedback")
        }
        assert {
            "ck_user_preference_feedback_type",
            "ck_user_preference_feedback_target",
            "ck_user_preference_feedback_target_present",
        } <= checks

        columns = {c["name"] for c in inspector.get_columns("user_preference_feedback")}
        # Explicit feedback must not be storable as a rating, even by mistake.
        assert not {"rating", "score", "preference_evidence"} & columns

        events = {c["name"] for c in inspector.get_columns("user_preference_feedback_events")}
        assert {"feedback_before", "feedback_after", "occurred_at"} <= events
    finally:
        engine.dispose()


def test_the_feedback_migration_left_the_library_tables_alone(
    database_available: bool,
) -> None:
    """0011 adds tables; it does not reach into the ones that hold ratings."""
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        interactions = {
            c["name"] for c in inspector.get_columns("user_content_interactions")
        }
        assert not {"feedback", "feedback_type", "confirmed"} & interactions
        assert "rating" in interactions
        events = {c["name"] for c in inspector.get_columns("user_content_events")}
        assert not {"feedback_before", "feedback_after"} & events
    finally:
        engine.dispose()


def test_content_concepts_was_left_untouched_by_the_work_concept_migration(
    database_available: bool,
) -> None:
    """0010 adds a work-level table; it does not repurpose the unit-level one.

    `content_concepts` answers a different question and a later phase may
    populate it. Silently changing it would destroy that option.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    engine = create_engine(get_settings().sync_database_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("content_concepts")}
        assert columns == {
            "id",
            "content_unit_id",
            "concept_id",
            "method",
            "confidence",
            "created_at",
        }
    finally:
        engine.dispose()


def test_a_content_unit_has_exactly_one_parent(database_available: bool) -> None:
    """Migration 0012's rule, checked on the real schema.

    A unit hangs off a container or off a work. Both would make "which work
    is this?" have two answers; neither would make it have none. The database
    enforces it rather than the application, because every retrieval query
    resolves that question by joining.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(get_settings().sync_database_url)
    try:
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("content_units")}

        # Both nullable, so either can be the parent...
        assert columns["container_id"]["nullable"] is True
        assert columns["work_id"]["nullable"] is True
        # ...and the constraint is what makes it exactly one.
        checks = {c["name"] for c in inspector.get_check_constraints("content_units")}
        assert "ck_content_unit_single_parent" in checks

        referred = {
            fk["referred_table"]: fk["constrained_columns"]
            for fk in inspector.get_foreign_keys("content_units")
        }
        assert referred["containers"] == ["container_id"]
        assert referred["works"] == ["work_id"]

        with engine.connect() as connection:
            for container_id, work_id in (
                ("(SELECT id FROM containers LIMIT 1)", "(SELECT id FROM works LIMIT 1)"),
                ("NULL", "NULL"),
            ):
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO content_units "
                                "(id, container_id, work_id, unit_type, sequence_number, "
                                " text_tier) VALUES "
                                f"(gen_random_uuid(), {container_id}, {work_id}, "
                                "'synopsis', 1, 'summary')"
                            )
                        )
            connection.rollback()
    finally:
        engine.dispose()


def test_a_clean_database_reaches_the_release_revision(database_available: bool) -> None:
    """The release gate for a new deployment: empty schema -> 0013, twice.

    The test above proves the chain builds a schema from nothing. This one
    asks the questions a first production deploy asks instead: does it land on
    the revision the release is cut at, is running it again a no-op rather
    than an error, is pgvector actually present, and did the structural
    guarantees the application relies on -- indexes, foreign keys, check and
    unique constraints -- come with it.

    Same isolation as above: a temporary schema, because the application role
    cannot CREATE DATABASE. The development database is never touched.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance (docker compose up db)")

    schema = "migration_release_check"
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    alembic_cfg.attributes["version_table_schema"] = schema

    engine = create_engine(get_settings().sync_database_url)

    def migrate() -> None:
        with engine.connect() as connection:
            connection.execute(text(f"SET search_path TO {schema}, public"))
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")
            connection.commit()

    def scalar(statement: str):
        with engine.connect() as connection:
            connection.execute(text(f"SET search_path TO {schema}, public"))
            return connection.execute(text(statement)).scalar_one()

    try:
        with engine.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.commit()

        migrate()

        # --- the revision this release is cut at ----------------------
        assert scalar("SELECT version_num FROM alembic_version") == "0013"

        # --- idempotent: a redeploy re-runs this command --------------
        migrate()
        assert scalar("SELECT version_num FROM alembic_version") == "0013"

        inspector = inspect(engine)
        tables = set(inspector.get_table_names(schema=schema))

        # --- every table the application opens ------------------------
        assert EXPECTED_TABLES <= tables
        assert "user_recommendation_feedback" in tables

        # --- pgvector, and a column that actually uses it -------------
        assert scalar(
            "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
        ) == 1
        vector_columns = scalar(
            "SELECT count(*) FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND udt_name = 'vector'"
        )
        assert vector_columns >= 1, "no column uses the vector type"

        # --- foreign keys, on the tables whose integrity depends on them
        for table, referred in (
            ("user_content_interactions", {"users", "works"}),
            ("user_recommendation_feedback", {"users", "works"}),
            ("user_preference_feedback", {"users", "concepts"}),
            ("work_concepts", {"works", "concepts"}),
            ("content_units", {"containers", "works", "text_sources"}),
        ):
            actual = {
                fk["referred_table"]
                for fk in inspector.get_foreign_keys(table, schema=schema)
            }
            assert referred <= actual, f"{table} lost a foreign key: {referred - actual}"

        # --- the uniqueness the product's semantics rest on -----------
        for table, constraint in (
            ("user_content_interactions", "uq_user_content_interaction"),
            ("user_recommendation_feedback", "uq_user_recommendation_feedback"),
            ("user_preference_feedback", "uq_user_preference_feedback"),
            ("work_concepts", "uq_work_concept"),
        ):
            names = {
                item["name"]
                for item in inspector.get_unique_constraints(table, schema=schema)
            }
            assert constraint in names, f"{table} lost {constraint}"

        # --- the checks that keep a vocabulary closed -----------------
        for table, constraint in (
            ("content_units", "ck_content_unit_text_tier"),
            ("content_units", "ck_content_unit_single_parent"),
            ("user_recommendation_feedback", "ck_user_recommendation_feedback_action"),
            ("user_content_interactions", "ck_user_content_interaction_status"),
            ("user_content_interactions", "ck_user_content_interaction_rating"),
        ):
            names = {
                item["name"]
                for item in inspector.get_check_constraints(table, schema=schema)
            }
            assert constraint in names, f"{table} lost {constraint}"

        # --- the indexes every hot query depends on -------------------
        for table, column in (
            ("user_content_interactions", "user_id"),
            ("user_recommendation_feedback", "user_id"),
            ("work_concepts", "work_id"),
            ("work_concepts", "concept_id"),
            ("content_units", "container_id"),
            ("content_units", "work_id"),
            ("embeddings", "owner_id"),
        ):
            indexed = {
                tuple(index["column_names"])
                for index in inspector.get_indexes(table, schema=schema)
            }
            covered = any(columns and columns[0] == column for columns in indexed)
            assert covered, f"{table}.{column} is not indexed"

        # --- a fresh deployment starts with no user data --------------
        for table in ("users", "user_sessions", "user_content_interactions",
                      "user_recommendation_feedback", "user_preference_feedback"):
            assert scalar(f"SELECT count(*) FROM {table}") == 0

        # --- and with the reference data the ingestion contract needs -
        assert scalar("SELECT count(*) FROM domains") == 3
    finally:
        with engine.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.commit()
        engine.dispose()
