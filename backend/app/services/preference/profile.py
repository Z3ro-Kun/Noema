"""Phase 1T: turning many discovered patterns into a small profile.

Phase 1S discovers. This composes. It takes the `TasteProfile` that
aggregation produced and decides which of its patterns are worth putting in
front of a reader, which belong in a secondary section, and which say nothing
the others do not.

**It computes nothing.** Every selected entry holds the `TastePattern` object
itself -- not a copy, not a re-derivation -- so evidence, direction,
confidence, supporting works, domains and the behavioural channels are the
same objects aggregation produced. A test asserts this by identity, which is
a stronger guarantee than comparing fields.

---

The rules, in plain English

  1. Only established patterns are eligible for the key section. Emerging
     ones are real but not settled, and go to their own section rather than
     competing for the same slots.

  2. Patterns supported by the *identical* set of rated works are one
     finding, not several. The reader sees one of them, and the others are
     recorded beside it as indistinguishable alternatives.

  3. What survives is ordered the way the preference page already orders --
     confidence, then evidence magnitude, then name -- and the first eight
     are the key patterns.

  4. If nothing individual would otherwise appear, the best-supported
     individual pattern takes the last slot, so a profile is never made
     entirely of pairs while plain features are equally well evidenced.

  5. Nothing is added to reach a target. Five to eight is what a rich history
     produces, not a quota.

That is the whole of it. There is no composite score, no diversity term, no
novelty bonus and no rarity adjustment -- corpus frequency is not consulted
here any more than it is in Phase 1S, and a test asserts the module never
imports it.

---

Why identical support is the right notion of redundancy

Measured on the evaluation library, case A produces thirteen established
patterns resting on **five** distinct sets of rated works: six of them --
existential questioning, memory and forgetting, mortality, political
intrigue, tragedy, urban modernity -- are carried by exactly the same four
works, rated the same way, and therefore have identical evidence and
identical confidence. Nothing in this reader's history separates them. Six
entries saying so is not six findings; it is one finding printed six times.

The rule stops exactly there, at *identical*. Two patterns differing by even
one rated work rest on different evidence and are allowed to stand as
separate entries, because the alternative -- discarding a pattern for
overlapping "enough" with another -- needs a similarity threshold, and a
threshold here would be a quality judgement about which aspect of a reader's
taste matters. Psychological + Mystery and Psychological + Romance can both
be true of the same person.

Which member of an indistinguishable group is shown is decided by one rule
and is otherwise arbitrary: the combination goes first, because Phase 1S
admitted pairs through three tests a single feature never faced, so of two
equally supported descriptions the pair is the more specific true one. After
that the existing ordering applies, ties on the name, and the choice means
nothing -- which is why the alternatives are always reported, so a later
interface can show them rather than pretend it did.
Note what is deliberately *not* used as a tiebreak: how much the reader was
exposed to a concept. That is consumption, and letting it choose the
representative would let consumption decide what a preference profile leads
with.

---

What this layer still refuses to do

No wording, and no claim about why anything was rated. It selects and groups,
and stops. Deriving structured observations from what it selected belongs to
`insights.py`, which reads a `ComposedProfile` and produces nothing this
module needs to know about -- the dependency runs one way, from selection to
interpretation, and never back.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.parameters import DEFAULT_PARAMETERS, PreferenceParameters
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    KIND_INDIVIDUAL,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    TasteParameters,
    TastePattern,
    build_taste_profile,
)

# Roughly five to eight, as a ceiling only. A reader with two supported
# patterns gets two; the floor is whatever the evidence yields.
MAX_KEY_PATTERNS = 8
MAX_EARLY_SIGNALS = 8

# Why a candidate did not reach the key section. Recorded for inspection and
# never shown to a reader.
REJECTED_NOT_ESTABLISHED = "not_established"
REJECTED_INDISTINGUISHABLE = "indistinguishable_from_selected"
REJECTED_SECTION_FULL = "section_full"


@dataclass(frozen=True)
class SelectedPattern:
    """One pattern chosen for the profile, plus what it stands in for.

    `pattern` is the aggregation layer's own object. Nothing here wraps,
    copies or adjusts its values.
    """

    pattern: TastePattern
    indistinguishable_from: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.pattern.key

    @property
    def has_alternatives(self) -> bool:
        return bool(self.indistinguishable_from)


@dataclass
class SelectionDiagnostics:
    """Why the profile looks the way it does. Internal only."""

    candidates: int = 0
    eligible: int = 0
    distinct_support_sets: int = 0
    selected: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    # Groups of patterns the evidence cannot tell apart, largest first.
    indistinguishable_groups: list[tuple[str, ...]] = field(default_factory=list)
    individual_guarantee_applied: bool = False


@dataclass
class ComposedProfile:
    """What a later interface would render, and nothing more."""

    user_id: uuid.UUID
    key_patterns: list[SelectedPattern] = field(default_factory=list)
    early_signals: list[SelectedPattern] = field(default_factory=list)
    diagnostics: SelectionDiagnostics = field(default_factory=SelectionDiagnostics)

    def keys(self) -> list[str]:
        return [item.key for item in self.key_patterns]

    def patterns(self) -> list[TastePattern]:
        return [item.pattern for item in self.key_patterns]


def rated_support(pattern: TastePattern) -> frozenset[uuid.UUID]:
    """The rated works behind a pattern -- the evidence it actually rests on.

    Unrated supporting works are excluded deliberately. They explain why a
    concept is present at all, but they carry no direction, so two patterns
    differing only in unrated works are not thereby distinguishable.
    """
    return frozenset(
        work.work_id for work in pattern.supporting_works if work.rating is not None
    )


def _representative_key(pattern: TastePattern) -> tuple:
    """Which member of an indistinguishable group is shown.

    **Combination before individual**, which is not the Occam answer and is
    deliberate. Both describe the same rated works, so neither claims more
    than the evidence carries -- but Phase 1S admitted the pair only after
    checking that it is strictly more selective than both its parts, that its
    evidence differs from both, and that no sibling pair rests on the same
    works. The single feature cleared the support minimum and nothing else. Of
    two equally supported descriptions of the same works, the one that
    survived more tests is the more specific true thing to say, and a profile
    should be as specific as its evidence allows.

    Then the ordering the preference page already uses, which inside such a
    group is a tie by construction, so in practice this resolves on the name
    -- arbitrary, and reported as arbitrary.
    """
    return (
        1 if pattern.kind == KIND_INDIVIDUAL else 0,
        -pattern.confidence,
        -abs(pattern.preference_evidence or 0.0),
        pattern.key,
    )


def _ordering_key(selected: SelectedPattern) -> tuple:
    """Phase 1R's ordering, reused rather than re-invented."""
    pattern = selected.pattern
    return (
        -pattern.confidence,
        -abs(pattern.preference_evidence or 0.0),
        pattern.key,
    )


def _collapse(patterns: list[TastePattern]) -> list[SelectedPattern]:
    """One entry per distinct set of rated works."""
    groups: dict[frozenset[uuid.UUID], list[TastePattern]] = defaultdict(list)
    for pattern in patterns:
        groups[rated_support(pattern)].append(pattern)

    collapsed: list[SelectedPattern] = []
    for members in groups.values():
        members.sort(key=_representative_key)
        head, *rest = members
        collapsed.append(
            SelectedPattern(
                pattern=head,
                indistinguishable_from=tuple(sorted(item.key for item in rest)),
            )
        )
    collapsed.sort(key=_ordering_key)
    return collapsed


def compose_from_patterns(
    user_id: uuid.UUID,
    patterns: list[TastePattern],
    max_key_patterns: int = MAX_KEY_PATTERNS,
    max_early_signals: int = MAX_EARLY_SIGNALS,
) -> ComposedProfile:
    """The selection itself: a pure function of the aggregated patterns.

    Separated from the query so it can be exercised directly on constructed
    inputs -- the cap and the individual guarantee are reachable on shapes
    the 17-work corpus cannot currently produce, and a rule that cannot be
    tested is a rule nobody can trust.
    """
    rejected: dict[str, int] = defaultdict(int)

    established = [
        pattern for pattern in patterns if pattern.status == STATUS_ESTABLISHED
    ]
    emerging = [
        pattern for pattern in patterns if pattern.status == STATUS_EMERGING
    ]
    rejected[REJECTED_NOT_ESTABLISHED] = len(emerging)

    collapsed = _collapse(established)
    rejected[REJECTED_INDISTINGUISHABLE] = len(established) - len(collapsed)

    key_patterns = collapsed[:max_key_patterns]
    rejected[REJECTED_SECTION_FULL] = len(collapsed) - len(key_patterns)

    # A profile made entirely of pairs, while plain features are equally well
    # evidenced, reads as though the pairs were the finding. If nothing
    # individual would otherwise appear, the best-supported one takes the
    # last slot.
    #
    # Only where there is something to balance: with a single slot the
    # section cannot be "entirely pairs" in any meaningful sense, and taking
    # the one slot from a better-supported combination would be a worse
    # answer than the problem.
    guarantee_applied = False
    if len(key_patterns) > 1 and not any(
        item.pattern.kind == KIND_INDIVIDUAL for item in key_patterns
    ):
        individual = next(
            (item for item in collapsed if item.pattern.kind == KIND_INDIVIDUAL),
            None,
        )
        if individual is not None:
            key_patterns = [*key_patterns[: max_key_patterns - 1], individual]
            key_patterns.sort(key=_ordering_key)
            guarantee_applied = True

    early_signals = _collapse(emerging)[:max_early_signals]

    diagnostics = SelectionDiagnostics(
        candidates=len(patterns),
        eligible=len(established),
        distinct_support_sets=len(collapsed),
        selected=len(key_patterns),
        rejected={key: value for key, value in sorted(rejected.items()) if value},
        indistinguishable_groups=sorted(
            (
                (item.key, *item.indistinguishable_from)
                for item in collapsed
                if item.has_alternatives
            ),
            key=lambda group: (-len(group), group),
        ),
        individual_guarantee_applied=guarantee_applied,
    )

    return ComposedProfile(
        user_id=user_id,
        key_patterns=key_patterns,
        early_signals=early_signals,
        diagnostics=diagnostics,
    )


async def compose_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    parameters: PreferenceParameters = DEFAULT_PARAMETERS,
    taste: TasteParameters = DEFAULT_TASTE_PARAMETERS,
    max_key_patterns: int = MAX_KEY_PATTERNS,
    max_early_signals: int = MAX_EARLY_SIGNALS,
) -> ComposedProfile:
    """Select a small profile from everything aggregation discovered."""
    aggregated = await build_taste_profile(session, user_id, parameters, taste)
    return compose_from_patterns(
        user_id, aggregated.patterns, max_key_patterns, max_early_signals
    )


# --- why nothing here is persisted ----------------------------------------
#
# The composed profile is a view over a view: aggregation is a pure function
# of interactions and work-concept associations, and selection is a pure
# function of aggregation. One changed rating invalidates both. Storing it
# would create a third place where "what this user likes" is recorded, free
# to drift from the two below it, in exchange for a speed-up nothing has
# asked for.
#
# The cost today is one preference query plus a pass over a few hundred
# candidate pairs. The moment to revisit is when that stops fitting in a
# request, which it does not.
