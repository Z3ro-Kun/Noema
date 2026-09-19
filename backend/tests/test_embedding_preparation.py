"""Text preparation tests. Pure functions, no model, no database."""

import pytest

from app.services.embedding.preparation import (
    PREP_VERSION,
    EmptyTextError,
    prepare_text,
    text_hash,
)


def test_collapses_whitespace_runs() -> None:
    assert prepare_text("The   crew    left.") == "The crew left."


def test_normalizes_line_endings_and_trims_lines() -> None:
    assert prepare_text("  one  \r\n  two  ") == "one\ntwo"


def test_collapses_excess_blank_lines_but_keeps_paragraphs() -> None:
    assert prepare_text("one\n\n\n\n\ntwo") == "one\n\ntwo"


def test_strips_invisible_characters() -> None:
    assert prepare_text("in​visible﻿") == "invisible"


def test_preserves_meaningful_punctuation_and_case() -> None:
    original = 'She asked, "Who goes there?" -- and no one answered.'

    assert prepare_text(original) == original


def test_does_not_rewrite_or_shorten_prose() -> None:
    passage = "Alice was beginning to get very tired of sitting by her sister on the bank."

    assert prepare_text(passage) == passage


def test_unicode_is_normalized_so_identical_prose_hashes_alike() -> None:
    composed = "café"
    decomposed = "café"

    assert prepare_text(composed) == prepare_text(decomposed)
    assert text_hash(prepare_text(composed)) == text_hash(prepare_text(decomposed))


@pytest.mark.parametrize("empty", ["", "   ", "\n\n\t", "​", None])
def test_empty_text_is_rejected(empty) -> None:
    with pytest.raises(EmptyTextError):
        prepare_text(empty)


def test_preparation_is_deterministic() -> None:
    text = "  Spike   and Jet\r\n\r\n\r\n head out.  "

    assert prepare_text(text) == prepare_text(text)
    assert text_hash(prepare_text(text)) == text_hash(prepare_text(text))


def test_different_text_hashes_differently() -> None:
    assert text_hash(prepare_text("one")) != text_hash(prepare_text("two"))


def test_hash_is_sha256_hex() -> None:
    digest = text_hash(prepare_text("anything"))

    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_prep_version_is_recorded_and_stable() -> None:
    """Bumping this is what marks existing vectors stale."""
    assert isinstance(PREP_VERSION, int)
    assert PREP_VERSION >= 1
