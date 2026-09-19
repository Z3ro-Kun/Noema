"""Deterministic grouping of adjacent ContentUnits into contextual passages.

Why this exists: literature units in the corpus are 34 tokens at the median
-- often a single line of dialogue like `"You are," said the King.` -- which
carries almost no standalone meaning for an embedding model. Grouping
adjacent units gives the model enough context to represent a scene rather
than a fragment.

**The strategy, fixed before any retrieval was evaluated:**

    window      = 3 adjacent ContentUnits
    overlap     = 1 unit  (A+B+C, then C+D+E, then E+F+G)
    max_tokens  = 240

Chosen from the measured corpus, not by convention. At window=3 with no
bound, 10% of passages would exceed the model's 256-token window; with the
240-token bound that falls to 0.5%, and those remaining two are single
ContentUnits that individually exceed the bound (the longest is 271 tokens).
Source units are never split, so those two stay truncated rather than being
cut into pieces the source never had.

Grouping uses **source order only**. No embeddings, no clustering, no
similarity -- the groups must be reproducible without running a model, and a
grouping that depended on embeddings would make the experiment circular.

A passage never crosses a Container boundary: chapters are a real narrative
break, and a passage spanning two of them would be an artifact.

Note the token bound makes grouping model-dependent, which is intentional --
the bound exists because of that model's input window. `grouping_config`
records it so a change is detectable.
"""

import hashlib
from dataclasses import dataclass
from typing import Callable, Sequence

from app.services.embedding.preparation import PREP_VERSION, EmptyTextError, prepare_text

DEFAULT_WINDOW = 3
DEFAULT_OVERLAP = 1
DEFAULT_MAX_TOKENS = 240

# Joins units into one passage. A blank line marks the original unit
# boundary, so the model sees paragraph structure rather than a run-on.
UNIT_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class GroupingConfig:
    window: int = DEFAULT_WINDOW
    overlap: int = DEFAULT_OVERLAP
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be at least 1")
        if not 0 <= self.overlap < self.window:
            raise ValueError("overlap must be >= 0 and smaller than window")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")

    @property
    def key(self) -> str:
        """Stable identity string, stored on every passage."""
        return f"window={self.window};overlap={self.overlap};max_tokens={self.max_tokens}"


@dataclass(frozen=True)
class SourceUnit:
    """The minimum a unit must expose to be grouped."""

    id: object
    sequence_number: int
    text: str
    text_tier: str


@dataclass(frozen=True)
class PassageDraft:
    sequence_number: int
    source_unit_ids: list
    first_unit_sequence: int
    last_unit_sequence: int
    text_content: str
    text_tier: str
    grouping_config: str
    source_hash: str

    @property
    def unit_count(self) -> int:
        return len(self.source_unit_ids)


def passage_hash(config: GroupingConfig, prepared_texts: Sequence[str]) -> str:
    """Identity of a passage's content.

    Covers the grouping configuration, the preparation version, and the
    ordered unit texts -- so a text edit, a reordering, a preparation change
    or a config change all produce a different hash, and therefore a stale
    embedding.
    """
    parts = [config.key, f"prep={PREP_VERSION}"]
    # Length-prefixed so no delimiter can be forged by the text itself.
    parts.extend(f"{len(text)}:{text}" for text in prepared_texts)
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_passages(
    units: Sequence[SourceUnit],
    count_tokens: Callable[[str], int],
    config: GroupingConfig | None = None,
) -> list[PassageDraft]:
    """Group one container's units, in order, into contextual passages.

    `units` must already be a single container's units in source order; the
    caller is responsible for not mixing containers, which is what keeps a
    passage from spanning a chapter break.
    """
    config = config or GroupingConfig()

    prepared: list[tuple[SourceUnit, str, int]] = []
    for unit in units:
        try:
            text = prepare_text(unit.text)
        except EmptyTextError:
            # An empty unit contributes nothing and is skipped rather than
            # padding a passage with whitespace.
            continue
        prepared.append((unit, text, count_tokens(text)))

    if not prepared:
        return []

    passages: list[PassageDraft] = []
    index = 0
    while index < len(prepared):
        group: list[tuple[SourceUnit, str, int]] = []
        total = 0
        cursor = index

        while cursor < len(prepared) and len(group) < config.window:
            _, _, tokens = prepared[cursor]
            # A first unit is always taken, even if it alone exceeds the
            # bound: splitting a source unit would invent a boundary the
            # source never had.
            if group and total + tokens > config.max_tokens:
                break
            group.append(prepared[cursor])
            total += tokens
            cursor += 1

        texts = [text for _, text, _ in group]
        passages.append(
            PassageDraft(
                sequence_number=len(passages) + 1,
                source_unit_ids=[unit.id for unit, _, _ in group],
                first_unit_sequence=group[0][0].sequence_number,
                last_unit_sequence=group[-1][0].sequence_number,
                text_content=UNIT_SEPARATOR.join(texts),
                # Passages inherit their units' tier; mixed tiers would make
                # tier filtering meaningless, so the first unit's tier wins
                # and mixing is prevented upstream by grouping per container.
                text_tier=group[0][0].text_tier,
                grouping_config=config.key,
                source_hash=passage_hash(config, texts),
            )
        )

        if cursor >= len(prepared):
            break
        # Step forward, re-including `overlap` units for continuity, but
        # always advancing at least one so this cannot loop forever.
        index = max(index + 1, cursor - config.overlap)

    return passages
