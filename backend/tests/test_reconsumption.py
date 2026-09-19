"""Phase 1AA: recording another completed cycle, deliberately.

The count was always there -- `times_completed`, moved by the ordinary
status model. What was wrong was how a reader reached it. The work page's
primary action for a completed work was "Read it again", which moved it
back to in progress; finishing it again counted a second completion. A
reader who took the offer, or who opened the status list to look around,
could end up with a work claiming two reads after one.

So the count becomes something a reader states rather than something they
walk into:

    POST /api/v1/library/{work_id}/completions

What is under test here is that it means exactly one thing and that nothing
else means it:

    exactly once        one call, one completion. Not two, not zero.

    nothing else        reading the entry, reading it repeatedly, reading
                        its history and refetching the page all leave the
                        count alone. Only the POST moves it.

    the rating is safe  a third read is not a new opinion. The rating is
                        neither cleared, changed, nor asked for.

    the history is real the stored trail is the same pair of transitions a
                        reader would have produced by hand, so the log of
                        someone who used the old route and someone who
                        presses the control cannot be told apart.

    one user only       A's count is not B's.

The suite reuses `test_library_experience`'s fixture and helpers rather than
seeding a second corpus: the works, the registration helper and the teardown
are the same, and duplicating them would make two things that have to agree.
"""

import pytest

from app.models import STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_ON_HOLD

from tests.test_library_experience import (  # noqa: F401  (fixture import)
    LIBRARY_URL,
    LibraryApi,
    add,
    api,
    history,
    kinds,
    library,
    patch,
    register,
)


def complete(api: LibraryApi, headers: dict, work_id: str) -> None:
    """Add a work and finish it once, the ordinary way."""
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    assert patch(api, headers, work_id, status=STATUS_COMPLETED).status_code == 200


def again(api: LibraryApi, headers: dict, work_id: str):
    return api.client.post(f"{LIBRARY_URL}/{work_id}/completions", headers=headers)


def undo(api: LibraryApi, headers: dict, work_id: str):
    return api.client.delete(f"{LIBRARY_URL}/{work_id}/completions", headers=headers)


def entry(api: LibraryApi, headers: dict, work_id: str) -> dict:
    response = api.client.get(f"{LIBRARY_URL}/{work_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def state(api: LibraryApi, headers: dict, work_id: str) -> dict:
    return entry(api, headers, work_id)["user_state"]


# --- the count -------------------------------------------------------------


def test_01_a_first_completion_counts_one(api: LibraryApi) -> None:
    """The ordinary status model still owns the first finish."""
    headers = register(api, "first-completion")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    assert state(api, headers, work_id)["times_completed"] == 1


def test_02_each_deliberate_record_adds_exactly_one(api: LibraryApi) -> None:
    """1 -> 2 -> 3, one call at a time."""
    headers = register(api, "counts-up")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    for expected in (2, 3, 4):
        response = again(api, headers, work_id)
        assert response.status_code == 200, response.text
        assert response.json()["user_state"]["times_completed"] == expected
        # And the entry agrees with what the call returned, so a client that
        # re-renders from either sees the same number.
        assert state(api, headers, work_id)["times_completed"] == expected


def test_03_the_work_stays_completed(api: LibraryApi) -> None:
    """A recorded re-read ends where it began: finished, not in progress."""
    headers = register(api, "stays-completed")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    assert again(api, headers, work_id).json()["user_state"]["status"] == STATUS_COMPLETED


def test_04_a_re_read_is_also_a_start(api: LibraryApi) -> None:
    """`times_started` moves with it, exactly as the manual route moved it."""
    headers = register(api, "started-too")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    before = state(api, headers, work_id)["times_started"]

    again(api, headers, work_id)

    assert state(api, headers, work_id)["times_started"] == before + 1


def test_05_only_a_completed_work_can_be_recorded_again(api: LibraryApi) -> None:
    """There is no reading something again before reading it once."""
    headers = register(api, "not-yet-finished")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    assert again(api, headers, work_id).status_code == 409
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    assert again(api, headers, work_id).status_code == 409
    assert state(api, headers, work_id)["times_completed"] == 0

    patch(api, headers, work_id, status=STATUS_ON_HOLD)
    assert again(api, headers, work_id).status_code == 409
    assert state(api, headers, work_id)["times_completed"] == 0


def test_06_refusal_leaves_no_trace(api: LibraryApi) -> None:
    """A 409 is not a half-applied write: no event, no counter movement."""
    headers = register(api, "refusal-clean")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    before = kinds(history(api, headers, work_id))

    assert again(api, headers, work_id).status_code == 409

    assert kinds(history(api, headers, work_id)) == before


# --- nothing else moves it -------------------------------------------------


def test_07_reading_the_entry_never_counts_a_completion(api: LibraryApi) -> None:
    """The bug this phase exists for: the count must not drift on reads.

    A component that re-renders, a page that refetches and a reader who
    opens the work ten times all look like this from the server's side.
    """
    headers = register(api, "reads-are-free")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    for _ in range(10):
        entry(api, headers, work_id)
        library(api, headers)
        history(api, headers, work_id)

    assert state(api, headers, work_id)["times_completed"] == 1


def test_08_returning_to_a_completed_work_counts_nothing(api: LibraryApi) -> None:
    """Re-reading the *page* is not re-reading the *book*."""
    headers = register(api, "returning-reader")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    # Look at something else, come back, look again.
    library(api, headers, status=STATUS_COMPLETED)
    add(api, headers, api.work_ids[1])
    entry(api, headers, api.work_ids[1])
    entry(api, headers, work_id)

    assert state(api, headers, work_id)["times_completed"] == 1


def test_09_re_marking_completed_is_not_a_new_cycle(api: LibraryApi) -> None:
    """Setting the status it already has changes nothing.

    `_apply_status` only counts a transition *into* completed from somewhere
    else, so a client that re-sends the current status -- a retried PATCH, a
    select that fires on focus -- cannot inflate the count.
    """
    headers = register(api, "already-completed")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    for _ in range(3):
        assert patch(api, headers, work_id, status=STATUS_COMPLETED).status_code == 200

    assert state(api, headers, work_id)["times_completed"] == 1


def test_10_rating_a_work_counts_nothing(api: LibraryApi) -> None:
    headers = register(api, "rating-is-not-reading")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    patch(api, headers, work_id, rating=9, rating_set=True)
    patch(api, headers, work_id, rating=7, rating_set=True)

    assert state(api, headers, work_id)["times_completed"] == 1


# --- the rating ------------------------------------------------------------


def test_11_the_rating_survives_every_recorded_re_read(api: LibraryApi) -> None:
    """Read 5 times, rated 8, is one opinion -- not five."""
    headers = register(api, "rating-survives")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    patch(api, headers, work_id, rating=8, rating_set=True)

    for _ in range(4):
        body = again(api, headers, work_id).json()
        assert body["user_state"]["rating"] == 8

    final = state(api, headers, work_id)
    assert final["times_completed"] == 5
    assert final["rating"] == 8


def test_12_an_unrated_work_stays_unrated(api: LibraryApi) -> None:
    """Finishing something three times still says nothing about liking it."""
    headers = register(api, "still-unrated")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    again(api, headers, work_id)
    again(api, headers, work_id)

    final = state(api, headers, work_id)
    assert final["times_completed"] == 3
    assert final["rating"] is None


def test_13_no_second_rating_is_created(api: LibraryApi) -> None:
    """One row, one rating. A cycle does not get its own score."""
    headers = register(api, "one-rating-only")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    patch(api, headers, work_id, rating=6, rating_set=True)

    again(api, headers, work_id)

    rated = [k for k in kinds(history(api, headers, work_id)) if k == "rated"]
    assert rated == ["rated"]


# --- history ---------------------------------------------------------------


def test_14_a_recorded_re_read_reads_as_a_restart_and_a_finish(
    api: LibraryApi,
) -> None:
    headers = register(api, "history-reads-right")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    again(api, headers, work_id)

    assert kinds(history(api, headers, work_id)) == [
        "added",
        "started",
        "completed",
        "restarted",
        "completed",
    ]


def test_15_the_history_matches_the_manual_route_exactly(api: LibraryApi) -> None:
    """The control is the old two transitions, not a parallel mechanism.

    A reader who pressed the button and one who drove the status control by
    hand must leave identical trails, or the product has two models of the
    same event.
    """
    by_hand = register(api, "did-it-by-hand")
    by_button = register(api, "pressed-the-button")
    work_id = api.work_ids[0]

    complete(api, by_hand, work_id)
    patch(api, by_hand, work_id, status=STATUS_IN_PROGRESS)
    patch(api, by_hand, work_id, status=STATUS_COMPLETED)

    complete(api, by_button, work_id)
    again(api, by_button, work_id)

    assert kinds(history(api, by_hand, work_id)) == kinds(
        history(api, by_button, work_id)
    )
    assert (
        state(api, by_hand, work_id)["times_completed"]
        == state(api, by_button, work_id)["times_completed"]
        == 2
    )


def test_16_the_history_exposes_no_internal_vocabulary(api: LibraryApi) -> None:
    headers = register(api, "no-internals")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)

    body = history(api, headers, work_id)
    for item in body["entries"]:
        assert item["kind"] in {
            "added",
            "returned",
            "started",
            "restarted",
            "completed",
            "paused",
            "abandoned",
            "planned",
            "rated",
            "rating_cleared",
            "removed",
        }
        assert "status_before" not in item
        assert "id" not in item


def test_17_every_earlier_cycle_stays_in_the_history(api: LibraryApi) -> None:
    """Recording a fourth read does not overwrite the first three."""
    headers = register(api, "keeps-every-cycle")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    for _ in range(3):
        again(api, headers, work_id)

    entries = kinds(history(api, headers, work_id))
    assert entries.count("completed") == 4
    assert entries.count("restarted") == 3


def test_18_the_history_count_agrees_with_the_row(api: LibraryApi) -> None:
    """The projection reports the stored figure, never a recount of itself."""
    headers = register(api, "counts-agree")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)

    body = history(api, headers, work_id)
    assert body["times_completed"] == state(api, headers, work_id)["times_completed"] == 2


# --- isolation -------------------------------------------------------------


def test_19_one_readers_count_is_not_anothers(api: LibraryApi) -> None:
    alice = register(api, "counter-alice")
    bob = register(api, "counter-bob")
    work_id = api.work_ids[0]

    complete(api, alice, work_id)
    complete(api, bob, work_id)
    for _ in range(3):
        again(api, alice, work_id)

    assert state(api, alice, work_id)["times_completed"] == 4
    assert state(api, bob, work_id)["times_completed"] == 1


def test_20_recording_a_completion_needs_the_work_in_your_own_library(
    api: LibraryApi,
) -> None:
    """Someone else's completed work is a 404, not a 403: a 403 confirms it."""
    alice = register(api, "owner-alice")
    bob = register(api, "stranger-bob")
    work_id = api.work_ids[0]
    complete(api, alice, work_id)

    assert again(api, bob, work_id).status_code == 404
    assert state(api, alice, work_id)["times_completed"] == 1


def test_21_anonymous_callers_cannot_record_a_completion(api: LibraryApi) -> None:
    response = api.client.post(f"{LIBRARY_URL}/{api.work_ids[0]}/completions")
    assert response.status_code == 401


# --- taking one back -------------------------------------------------------


def test_24_a_recorded_completion_can_be_taken_back(api: LibraryApi) -> None:
    """Counting up was one-way; a miscount had no remedy."""
    headers = register(api, "counts-down")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)
    again(api, headers, work_id)
    assert state(api, headers, work_id)["times_completed"] == 3

    for expected in (2, 1):
        response = undo(api, headers, work_id)
        assert response.status_code == 200, response.text
        assert response.json()["user_state"]["times_completed"] == expected
        assert state(api, headers, work_id)["times_completed"] == expected


def test_25_the_count_stops_at_one(api: LibraryApi) -> None:
    """One completion is a reading, not a mistake. Zero is never reachable."""
    headers = register(api, "floor-of-one")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    assert undo(api, headers, work_id).status_code == 409
    assert state(api, headers, work_id)["times_completed"] == 1

    # And repeatedly, from a count that was built up and taken back down.
    again(api, headers, work_id)
    undo(api, headers, work_id)
    for _ in range(3):
        assert undo(api, headers, work_id).status_code == 409
    assert state(api, headers, work_id)["times_completed"] == 1


def test_26_taking_one_back_is_the_inverse_of_recording_one(api: LibraryApi) -> None:
    """Up then down leaves the row exactly as it was, history included."""
    headers = register(api, "round-trip")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    before_state = state(api, headers, work_id)
    before_history = kinds(history(api, headers, work_id))

    again(api, headers, work_id)
    undo(api, headers, work_id)

    after = state(api, headers, work_id)
    assert after["times_completed"] == before_state["times_completed"]
    assert after["times_started"] == before_state["times_started"]
    assert after["status"] == before_state["status"]
    assert kinds(history(api, headers, work_id)) == before_history


def test_27_the_rating_is_untouched_by_a_correction(api: LibraryApi) -> None:
    headers = register(api, "rating-survives-undo")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    patch(api, headers, work_id, rating=8, rating_set=True)
    again(api, headers, work_id)

    body = undo(api, headers, work_id).json()["user_state"]

    assert body["rating"] == 8
    assert body["rated_at"] is not None
    # And no rating event was written by the correction.
    assert [k for k in kinds(history(api, headers, work_id)) if k == "rated"] == ["rated"]


def test_28_the_work_stays_completed_and_stays_in_the_library(api: LibraryApi) -> None:
    headers = register(api, "still-held")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)

    body = undo(api, headers, work_id).json()["user_state"]

    assert body["status"] == STATUS_COMPLETED
    assert body["in_library"] is True


def test_29_the_history_loses_exactly_one_cycle(api: LibraryApi) -> None:
    headers = register(api, "one-cycle-fewer")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)
    again(api, headers, work_id)
    assert kinds(history(api, headers, work_id)).count("completed") == 3

    undo(api, headers, work_id)

    entries = kinds(history(api, headers, work_id))
    assert entries.count("completed") == 2
    assert entries.count("restarted") == 1
    # The row and the fold still agree, which is the whole point.
    assert history(api, headers, work_id)["times_completed"] == 2


def test_30_an_unfinished_work_has_nothing_to_take_back(api: LibraryApi) -> None:
    headers = register(api, "nothing-to-undo")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    assert undo(api, headers, work_id).status_code == 409
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    assert undo(api, headers, work_id).status_code == 409
    assert state(api, headers, work_id)["times_completed"] == 0


def test_31_a_refused_correction_changes_nothing(api: LibraryApi) -> None:
    headers = register(api, "refusal-is-clean")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    before = state(api, headers, work_id)
    before_history = kinds(history(api, headers, work_id))

    assert undo(api, headers, work_id).status_code == 409

    assert state(api, headers, work_id) == before
    assert kinds(history(api, headers, work_id)) == before_history


def test_32_a_correction_reaches_only_your_own_entry(api: LibraryApi) -> None:
    alice = register(api, "undo-alice")
    bob = register(api, "undo-bob")
    work_id = api.work_ids[0]
    complete(api, alice, work_id)
    again(api, alice, work_id)
    complete(api, bob, work_id)
    again(api, bob, work_id)

    undo(api, alice, work_id)

    assert state(api, alice, work_id)["times_completed"] == 1
    assert state(api, bob, work_id)["times_completed"] == 2


def test_33_a_stranger_cannot_correct_someone_elses_count(api: LibraryApi) -> None:
    alice = register(api, "undo-owner")
    bob = register(api, "undo-stranger")
    work_id = api.work_ids[0]
    complete(api, alice, work_id)
    again(api, alice, work_id)

    assert undo(api, bob, work_id).status_code == 404
    assert state(api, alice, work_id)["times_completed"] == 2


def test_34_anonymous_callers_cannot_correct_a_count(api: LibraryApi) -> None:
    response = api.client.delete(f"{LIBRARY_URL}/{api.work_ids[0]}/completions")
    assert response.status_code == 401


def test_35_reading_the_entry_never_takes_a_completion_back(api: LibraryApi) -> None:
    """Symmetrical with the increment: only the explicit call moves it."""
    headers = register(api, "reads-take-nothing")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)

    for _ in range(5):
        entry(api, headers, work_id)
        history(api, headers, work_id)
        library(api, headers)

    assert state(api, headers, work_id)["times_completed"] == 2


def test_36_the_count_survives_being_read_back_after_a_correction(
    api: LibraryApi,
) -> None:
    """What a refresh would show."""
    headers = register(api, "undo-persists")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    again(api, headers, work_id)
    again(api, headers, work_id)
    undo(api, headers, work_id)

    fresh = library(api, headers)["items"][0]["user_state"]
    assert fresh["times_completed"] == 2
    assert fresh["times_started"] == 2


# --- the canonical work ----------------------------------------------------


def test_22_no_canonical_data_moves(api: LibraryApi) -> None:
    """A re-read is a fact about a reader, never about the work."""
    headers = register(api, "canonical-untouched")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)
    before = entry(api, headers, work_id)["work"]

    for _ in range(3):
        again(api, headers, work_id)

    assert entry(api, headers, work_id)["work"] == before


@pytest.mark.parametrize("attempts", [2, 5])
def test_23_the_count_equals_the_number_of_calls(
    api: LibraryApi, attempts: int
) -> None:
    """The plainest statement of the contract, at two sizes."""
    headers = register(api, f"exact-{attempts}")
    work_id = api.work_ids[0]
    complete(api, headers, work_id)

    for _ in range(attempts):
        assert again(api, headers, work_id).status_code == 200

    assert state(api, headers, work_id)["times_completed"] == 1 + attempts
