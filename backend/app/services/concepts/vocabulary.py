"""The canonical V1 concept vocabulary.

`Concept` was introduced in Phase 0 as shared cross-domain vocabulary -- its
`name` is unique and it is deliberately not scoped to a work -- but nothing
ever populated it. This module is that vocabulary: a small, curated, closed
list, defined in one place so the same idea cannot enter the database twice
under different words.

The failure this exists to prevent is a taxonomy that drifts into
"Psychological", "psychological drama", "Psychological Themes" and "mental
conflict" as four unrelated rows. Three things stop that:

  One definition    A concept exists here or it does not exist at all.
                    Nothing in the population path invents a concept.
  A slug            The stable identity. Display names can be reworded; the
                    slug is what the database enforces uniqueness on.
  Explicit aliases  Every external label that means this concept is listed
                    on it, so mapping is a lookup rather than a judgement
                    made per work.

Deliberately *not* an ontology. There is no hierarchy, no inheritance and no
relations between concepts. Those are real design decisions and nothing
consumes them yet.

Scope: AniList supplies 169 distinct tags across the ingested corpus, most of
them demographic, content-advisory or trivia ("Shounen", "Nudity", "Trains",
"Kuudere"). Importing all of them would recreate the drift this module
exists to prevent, and would bury narrative features under noise. Only
narratively meaningful labels are mapped; the rest are reported as unmapped
rather than silently dropped.
"""

import re
from dataclasses import dataclass

# `concepts.concept_type` has no CHECK constraint; these are the values this
# vocabulary uses. "genre" is included because a genre is genuinely a
# different kind of claim from a theme, and flattening them would lose that.
TYPE_THEME = "theme"
TYPE_MOTIF = "motif"
TYPE_GENRE = "genre"

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """The stable identity for a concept label.

    Casefold, strip punctuation, collapse separators. "Coming of Age",
    "coming-of-age" and "Coming Of Age" all resolve to one slug, which is
    what makes the uniqueness constraint mean "one concept per idea" rather
    than "one row per spelling".
    """
    return _NON_ALNUM_RE.sub("-", text.strip().lower()).strip("-")


@dataclass(frozen=True)
class ConceptDefinition:
    """One vocabulary entry.

    `aliases` carries every external label that means this concept:
    AniList genres and tags for anime and manga, Library of Congress Subject
    Headings for literature. Mapping is therefore a lookup, never a
    judgement made per work.
    """

    slug: str
    name: str
    concept_type: str
    description: str
    aliases: tuple[str, ...] = ()


VOCABULARY: tuple[ConceptDefinition, ...] = (
    # --- themes ---------------------------------------------------------
    ConceptDefinition(
        slug="psychological-depth",
        name="Psychological Depth",
        concept_type=TYPE_THEME,
        description="Sustained attention to characters' interior mental states.",
        aliases=("Psychological", "Psychological fiction"),
    ),
    ConceptDefinition(
        slug="existential-questioning",
        name="Existential Questioning",
        concept_type=TYPE_THEME,
        description="Characters confronting meaning, purpose, or the nature of existence.",
        aliases=("Philosophy", "Philosophical fiction"),
    ),
    ConceptDefinition(
        slug="moral-ambiguity",
        name="Moral Ambiguity",
        concept_type=TYPE_THEME,
        description="Choices without a clearly right answer; protagonists who are not simply good.",
        aliases=("Anti-Hero",),
    ),
    ConceptDefinition(
        slug="identity",
        name="Identity",
        concept_type=TYPE_THEME,
        description="Uncertainty or transformation in who a character understands themselves to be.",
        aliases=("Dissociative Identities", "Identity (Psychology)"),
    ),
    ConceptDefinition(
        slug="memory-and-forgetting",
        name="Memory and Forgetting",
        concept_type=TYPE_THEME,
        description="Memory as unreliable, lost, or deliberately altered.",
        aliases=("Amnesia", "Memory Manipulation"),
    ),
    ConceptDefinition(
        slug="grief-and-loss",
        name="Grief and Loss",
        concept_type=TYPE_THEME,
        description="Mourning, bereavement, and living with absence.",
        aliases=("Bereavement", "Grief"),
    ),
    ConceptDefinition(
        slug="isolation",
        name="Isolation",
        concept_type=TYPE_THEME,
        description="Loneliness and estrangement from other people.",
        aliases=("Loneliness", "Alienation (Social psychology)"),
    ),
    ConceptDefinition(
        slug="coming-of-age",
        name="Coming of Age",
        concept_type=TYPE_THEME,
        description="Growing up, and the loss of innocence that accompanies it.",
        aliases=("Coming of Age", "Bildungsromans", "Coming of age"),
    ),
    ConceptDefinition(
        slug="revenge",
        name="Revenge",
        concept_type=TYPE_THEME,
        description="Vengeance pursued for a wrong suffered.",
        aliases=("Revenge", "Vengeance"),
    ),
    ConceptDefinition(
        slug="redemption",
        name="Redemption",
        concept_type=TYPE_THEME,
        description="Atonement for past wrongs and the attempt to become better.",
        aliases=("Rehabilitation",),
    ),
    ConceptDefinition(
        slug="mortality",
        name="Mortality",
        concept_type=TYPE_THEME,
        description="Death as a subject rather than merely an event.",
        aliases=("Suicide", "Death"),
    ),
    ConceptDefinition(
        slug="scientific-overreach",
        name="Scientific Overreach",
        concept_type=TYPE_THEME,
        description="Knowledge or experiment pursued past the point of control.",
        aliases=("Human Experimentation",),
    ),
    ConceptDefinition(
        slug="sacrifice",
        name="Sacrifice",
        concept_type=TYPE_THEME,
        description="Giving up something of great value for someone or something else.",
    ),
    ConceptDefinition(
        slug="found-family",
        name="Found Family",
        concept_type=TYPE_THEME,
        description="Bonds chosen rather than inherited.",
        aliases=("Found Family", "Adoption", "Orphan"),
    ),
    # --- motifs ---------------------------------------------------------
    ConceptDefinition(
        slug="war",
        name="War",
        concept_type=TYPE_MOTIF,
        description="Armed conflict as setting or subject.",
        aliases=("War", "Military", "War stories"),
    ),
    ConceptDefinition(
        slug="crime-and-investigation",
        name="Crime and Investigation",
        concept_type=TYPE_MOTIF,
        description="Crime, detection, and the pursuit of a culprit.",
        aliases=(
            "Crime", "Detective", "Police", "Fugitive",
            "Detective and mystery stories", "Private investigators", "Criminals",
        ),
    ),
    ConceptDefinition(
        slug="political-intrigue",
        name="Political Intrigue",
        concept_type=TYPE_MOTIF,
        description="Conspiracy, statecraft, and hidden power.",
        aliases=("Conspiracy", "Politics", "Espionage", "Political fiction", "Spy stories"),
    ),
    ConceptDefinition(
        slug="post-apocalypse",
        name="Post-Apocalypse",
        concept_type=TYPE_MOTIF,
        description="Life after a civilisation-ending catastrophe.",
        aliases=("Post-Apocalyptic", "Dystopian", "Lost Civilization", "Survival"),
    ),
    ConceptDefinition(
        slug="time-manipulation",
        name="Time Manipulation",
        concept_type=TYPE_MOTIF,
        description="Time travel, loops, or altered chronology.",
        aliases=("Time Manipulation", "Time Loop"),
    ),
    ConceptDefinition(
        slug="the-supernatural",
        name="The Supernatural",
        concept_type=TYPE_MOTIF,
        description="Forces outside the natural order.",
        aliases=(
            "Supernatural", "Gods", "Religion", "Necromancy",
            "Monsters", "Ghost stories", "Supernatural fiction",
        ),
    ),
    ConceptDefinition(
        slug="urban-modernity",
        name="Urban Modernity",
        concept_type=TYPE_MOTIF,
        description="The modern city, technology, and networked life.",
        aliases=("Urban", "Cyberpunk", "Virtual World", "Artificial Intelligence"),
    ),
    ConceptDefinition(
        slug="historical-setting",
        name="Historical Setting",
        concept_type=TYPE_MOTIF,
        description="A recognisably historical period.",
        aliases=("Historical", "Vikings", "Historical fiction"),
    ),
    # --- genres ---------------------------------------------------------
    ConceptDefinition(
        slug="tragedy",
        name="Tragedy",
        concept_type=TYPE_GENRE,
        description="A trajectory toward loss or ruin.",
        aliases=("Tragedy", "Tragic"),
    ),
    ConceptDefinition(
        slug="horror",
        name="Horror",
        concept_type=TYPE_GENRE,
        description="Dread, revulsion, and fear as the intended effect.",
        aliases=(
            "Horror", "Cosmic Horror", "Body Horror",
            "Horror tales", "Gothic fiction",
        ),
    ),
    ConceptDefinition(
        slug="mystery",
        name="Mystery",
        concept_type=TYPE_GENRE,
        description="A withheld truth that the narrative works to uncover.",
        aliases=("Mystery", "Mystery fiction"),
    ),
    ConceptDefinition(
        slug="thriller",
        name="Thriller",
        concept_type=TYPE_GENRE,
        description="Sustained tension and imminent danger.",
        aliases=("Thriller",),
    ),
    ConceptDefinition(
        slug="science-fiction",
        name="Science Fiction",
        concept_type=TYPE_GENRE,
        description="Speculative technology or science as premise.",
        aliases=("Sci-Fi", "Mecha", "Space", "Cyborg", "Aliens", "Science fiction"),
    ),
    ConceptDefinition(
        slug="fantasy",
        name="Fantasy",
        concept_type=TYPE_GENRE,
        description="Magic or invented worlds as premise.",
        aliases=(
            "Fantasy", "Urban Fantasy", "Magic", "Alchemy", "Super Power",
            "Fantasy fiction", "Imaginary places",
        ),
    ),
    ConceptDefinition(
        slug="adventure",
        name="Adventure",
        concept_type=TYPE_GENRE,
        description="Journey, exploration, and incident.",
        aliases=("Adventure", "Travel", "Adventure stories", "Voyages and travels"),
    ),
    ConceptDefinition(
        slug="comedy",
        name="Comedy",
        concept_type=TYPE_GENRE,
        description="Humour as a primary register.",
        aliases=("Comedy", "Humorous stories", "Satire"),
    ),
    ConceptDefinition(
        slug="action",
        name="Action",
        concept_type=TYPE_GENRE,
        description="Physical conflict and kinetic spectacle.",
        aliases=("Action", "Martial Arts"),
    ),
    ConceptDefinition(
        slug="drama",
        name="Drama",
        concept_type=TYPE_GENRE,
        description="Serious interpersonal and emotional conflict.",
        aliases=("Drama",),
    ),
)


BY_SLUG: dict[str, ConceptDefinition] = {entry.slug: entry for entry in VOCABULARY}

# External label (casefolded) -> slug. Built from the aliases plus each
# concept's own name, so a source label matches whether it is spelled the way
# AniList spells it or the way the vocabulary does.
_ALIAS_INDEX: dict[str, str] = {}
for _entry in VOCABULARY:
    for _label in (_entry.name, _entry.slug, *_entry.aliases):
        _ALIAS_INDEX.setdefault(slugify(_label), _entry.slug)


def resolve_label(label: str) -> ConceptDefinition | None:
    """Map an external label to a vocabulary entry, or None if unmapped.

    Returning None is a normal outcome, not a failure: most AniList tags are
    demographic or advisory and have no place in a narrative vocabulary. The
    caller reports what went unmapped rather than inventing a concept.
    """
    if not label:
        return None
    return BY_SLUG.get(_ALIAS_INDEX.get(slugify(label), ""))


# LCSH headings carry subdivisions ("Monsters -- Fiction") and language
# qualifiers ("Detective and mystery stories, English"). The main heading is
# what names the subject; the qualifiers narrow it.
def _label_variants(label: str) -> list[str]:
    variants = [label]
    main = label.split(" -- ")[0].strip()
    if main != label:
        variants.append(main)
    for candidate in list(variants):
        head = candidate.split(",")[0].strip()
        if head and head != candidate:
            variants.append(head)
    return variants


def resolve_source_label(label: str) -> ConceptDefinition | None:
    """Map a source-provided label to a concept, trying LCSH subdivisions.

    Tries the whole heading first so a specific mapping always beats a
    general one, then progressively strips subdivisions and qualifiers.
    """
    for variant in _label_variants(label or ""):
        found = resolve_label(variant)
        if found is not None:
            return found
    return None
