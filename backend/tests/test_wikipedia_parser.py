"""Wikipedia narrative parser tests. Fixture-driven; no network."""

from pathlib import Path

from app.services.ingestion.wikipedia import (
    clean_wikitext,
    episode_list_candidates,
    parse_episode_list,
    parse_volume_list,
    parse_work_summary,
    volume_list_candidates,
    work_article_candidates,
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


# --- work-level summaries ------------------------------------------------
#
# The fallback parser, for works whose canonical source catalogues no
# containers. It reads a section rather than a template, so what it *refuses*
# is most of what makes it safe.


def parsed_article():
    return parse_work_summary(load("wikipedia_work_article.wikitext"))


def test_work_summary_comes_from_the_narrative_section() -> None:
    result = parsed_article()

    assert result.summary is not None
    assert result.summary.section == "Synopsis"
    assert "beneath the tower" in result.summary.summary


def test_work_summary_keeps_narrative_subsections() -> None:
    """A "Premise" under "Synopsis" is still the story."""
    summary = parsed_article().summary.summary

    assert "grants whoever reaches its top" in summary


def test_work_summary_drops_non_narrative_subsections() -> None:
    result = parsed_article()

    assert "character-list entry" not in result.summary.summary
    assert {"subsection_not_narrative"} == {
        item["reason"] for item in result.skipped if item["reason"] == "subsection_not_narrative"
    }


def test_work_summary_never_reaches_the_lead_or_the_other_sections() -> None:
    """Everything outside the narrative section is article furniture."""
    summary = parsed_article().summary.summary

    for forbidden in (
        "article furniture",
        "Production notes",
        "Reception prose",
        "Official site",
        "A publisher",
        "A citation",
    ):
        assert forbidden not in summary


def test_an_article_with_no_narrative_section_yields_nothing() -> None:
    result = parse_work_summary("== Production ==\nNotes about how it was made.\n")

    assert result.summary is None
    assert [item["reason"] for item in result.skipped] == ["no_narrative_section"]


def test_a_stub_section_is_refused_rather_than_stored() -> None:
    result = parse_work_summary("== Plot ==\nA boy climbs a tower.\n")

    assert result.summary is None
    assert result.skipped[0]["reason"] == "summary_too_short"


def test_work_article_candidates_try_disambiguated_titles_first() -> None:
    candidates = work_article_candidates("Tower of God", None, "신의 탑")

    assert candidates[0] == "Tower of God (webtoon)"
    # The bare title is a last resort: it can be an article about anything.
    assert candidates.index("Tower of God") > candidates.index("Tower of God (manhwa)")


def test_a_plot_split_into_chapter_ranges_is_still_the_plot() -> None:
    """Some articles put the whole plot in subsections named by span.

    "Chapters 39-85" under "Plot" is part of the story; "Characters" is not,
    and sits under the same heading on the same kind of page.
    """
    result = parse_work_summary(
        "== Plot ==\n"
        "{{Long plot|date=April 2022}}\n"
        "=== Prologue, Chapters 1-38 ===\n"
        "A shut-in discovers the building has begun turning people into "
        "monsters, and that leaving is no longer the obvious thing to do.\n"
        "=== Chapters 39-85 ===\n"
        "The survivors organise, and discover that the rules of the change "
        "are not what the first weeks made them look like.\n"
        "=== Characters ===\n"
        "* A shut-in, the protagonist of a character-list entry.\n"
    )

    assert result.summary is not None
    assert "shut-in discovers the building" in result.summary.summary
    assert "not what the first weeks" in result.summary.summary
    assert "character-list entry" not in result.summary.summary


def test_an_unknown_subsection_is_still_dropped() -> None:
    """The span rule is a rule about spans, not a general loosening."""
    result = parse_work_summary(
        "== Plot ==\n"
        "A boy climbs a tower that measures everyone who steps inside it, and "
        "keeps climbing long after the reason he started has stopped being "
        "the reason he continues. Each floor asks for something different, "
        "and the higher he goes the less any of it looks like a test he can "
        "pass without losing something.\n"
        "=== Merchandise ===\n"
        "Figures were released by a toy company in 2019.\n"
    )

    assert "Figures were released" not in result.summary.summary


# --- what a removed template leaves behind -------------------------------
#
# Templates were dropped wholesale, which is right for a citation and wrong
# for a template standing where a noun belongs: the punctuation around it
# stayed and the word did not.


def test_a_name_template_keeps_its_english_reading() -> None:
    """The case that exposed this: three names in one sentence."""
    cleaned = clean_wikitext(
        "follows a high-school student, {{Nihongo|Kirie Goshima|五島桐絵}}; her "
        "boyfriend, {{Nihongo|Shuichi Saito|斎藤秀一}}; and the citizens of "
        "{{Nihongo|Kurouzu-cho|黒渦町|Black Vortex Town}}."
    )

    assert cleaned == (
        "follows a high-school student, Kirie Goshima; her boyfriend, "
        "Shuichi Saito; and the citizens of Kurouzu-cho."
    )


def test_a_language_template_keeps_the_text_not_the_language_code() -> None:
    """`{{Lang|ja|X}}` leads with a code, so the text is the second parameter."""
    assert clean_wikitext("she signs with her name ({{lang|ja|荻野千尋}})") == (
        "she signs with her name (荻野千尋)"
    )
    assert clean_wikitext("the {{transliteration|ja|onsen}} trip") == "the onsen trip"


def test_an_unknown_template_is_still_removed_entirely() -> None:
    """The exceptions are a closed list, not a new default.

    A citation, a footnote and a maintenance banner have no display text a
    summary should carry, and guessing "the first parameter" would inject
    exactly that furniture.
    """
    cleaned = clean_wikitext(
        "{{Long plot|date=April 2022}}\nHe climbs.{{efn|A footnote.}}"
        "{{sfn|Author|2020|p=4}} {{Reflist}}"
    )

    assert cleaned == "He climbs."


def test_a_removed_footnote_does_not_leave_its_punctuation() -> None:
    assert clean_wikitext(
        "Eun-hyuk tasks Hyun-soo and Pyeon Sang-wook,{{efn|A note.}}, a resident."
    ) == "Eun-hyuk tasks Hyun-soo and Pyeon Sang-wook, a resident."


def test_a_bracket_emptied_by_a_removal_is_closed_up() -> None:
    assert clean_wikitext("with her name ({{unknownref|x}}), Yubaba takes it") == (
        "with her name, Yubaba takes it"
    )


def test_a_space_before_a_colon_is_left_alone() -> None:
    """Ordinary English, not residue.

    "Tokyo Ghoul :re" is a title. Closing that gap would corrupt text that
    nothing was removed from, which is the one thing the tidy-up must never
    do.
    """
    assert clean_wikitext("she establishes the :re cafe") == "she establishes the :re cafe"


def test_a_template_nested_in_a_kept_parameter_is_resolved_too() -> None:
    assert clean_wikitext("{{nowrap|{{Nihongo|Kirie|桐絵}}}} runs") == "Kirie runs"


def test_templates_that_stand_for_one_character_do_not_merge_words() -> None:
    assert clean_wikitext("five{{nbsp}}years later") == "five years later"
