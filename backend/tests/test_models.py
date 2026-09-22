from app.models import Base

EXPECTED_TABLES = {
    "domains",
    "creators",
    "works",
    "work_creators",
    "containers",
    "content_units",
    "entities",
    "concepts",
    "embeddings",
    "content_entities",
    "content_concepts",
    "relationships",
    "evidence",
    "users",
    "user_sessions",
    "user_content_interactions",
    "user_content_events",
    "work_concepts",
    "user_preference_feedback",
    "user_preference_feedback_events",
    "user_recommendation_feedback",
}


def test_metadata_declares_all_core_tables() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables.keys())


def test_explicit_feedback_is_stored_apart_from_ratings() -> None:
    """Phase 1X's central structural promise, asserted on the metadata.

    Explicit feedback is the reader's verdict on an inference; a rating is
    their evaluation of a work. They are different facts, so they are
    different tables -- and the feedback table has no column that could hold
    a rating even by accident.
    """
    feedback = set(Base.metadata.tables["user_preference_feedback"].columns.keys())
    assert {"user_id", "concept_id", "feedback_type", "target_kind"} <= feedback
    assert not {"rating", "normalized_rating", "preference_evidence", "score"} & feedback

    # And the library tables gained nothing.
    interactions = set(
        Base.metadata.tables["user_content_interactions"].columns.keys()
    )
    assert not {"feedback", "feedback_type", "confirmed", "corrected"} & interactions


def test_feedback_history_is_append_only_in_shape() -> None:
    """A single event says what changed and when, without replaying anything."""
    events = set(
        Base.metadata.tables["user_preference_feedback_events"].columns.keys()
    )
    assert {"feedback_before", "feedback_after", "occurred_at"} <= events
    # No updated_at: an event is never revised.
    assert "updated_at" not in events


def test_relationship_table_separates_computed_from_source_facts() -> None:
    columns = Base.metadata.tables["relationships"].columns
    assert {"source", "method", "score", "confidence"} <= set(columns.keys())


def test_embeddings_are_polymorphic_over_owner_type() -> None:
    columns = Base.metadata.tables["embeddings"].columns
    assert {"owner_type", "owner_id", "model_name", "vector"} <= set(columns.keys())
