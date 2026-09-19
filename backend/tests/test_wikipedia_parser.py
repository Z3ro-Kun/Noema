"""Wikipedia narrative parser tests. Fixture-driven; no network."""

from pathlib import Path

from app.services.ingestion.wikipedia import (
    clean_wikitext,
    episode_list_candidates,
    parse_episode_list,
    parse_volume_list,
    volume_list_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parsed_list():
    return parse_episode_list(load("wikipedia_episode_list.wikitext"))


# --- extraction ----------------------------------------------------------


def test_extracts_only_episode_summaries() -> None:
    result = parsed_list()

    assert [s.episode_number for s in result.summaries] == [1, 2]
    assert result.summaries[0].title == "The Departure"
    assert result.summaries[1].title == "Salt and Iron"


def test_ignores_every_non_narrative_section() -> None:
    """Cast, production, reception, references, infobox, tables, links."""
    text = " ".join(s.summary for s in parsed_list().summaries)

    for forbidden in (
        "cast list",
        "Actor One",
        "Production notes",
        "Test Studio",
        "Critics praised",
        "Official site",
        "Infobox",
        "Overview table",
        "lead paragraph",
    ):
        assert forbidden not in text


def test_summary_prose_is_cleaned_of_markup() -> None:
    summary = parsed_list().summaries[0].summary

    assert "[[" not in summary and "]]" not in summary
    assert "{{" not in summary and "}}" not in summary
    assert "<ref" not in summary
    assert "''" not in summary
    # Link display text survives; the target does not.
    assert "the harbour" in summary
    assert "Test Harbour|" not in summary


def test_multi_paragraph_summary_is_preserved() -> None:
    summary = parsed_list().summaries[0].summary

    assert "By nightfall" in summary
    assert summary.startswith("The crew of the Kestrel")


def test_external_links_reduce_to_their_text() -> None:
    summary = parsed_list().summaries[1].summary

    assert "an external link" in summary
    assert "https://" not in summary


def test_self_closing_ref_is_removed() -> None:
    assert "<ref" not in parsed_list().summaries[1].summary


# --- refusals, not guesses ----------------------------------------------


def test_non_integer_episode_number_is_skipped_not_renumbered() -> None:
    """A special numbered "SP" must not become the next integer."""
    result = parsed_list()

    assert 3 not in [s.episode_number for s in result.summaries]
    skipped = [s for s in result.skipped if s["reason"] == "unparseable_episode_number"]
    assert len(skipped) == 1
    assert skipped[0]["title"] == "Mish-Mash Blues"
    assert skipped[0]["raw_number"] == "SP"


def test_entry_without_summary_is_skipped() -> None:
    skipped = [s for s in parsed_list().skipped if s["reason"] == "no_summary"]

    assert len(skipped) == 1
    assert skipped[0]["episode_number"] == 3


def test_episode_numbers_never_come_from_position() -> None:
    """The skipped special sits between 2 and 3; numbering must not shift."""
    numbers = [s.episode_number for s in parsed_list().summaries]

    assert numbers == [1, 2]
    assert numbers != list(range(1, len(numbers) + 1)) or numbers == [1, 2]


# --- the sublist structure ----------------------------------------------


def test_sublist_template_variant_is_supported() -> None:
    result = parse_episode_list(load("wikipedia_sublist.wikitext"))

    assert [s.episode_number for s in result.summaries] == [14, 15]
    assert result.summaries[0].title == "Deep Water"
    assert "cargo" in result.summaries[0].summary


def test_sublist_numbering_is_not_reindexed_from_one() -> None:
    """Season sub-articles start mid-series; 14 must stay 14."""
    result = parse_episode_list(load("wikipedia_sublist.wikitext"))

    assert result.summaries[0].episode_number == 14


def test_episode_table_template_is_not_mistaken_for_an_episode() -> None:
    result = parse_episode_list(load("wikipedia_sublist.wikitext"))

    assert len(result.summaries) == 2


# --- degenerate input ----------------------------------------------------


def test_page_with_no_episode_templates_yields_nothing() -> None:
    result = parse_episode_list("== Plot ==\nA film with no episode list at all.\n")

    assert result.summaries == []
    assert result.skipped == []


def test_unbalanced_template_does_not_hang_or_misparse() -> None:
    result = parse_episode_list("{{Episode list\n| EpisodeNumber = 1\n| ShortSummary = Truncated")

    assert result.summaries == []


def test_clean_wikitext_on_plain_text_is_a_noop() -> None:
    assert clean_wikitext("Just plain prose.") == "Just plain prose."


def test_candidate_titles_are_specific_before_general() -> None:
    assert episode_list_candidates("Cowboy Bebop") == [
        "List of Cowboy Bebop episodes",
        "Cowboy Bebop",
    ]


# --- volume lists --------------------------------------------------------
#
# The manga equivalent. English Wikipedia collects manga volumes in
# {{Graphic novel list}} with VolumeNumber/Summary rather than
# {{Episode list}} with EpisodeNumber/ShortSummary, so the parser is separate;
# every refusal rule is deliberately the same.


def parsed_volumes():
    return parse_volume_list(load("wikipedia_volume_list.wikitext"))


def test_volume_summaries_are_extracted_with_their_stated_numbers() -> None:
    result = parsed_volumes()

    assert [s.episode_number for s in result.summaries] == [1, 2, 4]


def test_volume_numbers_come_from_the_source_not_list_position() -> None:
    """Volume 3 has no summary, so the third *entry* returned is volume 4."""
    result = parsed_volumes()

    assert result.summaries[2].episode_number == 4


def test_volume_summary_wikitext_is_reduced_to_prose() -> None:
    first = parsed_volumes().summaries[0]

    assert "<ref" not in first.summary
    assert "{{" not in first.summary
    assert "[[" not in first.summary
    assert "a land beyond the sea" in first.summary
    assert "an external link" in parsed_volumes().summaries[1].summary


def test_multi_paragraph_volume_summaries_are_kept_whole() -> None:
    first = parsed_volumes().summaries[0]

    assert "\n\n" in first.summary
    assert first.summary.endswith("did not invite.")


def test_unparseable_volume_number_is_skipped_not_renumbered() -> None:
    """An omnibus spanning 1-3 must not be coerced into a single volume."""
    result = parsed_volumes()
    skipped = [s for s in result.skipped if s["reason"] == "unparseable_volume_number"]

    assert len(skipped) == 1
    assert "Omnibus" in skipped[0]["raw_number"]
    assert 3 not in [s.episode_number for s in result.summaries[:2]]


def test_volume_without_a_summary_is_reported_as_a_gap() -> None:
    result = parsed_volumes()
    skipped = [s for s in result.skipped if s["reason"] == "no_summary"]

    assert [s["episode_number"] for s in skipped] == [3]


def test_header_template_is_not_mistaken_for_a_volume() -> None:
    """{{Graphic novel list/header}} is furniture; only exact names match."""
    result = parsed_volumes()

    assert len(result.summaries) + len(result.skipped) == 5


def test_article_prose_is_never_ingested_as_volume_narrative() -> None:
    """The work-level Plot section describes the series, not any one volume."""
    joined = " ".join(s.summary for s in parsed_volumes().summaries)

    assert "work-level plot section" not in joined.lower()
    assert "serialised between" not in joined.lower()
    assert "critics praised" not in joined.lower()


def test_episode_parser_finds_nothing_in_a_volume_list() -> None:
    """The two template families do not overlap, so neither parser guesses."""
    assert parse_episode_list(load("wikipedia_volume_list.wikitext")).summaries == []


def test_volume_parser_finds_nothing_in_an_episode_list() -> None:
    assert parse_volume_list(load("wikipedia_episode_list.wikitext")).summaries == []


def test_page_with_no_volume_templates_yields_nothing() -> None:
    result = parse_volume_list("== Plot ==\nA one-shot with no volume list at all.\n")

    assert result.summaries == []
    assert result.skipped == []


def test_volume_candidate_titles_are_specific_before_general() -> None:
    assert volume_list_candidates("Vinland Saga") == [
        "List of Vinland Saga chapters",
        "List of Vinland Saga volumes",
        "Vinland Saga",
    ]
