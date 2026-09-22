"""Wikipedia narrative adapter: episode-list wikitext -> episode summaries.

Deliberately narrow. It reads only the `ShortSummary` parameter of
`{{Episode list}}` / `{{Episode list/sublist}}` templates, which is where
English Wikipedia puts episode plot prose. Everything else on the page --
infoboxes, cast, production, reception, references, navigation -- is never
looked at, so there is no section-detection heuristic to get wrong and no
risk of ingesting article furniture as narrative.

Supported page structures:
  A. A dedicated "List of <series> episodes" article using {{Episode list}}.
  B. A season or main article embedding {{Episode list/sublist}}.
Both are the same template family, so one scanner covers both.

Episode numbers come from the template's own `EpisodeNumber` parameter. They
are never inferred from the order entries appear in: a table of contents, a
recap row, or a split season would silently shift every number if they were.
An entry whose number cannot be read is reported, not guessed at.

No network access lives here; the client hands this module plain text.
"""

import re
from dataclasses import dataclass, field

TEMPLATE_NAMES = ("episode list", "episode list/sublist")

# "1", "12"; also tolerates the "{{sort|1|1}}"-free plain forms only.
_PLAIN_NUMBER_RE = re.compile(r"^\d+$")

_REF_BLOCK_RE = re.compile(r"<ref[^>]*>.*?</ref>", re.IGNORECASE | re.DOTALL)
_REF_SELF_RE = re.compile(r"<ref[^>]*/\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]")
_EXTLINK_RE = re.compile(r"\[(?:https?|//)[^\s\]]+\s+([^\]]+)\]")
_EMPHASIS_RE = re.compile(r"'{2,5}")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class EpisodeSummary:
    """One episode's plot prose, with the number the source itself stated."""

    episode_number: int
    title: str | None
    summary: str


@dataclass
class ParsedEpisodeList:
    summaries: list[EpisodeSummary] = field(default_factory=list)
    # Entries deliberately not turned into summaries, with the reason, so a
    # coverage gap is visible rather than silent.
    skipped: list[dict] = field(default_factory=list)


# Templates whose rendered output *is* part of the sentence, and which
# position in the invocation carries that output.
#
# Dropping these was the default and it was wrong in a specific way: a
# template standing where a noun belongs leaves the punctuation around it
# behind. "{{Nihongo|Kirie Goshima|五島桐絵}}" removed entirely turns
#
#     follows a high-school student, {{Nihongo|Kirie Goshima|...}}; her
#     boyfriend, {{Nihongo|Shuichi Saito|...}}; and the citizens of ...
#
# into "follows a high-school student, ; her boyfriend, ; and the citizens",
# which reads as damage rather than as an omission.
#
# The table is deliberately a closed list of the forms actually found in the
# narrative sections of this corpus' source pages, and each entry records
# which positional parameter the article renders. Everything not named here
# keeps the old behaviour and is removed outright -- a citation, a footnote
# or a maintenance banner has no display text a summary should carry, and
# guessing "the first parameter" for unknown templates would inject exactly
# that kind of furniture.
#
# Two shapes exist. `{{Nihongo|English|kanji}}` leads with the English
# reading; `{{Lang|ja|うずまき}}` leads with a language code, so the text is
# the second positional parameter.
_TEMPLATE_DISPLAY_PARAMETER = {
    "nihongo": 1,
    "nihongo foot": 1,
    "nowrap": 1,
    "vanchor": 1,
    "visible anchor": 1,
    "ill": 1,
    "interlanguage link": 1,
    "interlanguage link multi": 1,
    "lang": 2,
    "langx": 2,
    "tlit": 2,
    "transliteration": 2,
}

# Templates that stand for one literal character. Removing them merges the
# words either side.
_TEMPLATE_LITERAL = {"nbsp": " ", "!": "|", "=": "=", "'": "'", "'s": "'s"}


def _positional(body: str) -> list[str]:
    """The positional (unnamed) parameters of a template body, in order.

    Reuses the depth-aware splitter, so a nested template or wikilink inside
    a parameter does not split it.
    """
    parts = _split_positional(body)
    # parts[0] is the template name; named parameters are dropped because a
    # positional index is what the table above refers to.
    return [part for part in parts[1:] if "=" not in part.split("[[")[0]]


def _split_positional(body: str) -> list[str]:
    """Split a template body on top-level pipes, name first."""
    parts: list[str] = []
    current: list[str] = []
    template_depth = 0
    link_depth = 0
    index = 0
    while index < len(body):
        if body.startswith("{{", index):
            template_depth += 1
            current.append("{{")
            index += 2
        elif body.startswith("}}", index):
            template_depth = max(0, template_depth - 1)
            current.append("}}")
            index += 2
        elif body.startswith("[[", index):
            link_depth += 1
            current.append("[[")
            index += 2
        elif body.startswith("]]", index):
            link_depth = max(0, link_depth - 1)
            current.append("]]")
            index += 2
        elif body[index] == "|" and template_depth == 0 and link_depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
        else:
            current.append(body[index])
            index += 1
    parts.append("".join(current))
    return parts


def _strip_templates(text: str) -> str:
    """Resolve {{...}} invocations: display text where there is any, else nothing.

    Nested invocations are resolved innermost-first, so a template inside a
    kept parameter is itself resolved rather than surviving as braces.
    """
    out: list[str] = []
    stack: list[list[str]] = []
    index = 0
    while index < len(text):
        if text.startswith("{{", index):
            stack.append([])
            index += 2
        elif text.startswith("}}", index) and stack:
            body = "".join(stack.pop())
            replacement = _render_template(body)
            (stack[-1] if stack else out).append(replacement)
            index += 2
        else:
            # A stray "}}" with nothing open is dropped, matching the old
            # behaviour of tolerating unbalanced braces rather than raising.
            if text.startswith("}}", index):
                index += 2
                continue
            (stack[-1] if stack else out).append(text[index])
            index += 1

    # An unclosed "{{" means malformed wikitext. Everything after it was
    # already being discarded before this change, and still is.
    return "".join(out)


def _render_template(body: str) -> str:
    """What one template invocation contributes to the prose, if anything."""
    parts = _split_positional(body)
    name = parts[0].strip().lower()

    literal = _TEMPLATE_LITERAL.get(name)
    if literal is not None:
        return literal

    position = _TEMPLATE_DISPLAY_PARAMETER.get(name)
    if position is None:
        return ""

    positional = _positional(body)
    if len(positional) < position:
        return ""
    return positional[position - 1].strip()


def clean_wikitext(raw: str) -> str:
    """Reduce summary wikitext to plain prose.

    Citations become nothing rather than inline noise, links keep their
    display text, and templates are dropped -- in a ShortSummary they are
    almost always notes or citations, never narrative. The exceptions are
    named in `_TEMPLATE_DISPLAY_PARAMETER`: a handful of templates whose
    output is a noun in the middle of a sentence, which are rendered rather
    than removed.

    Whatever is removed leaves its punctuation behind, so the last step
    tidies the residue: a footnote between two commas should not become two
    commas.
    """
    text = _REF_BLOCK_RE.sub("", raw)
    text = _REF_SELF_RE.sub("", text)
    text = _strip_templates(text)
    # Repeat: piped links can nest inside one another's display text.
    for _ in range(3):
        new_text = _WIKILINK_RE.sub(r"\1", text)
        if new_text == text:
            break
        text = new_text
    text = _EXTLINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&ndash;", "–").replace("&mdash;", "—")
    text = _WS_RE.sub(" ", text)
    text = _tidy_removal_residue(text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


# What a removal leaves behind, in the order the residue has to be unwound:
# an emptied bracket first, since clearing it creates the other two.
_EMPTY_BRACKET_RE = re.compile(r"[(\[]\s*[,;]?\s*[)\]]")
# Two separators with nothing left between them -- "Sang-wook, , a gangster".
_REPEATED_SEPARATOR_RE = re.compile(r"([,;])(?:\s*[,;])+")
# A separator that lost the word before it -- "a high-school student, ;".
#
# The colon is deliberately absent from all three. A space before one is
# ordinary English ("Tokyo Ghoul :re opens the cafe"), so closing that gap
# would corrupt text nothing had removed anything from -- which is exactly
# the failure this tidy-up must not introduce.
_ORPHANED_SEPARATOR_RE = re.compile(r"\s+([,;.!?])")


def _tidy_removal_residue(text: str) -> str:
    """Close up the punctuation a removed citation or footnote left behind.

    Only ever *deletes* characters the removal orphaned, and only whitespace
    and repeated separators at that. It never inserts a word, never joins two
    sentences and never reorders anything -- there is nothing here that could
    turn a gap into a claim.
    """
    text = _EMPTY_BRACKET_RE.sub("", text)
    text = _REPEATED_SEPARATOR_RE.sub(r"\1", text)
    text = _ORPHANED_SEPARATOR_RE.sub(r"\1", text)
    # The deletions above can leave a doubled space mid-line.
    return re.sub(r"[ \t]{2,}", " ", text)


def _iter_template_bodies(
    wikitext: str, template_names: tuple[str, ...] = TEMPLATE_NAMES
) -> list[str]:
    """Return the inner body of each matching template invocation."""
    bodies: list[str] = []
    index = 0
    while True:
        start = wikitext.find("{{", index)
        if start == -1:
            return bodies

        # Read the template name, which runs to the first | or the closing braces.
        cursor = start + 2
        name_chars: list[str] = []
        while cursor < len(wikitext) and wikitext[cursor] not in "|}":
            name_chars.append(wikitext[cursor])
            cursor += 1
        name = "".join(name_chars).strip().lower()

        if name not in template_names:
            index = start + 2
            continue

        # Walk to the matching close, tracking nesting.
        depth = 0
        scan = start
        end = None
        while scan < len(wikitext):
            if wikitext.startswith("{{", scan):
                depth += 1
                scan += 2
            elif wikitext.startswith("}}", scan):
                depth -= 1
                scan += 2
                if depth == 0:
                    end = scan
                    break
            else:
                scan += 1

        if end is None:
            # Unbalanced template: stop rather than mis-parse the rest.
            return bodies

        bodies.append(wikitext[start + 2 + len(name_chars) : end - 2])
        index = end


def _split_parameters(body: str) -> dict[str, str]:
    """Split a template body into named parameters at nesting depth zero."""
    parts: list[str] = []
    current: list[str] = []
    template_depth = 0
    link_depth = 0
    index = 0

    while index < len(body):
        if body.startswith("{{", index):
            template_depth += 1
            current.append("{{")
            index += 2
            continue
        if body.startswith("}}", index):
            template_depth = max(0, template_depth - 1)
            current.append("}}")
            index += 2
            continue
        if body.startswith("[[", index):
            link_depth += 1
            current.append("[[")
            index += 2
            continue
        if body.startswith("]]", index):
            link_depth = max(0, link_depth - 1)
            current.append("]]")
            index += 2
            continue
        if body[index] == "|" and template_depth == 0 and link_depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(body[index])
        index += 1

    parts.append("".join(current))

    parameters: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        parameters[key.strip().lower()] = value.strip()
    return parameters


def parse_episode_list(wikitext: str) -> ParsedEpisodeList:
    """Extract episode summaries from an episode-list page's wikitext."""
    result = ParsedEpisodeList()

    for body in _iter_template_bodies(wikitext):
        parameters = _split_parameters(body)
        raw_number = parameters.get("episodenumber", "").strip()
        raw_summary = parameters.get("shortsummary", "").strip()
        title = clean_wikitext(parameters.get("title", "")) or None

        if not raw_number:
            result.skipped.append(
                {"reason": "no_episode_number", "title": title, "raw_number": raw_number}
            )
            continue

        number_text = clean_wikitext(raw_number)
        if not _PLAIN_NUMBER_RE.match(number_text):
            # Double episodes ("1–2"), lettered specials, templated numbers:
            # real cases we decline rather than coerce into a guess.
            result.skipped.append(
                {"reason": "unparseable_episode_number", "title": title, "raw_number": number_text}
            )
            continue

        summary = clean_wikitext(raw_summary)
        if not summary:
            result.skipped.append(
                {"reason": "no_summary", "title": title, "episode_number": int(number_text)}
            )
            continue

        result.summaries.append(
            EpisodeSummary(episode_number=int(number_text), title=title, summary=summary)
        )

    return result


VOLUME_TEMPLATE_NAMES = ("graphic novel list",)


def parse_volume_list(wikitext: str) -> ParsedEpisodeList:
    """Extract per-volume summaries from a manga chapter-list article.

    The manga equivalent of `parse_episode_list`: English Wikipedia collects
    manga volumes in `{{Graphic novel list}}` rather than `{{Episode list}}`,
    with `VolumeNumber` and an optional `Summary`. The same refusals apply --
    a volume number that will not parse as an integer is reported rather than
    guessed at, and an entry with no summary is skipped.

    Coverage is uneven and that is the source's nature, not a parser bug:
    some series summarise every volume, many summarise none.
    """
    result = ParsedEpisodeList()

    for body in _iter_template_bodies(wikitext, VOLUME_TEMPLATE_NAMES):
        parameters = _split_parameters(body)
        raw_number = parameters.get("volumenumber", "").strip()
        raw_summary = parameters.get("summary", "").strip()
        title = clean_wikitext(parameters.get("title", "")) or None

        if not raw_number:
            result.skipped.append(
                {"reason": "no_volume_number", "title": title, "raw_number": raw_number}
            )
            continue

        number_text = clean_wikitext(raw_number)
        if not _PLAIN_NUMBER_RE.match(number_text):
            result.skipped.append(
                {"reason": "unparseable_volume_number", "title": title, "raw_number": number_text}
            )
            continue

        summary = clean_wikitext(raw_summary)
        if not summary:
            result.skipped.append(
                {"reason": "no_summary", "title": title, "episode_number": int(number_text)}
            )
            continue

        result.summaries.append(
            EpisodeSummary(episode_number=int(number_text), title=title, summary=summary)
        )

    return result


def _candidates(patterns: tuple[str, ...], titles: tuple[str | None, ...]) -> list[str]:
    """Every pattern applied to every known title, most specific pattern first.

    Ordered by pattern and not by title, because a wrong-but-existing page is
    worse than a missing one: "List of <english> chapters" is a chapter list
    whichever title found it, whereas the bare-title article is the series
    page, which holds no per-volume text. Trying every specific form before
    any general one keeps the bare title a genuine last resort.
    """
    ordered: list[str] = []
    for pattern in patterns:
        for title in titles:
            if not title:
                continue
            candidate = pattern.format(title)
            if candidate not in ordered:
                ordered.append(candidate)
    return ordered


def volume_list_candidates(*titles: str | None) -> list[str]:
    """Page titles worth trying for a manga's volume list, most specific first.

    Takes every title the source knows -- romaji, English, native -- because
    AniList's primary title is romaji while English Wikipedia files the same
    series under its English one: "Hagane no Renkinjutsushi" has no chapter
    list, "Fullmetal Alchemist" does.
    """
    return _candidates(("List of {} chapters", "List of {} volumes", "{}"), titles)


def episode_list_candidates(*titles: str | None) -> list[str]:
    """Page titles worth trying for a series, most specific first.

    Only the structures this phase supports. When none exist the caller
    records a coverage gap; it does not fall back to a looser search that
    could attach another series' text.
    """
    return _candidates(("List of {} episodes", "{}"), titles)


# --- work-level summaries ------------------------------------------------
#
# The parsers above read a template: a bounded, labelled structure that says
# what it is. This one reads a *section*, which is looser, so the refusals do
# the work instead.
#
# Only a level-2 heading the English Wikipedia fiction articles actually use
# for narrative counts, and inside it only prose and narrative subsections are
# kept -- a "Characters" or "Production" subsection under "Synopsis" is not a
# summary of the story and is dropped rather than swept in. If no such section
# exists the answer is "no summary here", never the lead paragraph or the
# article at large.

# Normalized level-2 headings that hold narrative description. Deliberately a
# closed list: an unrecognised heading is a coverage gap, not an invitation to
# guess what the section contains.
NARRATIVE_SECTIONS = (
    "plot",
    "plot summary",
    "synopsis",
    "premise",
    "story",
    "storyline",
)

# Below this a "summary" is a stub, a cross-reference, or a single sentence of
# framing -- text that would embed into noise and claim coverage the article
# does not give.
MINIMUM_WORK_SUMMARY_CHARS = 200

# A subsection named after a span of the work -- "Chapters 39-85", "Prologue",
# "Season 2", "Part One" -- is part of the story it sits under, however the
# article chose to divide it. This is a rule about what such a heading *is*,
# not a general willingness to accept unknown headings: a subsection that is
# neither narrative nor a span of the work is still dropped.
_STRUCTURAL_SPAN_RE = re.compile(
    r"^(prologue|epilogue|interlude|chapters?|episodes?|volumes?|parts?|arcs?|"
    r"seasons?|books?|acts?)\b"
)

_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SECTION_KEY_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class WorkSummary:
    """Narrative prose describing a whole work, and where on the page it sat."""

    # The heading as the article wrote it, kept for the unit's provenance so
    # "which part of which page is this" stays answerable.
    section: str
    summary: str


@dataclass
class ParsedWorkSummary:
    summary: WorkSummary | None = None
    # Why there is nothing, when there is nothing.
    skipped: list[dict] = field(default_factory=list)


def _section_key(heading: str) -> str:
    """Normalize a heading for comparison against the allowed list."""
    return _SECTION_KEY_RE.sub(" ", clean_wikitext(heading).lower()).strip()


def _is_narrative_subsection(heading: str) -> bool:
    """Does this subsection of a narrative section still describe the story?"""
    key = _section_key(heading)
    return key in NARRATIVE_SECTIONS or bool(_STRUCTURAL_SPAN_RE.match(key))


def _sections(wikitext: str) -> list[tuple[int, str, str]]:
    """Every heading as (level, heading text, body up to the next heading)."""
    matches = list(_HEADING_RE.finditer(wikitext))
    sections: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
        sections.append((len(match.group(1)), match.group(2), wikitext[start:end]))
    return sections


def parse_work_summary(wikitext: str) -> ParsedWorkSummary:
    """Extract one whole-work narrative summary from a series article.

    Used only where the canonical source catalogues no containers to attach
    per-episode or per-volume text to. Returns the first narrative section on
    the page, with any non-narrative subsection of it removed, or nothing at
    all with the reason recorded.
    """
    result = ParsedWorkSummary()
    sections = _sections(_COMMENT_RE.sub("", wikitext))

    index = 0
    while index < len(sections):
        level, heading, body = sections[index]
        if level != 2 or _section_key(heading) not in NARRATIVE_SECTIONS:
            index += 1
            continue

        parts = [body]
        cursor = index + 1
        while cursor < len(sections) and sections[cursor][0] > 2:
            sub_heading, sub_body = sections[cursor][1], sections[cursor][2]
            if _is_narrative_subsection(sub_heading):
                parts.append(sub_body)
            else:
                result.skipped.append(
                    {"reason": "subsection_not_narrative", "title": clean_wikitext(sub_heading)}
                )
            cursor += 1

        summary = clean_wikitext("\n\n".join(parts))
        if len(summary) < MINIMUM_WORK_SUMMARY_CHARS:
            result.skipped.append(
                {
                    "reason": "summary_too_short",
                    "title": clean_wikitext(heading),
                    "length": len(summary),
                }
            )
            index = cursor
            continue

        result.summary = WorkSummary(section=clean_wikitext(heading), summary=summary)
        return result

    if result.summary is None and not any(
        item["reason"] == "summary_too_short" for item in result.skipped
    ):
        result.skipped.append({"reason": "no_narrative_section", "title": None})
    return result


def work_article_candidates(*titles: str | None) -> list[str]:
    """Page titles worth trying for a series' own article, most specific first.

    Disambiguated forms come first for the same reason the list-article
    patterns do: "Noblesse (webtoon)" can only be the webtoon, while a bare
    "Noblesse" might be anything the encyclopaedia files under that word. The
    bare title stays last, and the caller corroborates whatever it resolves to
    before storing a word of it.
    """
    return _candidates(
        (
            "{} (webtoon)",
            "{} (manhwa)",
            "{} (manga)",
            "{} (manga series)",
            "{} (TV series)",
            "{} (anime)",
            "{}",
        ),
        titles,
    )
