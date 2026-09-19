"""Rating normalization. Pure functions; no database, no network.

The properties under test are the ones the design turns on, not the exact
numbers the default parameters happen to produce. Where a test does pin a
number it also passes its own parameters, to show the behaviour is a
property of the method rather than of a tuned constant.
"""

import pytest

from app.services.preference.normalization import (
    absolute_component,
    build_rating_context,
    normalize_rating,
    relative_component,
    saturate,
)
from app.services.preference.parameters import PreferenceParameters

GENEROUS = [8, 9, 9, 9, 10]  # evaluation case A's distribution
HARSH = [3, 4, 4, 5, 5]  # case B's
COMPRESSED = [6, 7, 7, 6, 2, 3, 3]  # case H's


# --- the absolute reading --------------------------------------------------


def test_the_scale_midpoint_is_neutral() -> None:
    assert absolute_component(1) == -1.0
    assert absolute_component(10) == 1.0
    assert absolute_component(5) < 0 < absolute_component(6)


# --- shrinkage -------------------------------------------------------------


def test_with_no_ratings_the_prior_is_used_outright() -> None:
    context = build_rating_context([])

    assert context.rating_count == 0
    assert context.baseline == 5.5
    assert context.normalization_confidence == 0.0
    assert context.relative_weight == 0.0
    # Purely the absolute reading, which is the honest answer, not a failure.
    assert normalize_rating(9, context) == pytest.approx(absolute_component(9))


def test_a_personal_baseline_is_shrunk_toward_the_midpoint() -> None:
    """A generous rater's baseline must not become their own mean.

    Without shrinkage this user's baseline would be 9.0, and their 8/10 would
    read as negative evidence -- the classic failure of naive mean-centring.
    """
    context = build_rating_context(GENEROUS)

    assert context.observed_mean == 9.0
    assert 5.5 < context.baseline < 9.0
    assert normalize_rating(8, context) > 0


def test_shrinkage_weakens_as_evidence_accumulates() -> None:
    few = build_rating_context([9, 9])
    many = build_rating_context([9] * 40)

    assert abs(few.baseline - 5.5) < abs(many.baseline - 5.5)
    assert few.normalization_confidence < many.normalization_confidence
    assert few.relative_weight < many.relative_weight


def test_an_identical_rating_history_cannot_produce_an_infinite_signal() -> None:
    """Zero observed spread must not become a division by zero."""
    context = build_rating_context([7, 7, 7, 7, 7])

    assert context.observed_spread == 0.0
    assert context.spread >= 0.5
    assert -1.0 <= normalize_rating(8, context) <= 1.0
    assert -1.0 <= normalize_rating(1, context) <= 1.0


# --- the blend -------------------------------------------------------------


def test_the_relative_term_never_outweighs_the_absolute_one() -> None:
    """A 10/10 can never be negative evidence, nor a 1/10 positive.

    This is what the weight cap buys: relative reading adjusts the absolute
    one, it does not overrule it.
    """
    for history in ([10] * 50, [1] * 50, GENEROUS, HARSH, COMPRESSED):
        context = build_rating_context(history)
        assert normalize_rating(10, context) > 0, history[:3]
        assert normalize_rating(1, context) < 0, history[:3]


def test_normalized_ratings_stay_within_range() -> None:
    for history in ([], [1], [10], GENEROUS, HARSH, COMPRESSED, [5] * 30):
        context = build_rating_context(history)
        for rating in range(1, 11):
            assert -1.0 <= normalize_rating(rating, context) <= 1.0


def test_normalization_is_monotonic_in_the_rating() -> None:
    """A higher rating is never weaker evidence than a lower one."""
    for history in ([], GENEROUS, HARSH, COMPRESSED):
        context = build_rating_context(history)
        values = [normalize_rating(rating, context) for rating in range(1, 11)]
        assert values == sorted(values), history[:3]


# --- the point of the whole exercise ---------------------------------------


def test_the_same_raw_rating_means_different_things_to_different_users() -> None:
    """A 7 from a harsh rater is their best; from a generous one it is not."""
    harsh = build_rating_context(COMPRESSED)
    generous = build_rating_context(GENEROUS)

    assert normalize_rating(7, harsh) > normalize_rating(7, generous)


def test_a_harsh_raters_top_ratings_read_as_positive(
) -> None:
    """Case H's favourites are 6 and 7, and must not read as lukewarm."""
    context = build_rating_context(COMPRESSED)

    assert normalize_rating(7, context) > 0
    assert normalize_rating(6, context) > 0
    assert normalize_rating(3, context) < 0
    assert normalize_rating(2, context) < 0


def test_a_generous_raters_low_ratings_do_not_read_as_enthusiasm() -> None:
    context = build_rating_context(GENEROUS)

    assert normalize_rating(8, context) < normalize_rating(10, context)


def test_a_harsh_history_keeps_middling_ratings_out_of_positive_territory() -> None:
    """Case B rates 3-5; the aggregate must not come out positive."""
    context = build_rating_context(HARSH)
    values = [normalize_rating(rating, context) for rating in HARSH]

    assert sum(values) / len(values) < 0


# --- behaviour is a property of the method, not of the defaults ------------


def test_the_properties_hold_under_different_parameters() -> None:
    parameters = PreferenceParameters(
        baseline_prior_weight=1.0,
        spread_prior=3.0,
        spread_prior_weight=2.0,
        max_relative_weight=0.4,
        relative_weight_prior=2.0,
    )
    context = build_rating_context(GENEROUS, parameters)

    assert normalize_rating(10, context, parameters) > 0
    assert normalize_rating(1, context, parameters) < 0
    assert normalize_rating(8, context, parameters) > 0
    assert -1.0 <= normalize_rating(5, context, parameters) <= 1.0


def test_a_wider_rating_scale_is_supported() -> None:
    parameters = PreferenceParameters(rating_min=0, rating_max=100)

    assert parameters.scale_midpoint() == 50.0
    assert absolute_component(100, parameters) == 1.0
    assert absolute_component(0, parameters) == -1.0


# --- the relative component in isolation -----------------------------------


def test_the_relative_component_is_clamped() -> None:
    """One outlier must not be allowed to dominate a whole concept."""
    context = build_rating_context([5] * 20)

    assert relative_component(10, context) == 1.0
    assert relative_component(1, context) == -1.0


# --- saturation ------------------------------------------------------------


def test_counts_saturate_with_diminishing_returns() -> None:
    assert saturate(0, 3) == 0.0
    assert saturate(3, 3) == pytest.approx(0.5)
    assert saturate(100, 3) < 1.0
    # The first work matters more than the eleventh.
    assert saturate(1, 3) - saturate(0, 3) > saturate(11, 3) - saturate(10, 3)


def test_saturation_is_monotonic_and_bounded() -> None:
    values = [saturate(n, 3) for n in range(0, 50)]

    assert values == sorted(values)
    assert all(0.0 <= value < 1.0 for value in values)
    assert saturate(-5, 3) == 0.0
