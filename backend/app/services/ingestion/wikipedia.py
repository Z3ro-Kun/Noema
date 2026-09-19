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


def _strip_templates(text: str) -> str:
    """Remove {{...}} invocations, including nested ones."""
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        if text.startswith("{{", index):
            depth += 1
            index += 2
        elif text.startswith("}}", index):
            depth = max(0, depth - 1)
            index += 2
        else:
            if depth == 0:
                out.append(text[index])
            index += 1
    return "".join(out)


def clean_wikitext(raw: str) -> str:
    """Reduce summary wikitext to plain prose.

    Citations become nothing rather than inline noise, links keep their
    display text, and templates are dropped -- in a ShortSummary they are
    almost always notes or citations, never narrative.
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
    text = _BLANKLINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


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
