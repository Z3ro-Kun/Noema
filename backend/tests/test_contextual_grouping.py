"""Grouping tests. Pure functions with an injected token counter -- no model."""

import pytest

from app.services.embedding.grouping import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_OVERLAP,
    DEFAULT_WINDOW,
    GroupingConfig,
    SourceUnit,
    build_passages,
    passage_hash,
)

# One "token" per whitespace word, so group sizes in tests are obvious.
def words(text: str) -> int:
    return len(text.split())


def unit(index: int, text: str | None = None, tier: str = "primary") -> SourceUnit:
    return SourceUnit(
        id=f"u{index}",
        sequence_number=index,
        text=text if text is not None else f"unit {index} text",
        text_tier=tier,
    )


def letters(count: int) -> list[SourceUnit]:
    return [unit(i) for i in range(1, count + 1)]


# --- documented grouping behaviour --------------------------------------


def test_five_units_produce_the_documented_overlapping_groups() -> None:
    """A B C D E -> [A B C], [C D E] with window=3, overlap=1."""
    passages = build_passages(letters(5), words)

    assert [p.source_unit_ids for p in passages] == [
        ["u1", "u2", "u3"],
        ["u3", "u4", "u5"],
    ]


def test_seven_units_continue_the_pattern() -> None:
    passages = build_passages(letters(7), words)

    assert [p.source_unit_ids for p in passages] == [
        ["u1", "u2", "u3"],
        ["u3", "u4", "u5"],
        ["u5", "u6", "u7"],
    ]


def test_passages_are_numbered_in_order() -> None:
    passages = build_passages(letters(7), words)

    assert [p.sequence_number for p in passages] == [1, 2, 3]


def test_first_and_last_source_positions_are_recorded() -> None:
    passages = build_passages(letters(5), words)

    assert (passages[0].first_unit_sequence, passages[0].last_unit_sequence) == (1, 3)
    assert (passages[1].first_unit_sequence, passages[1].last_unit_sequence) == (3, 5)


def test_a_partial_final_group_is_still_emitted() -> None:
    passages = build_passages(letters(4), words)

    assert [p.source_unit_ids for p in passages] == [["u1", "u2", "u3"], ["u3", "u4"]]
    assert passages[-1].unit_count == 2


def test_single_unit_container_produces_one_passage() -> None:
    passages = build_passages(letters(1), words)

    assert len(passages) == 1
    assert passages[0].source_unit_ids == ["u1"]


def test_no_units_produces_no_passages() -> None:
    assert build_passages([], words) == []


def test_text_joins_units_with_a_blank_line() -> None:
    passages = build_passages(letters(3), words)

    assert passages[0].text_content == "unit 1 text\n\nunit 2 text\n\nunit 3 text"


def test_defaults_are_the_documented_strategy() -> None:
    config = GroupingConfig()

    assert (DEFAULT_WINDOW, DEFAULT_OVERLAP, DEFAULT_MAX_TOKENS) == (3, 1, 240)
    assert config.key == "window=3;overlap=1;max_tokens=240"


# --- length bound --------------------------------------------------------


def test_group_stops_early_when_the_token_bound_would_be_exceeded() -> None:
    config = GroupingConfig(window=3, overlap=1, max_tokens=10)
    units = [unit(1, "a b c d e f"), unit(2, "g h i j k l"), unit(3, "m n o")]

    passages = build_passages(units, words, config)

    # 6 + 6 would exceed 10, so the first passage holds only unit 1.
    assert passages[0].source_unit_ids == ["u1"]


def test_a_unit_larger_than_the_bound_becomes_its_own_passage() -> None:
    """Source units are never split; an oversized one is kept whole."""
    config = GroupingConfig(window=3, overlap=1, max_tokens=5)
    units = [unit(1, " ".join(["x"] * 50)), unit(2, "a b"), unit(3, "c d")]

    passages = build_passages(units, words, config)

    assert passages[0].source_unit_ids == ["u1"]
    assert passages[0].unit_count == 1


def test_bounded_grouping_still_covers_every_unit() -> None:
    config = GroupingConfig(window=3, overlap=1, max_tokens=8)
    units = [unit(i, "a b c d") for i in range(1, 8)]

    passages = build_passages(units, words, config)

    covered = {unit_id for passage in passages for unit_id in passage.source_unit_ids}
    assert covered == {f"u{i}" for i in range(1, 8)}


def test_grouping_terminates_when_every_unit_exceeds_the_bound() -> None:
    config = GroupingConfig(window=3, overlap=1, max_tokens=1)
    units = [unit(i, "a b c d e") for i in range(1, 6)]

    passages = build_passages(units, words, config)

    assert len(passages) == 5
    assert all(p.unit_count == 1 for p in passages)


# --- invalid text --------------------------------------------------------


def test_empty_units_are_skipped_not_embedded_as_whitespace() -> None:
    units = [unit(1), unit(2, "   "), unit(3), unit(4, ""), unit(5)]

    passages = build_passages(units, words)

    covered = [unit_id for passage in passages for unit_id in passage.source_unit_ids]
    assert "u2" not in covered
    assert "u4" not in covered
    assert "u1" in covered and "u3" in covered and "u5" in covered


def test_all_empty_units_produce_no_passages() -> None:
    assert build_passages([unit(1, ""), unit(2, "  ")], words) == []


# --- configuration validation -------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window": 0},
        {"overlap": -1},
        {"window": 2, "overlap": 2},
        {"window": 2, "overlap": 3},
        {"max_tokens": 0},
    ],
)
def test_invalid_configurations_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        GroupingConfig(**kwargs)


def test_zero_overlap_produces_disjoint_groups() -> None:
    config = GroupingConfig(window=3, overlap=0)

    passages = build_passages(letters(6), words, config)

    assert [p.source_unit_ids for p in passages] == [
        ["u1", "u2", "u3"],
        ["u4", "u5", "u6"],
    ]


# --- reproducibility -----------------------------------------------------


def test_same_input_and_config_produce_the_same_hash() -> None:
    first = build_passages(letters(5), words)
    second = build_passages(letters(5), words)

    assert [p.source_hash for p in first] == [p.source_hash for p in second]


def test_changed_text_changes_the_hash() -> None:
    before = build_passages(letters(3), words)
    after = build_passages([unit(1), unit(2, "different text now"), unit(3)], words)

    assert before[0].source_hash != after[0].source_hash


def test_changed_order_changes_the_hash() -> None:
    forward = build_passages([unit(1), unit(2), unit(3)], words)
    reversed_order = build_passages([unit(3), unit(2), unit(1)], words)

    assert forward[0].source_hash != reversed_order[0].source_hash


def test_changed_grouping_config_changes_the_hash() -> None:
    default = build_passages(letters(5), words)
    wider = build_passages(letters(5), words, GroupingConfig(window=3, overlap=1, max_tokens=99))

    assert default[0].source_hash != wider[0].source_hash


def test_hash_covers_config_and_texts() -> None:
    config = GroupingConfig()

    assert passage_hash(config, ["a", "b"]) == passage_hash(config, ["a", "b"])
    assert passage_hash(config, ["a", "b"]) != passage_hash(config, ["b", "a"])
    assert len(passage_hash(config, ["a"])) == 64


def test_tier_is_inherited_from_the_source_units() -> None:
    units = [unit(i, tier="summary") for i in range(1, 4)]

    passages = build_passages(units, words)

    assert passages[0].text_tier == "summary"
