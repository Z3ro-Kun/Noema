"""Reader profiles built to exercise the recommendation engine's semantics.

**Not an accuracy benchmark.** Nothing here asserts that a particular work is
the right answer for a particular reader; there is no ground truth for that,
and inventing one would make the suite a record of somebody's taste rather
than of the engine's behaviour. What these profiles establish is whether the
engine does what Noema says it does under controlled preference evidence:
that reasons trace to real evidence, that combinations need both constituents,
that negatives subtract, that emerging signals never speak, and that a sparse
profile is answered honestly instead of filled.

**Nothing is injected.** Each profile is a list of works and ratings, replayed
through `auth_service` and `library_service` exactly as `dataset.py` replays
the preference-engine cases. The preference engine therefore does the same
work here that it does for a real reader, and a case that produces no
established preference is telling us something true rather than something
misconfigured.

The works are referenced by `(source, source_ref)` against the real corpus,
because the concept associations are what make the profiles mean anything. A
missing work raises rather than quietly building a smaller, different case.

---

The cases, and what each is for

    A   thriller-heavy          concentrated evidence; candidates sharing
                                those concepts should be supported and the
                                explanation should name them
    B   literature-oriented     evidence on concepts that literature works
                                actually carry, so "can literature surface at
                                all" is answerable separately from "is
                                literature usually ranked highly"
    C   anime-oriented          the same question from the other side
    D   mixed-domain            evidence on science fiction, the one concept
                                group spanning all three media
    E   positive and negative   a clean split: the positives carry no horror,
                                the negatives carry nothing else, and the
                                catalogue holds candidates on both sides
    F   sparse                  one rating. The engine must say so.
    G   combination             reproduces the shape `dataset.py` case S uses,
                                which is the only one on this corpus that
                                reliably establishes a pair

Case E's separation is worth stating precisely, because it is what makes the
case a test rather than a coincidence: none of its four positively-rated
works carries `horror`, and none of its four negatively-rated works carries
`crime-and-investigation`. So a candidate carrying both is matched by one
established preference in each direction, and nothing else.
"""

from dataclasses import dataclass, field

from app.models import STATUS_COMPLETED, STATUS_IN_PROGRESS

# The same reserved domain family the preference-engine fixtures use, so
# cleanup can match on it exactly and can never reach a real account.
RECOMMENDATION_EMAIL_DOMAIN = "@recommendation-evaluation.invalid"
RECOMMENDATION_PASSWORD = "recommendation-fixture-password"


@dataclass(frozen=True)
class CaseInteraction:
    source: str
    source_ref: str
    rating: int | None
    statuses: tuple[str, ...] = (STATUS_IN_PROGRESS, STATUS_COMPLETED)


@dataclass(frozen=True)
class RecommendationCase:
    """One profile, and the invariant it exists to exercise."""

    key: str
    case: str
    profile: str
    # What the engine is expected to *do*, in terms of its own rules. Never
    # "should recommend work X", which would be a taste claim.
    expectation: str
    interactions: tuple[CaseInteraction, ...] = field(default_factory=tuple)

    @property
    def email(self) -> str:
        return f"{self.key}{RECOMMENDATION_EMAIL_DOMAIN}"


def _rated(works, ratings) -> tuple[CaseInteraction, ...]:
    return tuple(
        CaseInteraction(source=source, source_ref=ref, rating=rating)
        for (source, ref), rating in zip(works, ratings)
    )


# --- the works each case is built from -------------------------------------
#
# Chosen from the real corpus for their concept membership, which is recorded
# beside each group so a reader of this file can check it against
# `work_concepts` rather than trust the comment.

# Carry `thriller` and `crime-and-investigation`.
THRILLERS = (
    ("anilist", "1535"),  # Death Note
    ("anilist", "19"),  # Monster
    ("anilist", "9253"),  # Steins;Gate
    ("anilist", "1575"),  # Code Geass
)

# Literature carrying `psychological-depth` or `science-fiction` -- the two
# concepts the literature half of the corpus is most often described by.
LITERARY = (
    ("gutenberg", "5200"),  # Metamorphosis          psychological-depth
    ("gutenberg", "219"),  # Heart of Darkness       psychological-depth
    ("gutenberg", "768"),  # Wuthering Heights       psychological-depth
    ("gutenberg", "35"),  # The Time Machine         science-fiction
    ("gutenberg", "36"),  # The War of the Worlds    science-fiction
)

ANIME_FAVOURITES = (
    ("anilist", "1"),  # Cowboy Bebop
    ("anilist", "918"),  # Gintama
    ("anilist", "2001"),  # Gurren Lagann
    ("anilist", "30"),  # Neon Genesis Evangelion
)

# `science-fiction` is the only concept group spanning all three media.
CROSS_DOMAIN = (
    ("anilist", "9253"),  # Steins;Gate              anime
    ("anilist", "339"),  # Serial Experiments Lain   anime
    ("anilist", "98416"),  # Dr. STONE               manhwa
    ("gutenberg", "35"),  # The Time Machine         literature
    ("gutenberg", "36"),  # The War of the Worlds    literature
)

# Carry `crime-and-investigation`; not one of them carries `horror`.
CRIME_NOT_HORROR = (
    ("anilist", "1"),  # Cowboy Bebop
    ("anilist", "1535"),  # Death Note
    ("anilist", "918"),  # Gintama
    ("gutenberg", "1661"),  # The Adventures of Sherlock Holmes
)

# Carry `horror`; not one of them carries `crime-and-investigation`.
HORROR_NOT_CRIME = (
    ("anilist", "101922"),  # Demon Slayer
    ("gutenberg", "345"),  # Dracula
    ("anilist", "30436"),  # Uzumaki
    ("anilist", "105778"),  # Chainsaw Man
)

# Case G, reproducing the only pair this corpus reliably establishes: four
# works carrying both `crime-and-investigation` and `urban-modernity` rated
# highly, and three carrying one without the other rated poorly.
COMBINATION_LIKED = (
    ("anilist", "1"),  # Cowboy Bebop
    ("anilist", "5"),  # Cowboy Bebop: The Movie
    ("anilist", "1535"),  # Death Note
    ("anilist", "19"),  # Monster
)
# The third carries `urban-modernity` without `crime-and-investigation`,
# which is the premise that makes the pair more selective than either part.
# It was Neon Genesis Evangelion until the concept audit found NGE's only
# claim to `urban-modernity` was an AniList "Artificial Intelligence" tag at
# rank 13 -- an alias naming a subject rather than a setting, corrected out
# of the vocabulary. Nana carries the concept on its own evidence.
COMBINATION_DISLIKED = (
    ("anilist", "5114"),  # Fullmetal Alchemist: Brotherhood
    ("gutenberg", "1661"),  # The Adventures of Sherlock Holmes
    ("anilist", "30028"),  # Nana
)


RECOMMENDATION_CASES: tuple[RecommendationCase, ...] = (
    RecommendationCase(
        key="rec-case-a-thriller",
        case="A",
        profile="Four thrillers rated 9-10, all carrying crime and investigation.",
        expectation=(
            "Established positive preferences form around the concepts those "
            "works share. Candidates carrying them are supported, and every "
            "stated reason names one of those preferences."
        ),
        interactions=_rated(THRILLERS, (10, 10, 9, 9)),
    ),
    RecommendationCase(
        key="rec-case-b-literature",
        case="B",
        profile="Five works of literature rated 9-10.",
        expectation=(
            "Literature candidates can surface. Nothing requires an embedding "
            "or any text at all, and the medium is neither favoured nor "
            "excluded -- only the concepts decide."
        ),
        interactions=_rated(LITERARY, (10, 10, 9, 9, 9)),
    ),
    RecommendationCase(
        key="rec-case-c-anime",
        case="C",
        profile="Four anime series rated 9-10.",
        expectation=(
            "Anime candidates surface by the same mechanism, with reasons "
            "naming established preferences rather than the medium."
        ),
        interactions=_rated(ANIME_FAVOURITES, (10, 10, 9, 9)),
    ),
    RecommendationCase(
        key="rec-case-d-mixed",
        case="D",
        profile=(
            "Five science-fiction works rated highly, spanning anime, manga "
            "and literature."
        ),
        expectation=(
            "Recommendations can cross media, and the diversity rule spaces "
            "them rather than removing a medium that has candidates."
        ),
        interactions=_rated(CROSS_DOMAIN, (10, 10, 9, 9, 9)),
    ),
    RecommendationCase(
        key="rec-case-e-both-directions",
        case="E",
        profile=(
            "Four crime works rated 9-10, none carrying horror; four horror "
            "works rated 2-3, none carrying crime."
        ),
        expectation=(
            "Positives support and negatives subtract. A candidate carrying "
            "both is ranked below one carrying only the liked concept and "
            "declares the dislike as a caution; a candidate carrying only the "
            "disliked concept is not recommended at all."
        ),
        interactions=(
            *_rated(CRIME_NOT_HORROR, (10, 10, 9, 9)),
            *_rated(HORROR_NOT_CRIME, (3, 2, 2, 3)),
        ),
    ),
    RecommendationCase(
        key="rec-case-f-sparse",
        case="F",
        profile="One work rated.",
        expectation=(
            "No established preference, so no personalized shelf. The state "
            "says which kind of nothing it is, and no popularity fallback "
            "appears in its place."
        ),
        interactions=_rated((THRILLERS[0],), (9,)),
    ),
    RecommendationCase(
        key="rec-case-g-combination",
        case="G",
        profile=(
            "Four works carrying both crime and investigation and urban "
            "modernity rated 9-10; three carrying one without the other rated "
            "2-3."
        ),
        expectation=(
            "A combination reaches established status. It may only explain a "
            "candidate carrying BOTH of its concepts -- a work carrying one "
            "constituent must never claim the pair."
        ),
        interactions=(
            *_rated(COMBINATION_LIKED, (10, 10, 9, 9)),
            *_rated(COMBINATION_DISLIKED, (3, 2, 3)),
        ),
    ),
)


def referenced_works() -> set[tuple[str, str]]:
    return {
        (interaction.source, interaction.source_ref)
        for case in RECOMMENDATION_CASES
        for interaction in case.interactions
    }
