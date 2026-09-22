"""The evaluation library: deliberately constructed interaction histories.

**This is test material, not production data.** Nothing here is inserted into
the development corpus by any normal code path. Tests build it inside a
transaction that is rolled back; the CLI that materialises it for manual
inspection uses a reserved e-mail domain and can remove it exactly.

Its purpose is to make the *future* preference engine falsifiable. The single
claim that engine must never make is

    "consumed X"  =>  "prefers X"

so this dataset contains two users with an identical exposure pattern and
opposite ratings. Any engine that reports the same preference for both is
wrong, and these fixtures are how that gets caught.

It references the real corpus by `(source, source_ref)` rather than inventing
works, because the concept associations that make the patterns meaningful are
real ones from Phase 1M. Missing works raise rather than silently producing a
smaller, different dataset -- a fixture that quietly degrades is worse than
one that fails.

Cases A-I (Phase 1N) cover the exposure/rating boundary. Cases J-N (Phase
1S) cover feature *combinations*. Cases O-W (Phase 1T) cover selection: which
of many discovered patterns a profile should actually show, and which of them
are the same finding under different names.

Cases AK and AN (Phase 1V) cover the two ends of the semantic-bucket rule:
a history confident enough to be called a strong preference, and negative
evidence too thin to be called a dislike.

Cases AG and AH (Phase 1U) cover what an insight may not say: a fictional
theme is not biography, and a rating carries no account of its own cause.

Cases J-N cover feature *combinations*: a pair that repeats, a pair seen once, a
pair that contradicts itself, a concept that is common across the corpus, and
a single work carrying enough concepts to generate 153 candidate pairs.

Each user documents its own exposure, rating, reconsumption and abandonment
patterns and the *qualitative* reading expected of them. Those expectations
deliberately stop at evidence -- "strong positive evidence for
psychological-depth" -- and never reach for a personality label. Turning
evidence into traits belongs to a phase that does not exist yet.

No preference is computed here. No rating is normalised. This module builds
histories and asserts nothing about what they mean.
"""

from dataclasses import dataclass, field

# Reserved by RFC 6761, so these addresses can never collide with a real one
# and cleanup can match on the domain exactly.
EVALUATION_EMAIL_DOMAIN = "@evaluation.invalid"
EVALUATION_PASSWORD = "evaluation-fixture-password"

# --- the works these patterns are built from ------------------------------
# Addressed by source identity, which is stable across re-ingestion.

# Carry `psychological-depth`. Four anime and one work of literature, so a
# preference for the concept is separable from a preference for the medium.
PSYCHOLOGICAL = (
    ("anilist", "1535"),  # DEATH NOTE
    ("anilist", "19"),  # MONSTER
    ("anilist", "339"),  # serial experiments lain
    ("anilist", "9253"),  # Steins;Gate
    ("gutenberg", "5200"),  # Metamorphosis
)

# Carry `crime-and-investigation`, across anime and literature.
CRIME = (
    ("anilist", "1"),  # Cowboy Bebop
    ("anilist", "1535"),  # DEATH NOTE
    ("gutenberg", "1661"),  # The Adventures of Sherlock Holmes
)

# Carry `science-fiction`, and are the only concept group spanning all three
# domains -- the material for a genuinely cross-medium signal.
SCIENCE_FICTION = (
    ("anilist", "339"),  # serial experiments lain        (anime)
    ("anilist", "9253"),  # Steins;Gate                   (anime)
    ("gutenberg", "84"),  # Frankenstein                  (literature)
    ("anilist", "98416"),  # Dr. STONE                    (manga)
)

# A mixed bag with no shared concept, for the user whose taste should not
# generalise.
ASSORTED = (
    ("anilist", "30642"),  # Vinland Saga      (manga)
    ("gutenberg", "11"),  # Alice in Wonderland (literature)
    ("anilist", "30"),  # Shin Seiki Evangelion (anime)
    ("anilist", "105398"),  # Solo Leveling     (manhwa)
)

STATUS_PLANNED = "planned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_ON_HOLD = "on_hold"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"


@dataclass(frozen=True)
class EvaluationInteraction:
    """One user's history with one work, as a sequence of real transitions.

    `statuses` is applied in order through the ordinary library service, so
    the resulting `UserContentEvent` trail is genuine rather than
    back-dated -- which is what makes reconsumption testable at all.
    """

    source: str
    source_ref: str
    statuses: tuple[str, ...] = (STATUS_COMPLETED,)
    rating: int | None = None
    # Soft-removed after the fact: tests that the evidence survives removal.
    removed: bool = False


@dataclass(frozen=True)
class EvaluationUser:
    """One deliberately-shaped history, with the reading expected of it."""

    key: str
    case: str
    exposure: str
    rating_pattern: str
    reconsumption: str
    abandonment: str
    # Qualitative, evidence-level, and deliberately not a personality label.
    expectation: str
    interactions: tuple[EvaluationInteraction, ...] = field(default_factory=tuple)

    @property
    def email(self) -> str:
        return f"{self.key}{EVALUATION_EMAIL_DOMAIN}"


def _rated(works, ratings, statuses=(STATUS_IN_PROGRESS, STATUS_COMPLETED)):
    return tuple(
        EvaluationInteraction(source=s, source_ref=r, statuses=statuses, rating=rating)
        for (s, r), rating in zip(works, ratings)
    )


EVALUATION_USERS: tuple[EvaluationUser, ...] = (
    # --- A -----------------------------------------------------------------
    EvaluationUser(
        key="case-a-strong-positive",
        case="A",
        exposure="Five works carrying psychological-depth, across anime and literature.",
        rating_pattern="Uniformly high: 9, 9, 10, 8, 9. A generous rater (8-10).",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Strong positive evidence for psychological-depth. The signal is "
            "concept-level rather than medium-level, because the same ratings "
            "span anime and literature."
        ),
        interactions=_rated(PSYCHOLOGICAL, (9, 9, 10, 8, 9)),
    ),
    # --- B -----------------------------------------------------------------
    EvaluationUser(
        key="case-b-exposure-not-preference",
        case="B",
        exposure="The same five psychological-depth works as case A. Identical exposure.",
        rating_pattern="Uniformly low: 4, 5, 3, 4, 5. A harsh rater (3-5).",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Strong NEGATIVE evidence for psychological-depth, despite exposure "
            "identical to case A. The load-bearing fixture of this dataset: an "
            "engine that reports the same preference for A and B has inferred "
            "preference from consumption and is wrong."
        ),
        interactions=_rated(PSYCHOLOGICAL, (4, 5, 3, 4, 5)),
    ),
    # --- C -----------------------------------------------------------------
    EvaluationUser(
        key="case-c-unrated-exposure",
        case="C",
        exposure="Three crime-and-investigation works, added, started and completed.",
        rating_pattern="None at all. Every interaction is unrated.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Weak positive evidence for crime-and-investigation -- weaker than "
            "any explicit rating. Completion is engagement, not approval, and "
            "unrated must not be read as neutral-or-good."
        ),
        interactions=_rated(CRIME, (None, None, None)),
    ),
    # --- D -----------------------------------------------------------------
    EvaluationUser(
        key="case-d-abandonment",
        case="D",
        exposure="Two works started; one abandoned, one left on hold.",
        rating_pattern="None. Neither work was rated.",
        reconsumption="None.",
        abandonment="One work abandoned after being started, with no rating.",
        expectation=(
            "Ambiguous evidence, and it must stay ambiguous. Abandonment may "
            "mean dislike, wrong moment, or lost access. It must not be given a "
            "fixed negative weight, and on_hold must not be read as abandonment."
        ),
        interactions=(
            EvaluationInteraction(
                source="anilist",
                source_ref="30",
                statuses=(STATUS_IN_PROGRESS, STATUS_ABANDONED),
            ),
            EvaluationInteraction(
                source="anilist",
                source_ref="5114",
                statuses=(STATUS_IN_PROGRESS, STATUS_ON_HOLD),
            ),
        ),
    ),
    # --- E -----------------------------------------------------------------
    EvaluationUser(
        key="case-e-reconsumption",
        case="E",
        exposure="One work completed three times; one companion work completed once.",
        rating_pattern="Both rated 9, so rating alone cannot separate them.",
        reconsumption="Three full start-to-completion cycles of the same work.",
        abandonment="None.",
        expectation=(
            "Stronger behavioural evidence for the reconsumed work than for the "
            "one-time work, even though their ratings are identical. Every "
            "earlier cycle must remain in the event history."
        ),
        interactions=(
            EvaluationInteraction(
                source="anilist",
                source_ref="1",
                statuses=(
                    STATUS_IN_PROGRESS,
                    STATUS_COMPLETED,
                    STATUS_IN_PROGRESS,
                    STATUS_COMPLETED,
                    STATUS_IN_PROGRESS,
                    STATUS_COMPLETED,
                ),
                rating=9,
            ),
            EvaluationInteraction(
                source="anilist",
                source_ref="19",
                statuses=(STATUS_IN_PROGRESS, STATUS_COMPLETED),
                rating=9,
            ),
        ),
    ),
    # --- F -----------------------------------------------------------------
    EvaluationUser(
        key="case-f-mixed",
        case="F",
        exposure="Four works sharing no concept, one per medium where possible.",
        rating_pattern="Genuinely mixed: 9, 3, 7, 5. Wide spread, no trend.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "No strong concept-level preference in either direction. Tests "
            "whether the engine overgeneralises from a small, inconsistent "
            "history; the honest output here is low confidence, not a verdict."
        ),
        interactions=_rated(ASSORTED, (9, 3, 7, 5)),
    ),
    # --- G -----------------------------------------------------------------
    EvaluationUser(
        key="case-g-cross-domain",
        case="G",
        exposure=(
            "Four science-fiction works spanning all three domains: anime, "
            "literature and manga."
        ),
        rating_pattern="Uniformly high: 9, 10, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Strong positive evidence for science-fiction as a cross-medium "
            "signal. An engine that concludes 'prefers anime' has memorised the "
            "medium instead of learning the concept."
        ),
        interactions=_rated(SCIENCE_FICTION, (9, 10, 9, 9)),
    ),
    # --- H -----------------------------------------------------------------
    EvaluationUser(
        key="case-h-harsh-rater",
        case="H",
        exposure=(
            "Four science-fiction works -- the same set as case G -- plus three "
            "unrelated works."
        ),
        rating_pattern=(
            "Compressed and low: 6, 7, 7, 6 for science fiction against 2, 3, 3 "
            "for everything else. This user's personal maximum is 7."
        ),
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Positive evidence for science-fiction *relative to this user's own "
            "distribution*. A raw 7 here is a top-of-range rating, while a 7 "
            "from case A would be below their average. Any engine comparing raw "
            "numbers across users will read this backwards. Phase 1N only "
            "creates the case; it does not normalise."
        ),
        interactions=(
            *_rated(SCIENCE_FICTION, (6, 7, 7, 6)),
            *_rated(ASSORTED[:3], (2, 3, 3)),
        ),
    ),
    # --- I -----------------------------------------------------------------
    EvaluationUser(
        key="case-i-removed-but-rated",
        case="I",
        exposure="Two works completed and rated highly, then removed from the library.",
        rating_pattern="High: 10 and 9.",
        reconsumption="None.",
        abandonment="None. Removal is not abandonment.",
        expectation=(
            "The evidence survives removal. Tidying a shelf is not a retraction, "
            "and an engine that only reads the current library will miss the "
            "strongest ratings this user gave."
        ),
        interactions=(
            EvaluationInteraction(
                source="anilist",
                source_ref="5114",
                statuses=(STATUS_IN_PROGRESS, STATUS_COMPLETED),
                rating=10,
                removed=True,
            ),
            EvaluationInteraction(
                source="gutenberg",
                source_ref="84",
                statuses=(STATUS_IN_PROGRESS, STATUS_COMPLETED),
                rating=9,
                removed=True,
            ),
        ),
    ),
    # --- J -----------------------------------------------------------------
    # The combination cases (J-N) were added in Phase 1S. They are built from
    # the same real corpus works as A-I, and their shapes are chosen from the
    # actual concept sets rather than from what would be convenient: the pair
    # in J really does occur in exactly three corpus works, and the eighteen
    # concepts in N are Fullmetal Alchemist's real annotation.
    EvaluationUser(
        key="case-j-repeated-combination",
        case="J",
        exposure=(
            "Six works. Three carry BOTH crime-and-investigation and mystery; "
            "two carry crime-and-investigation without mystery; one carries "
            "mystery without crime-and-investigation."
        ),
        rating_pattern=(
            "The three works carrying both are rated 10, 10, 9. The works "
            "carrying only one of the pair are rated 4, 3 and 3."
        ),
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "The pair is separable from its parts. Both constituents have "
            "mixed evidence because each also appears in poorly rated works, "
            "while the three works carrying both are rated highly. This is the "
            "one shape in which a combination has genuinely earned its place: "
            "it is more selective than either part and says something neither "
            "part says alone."
        ),
        interactions=(
            *_rated(
                (("anilist", "5"), ("anilist", "1535"), ("anilist", "19")),
                (10, 10, 9),
            ),
            *_rated(
                (("gutenberg", "1661"), ("anilist", "1")),
                (3, 4),
            ),
            *_rated((("anilist", "339"),), (3,)),
        ),
    ),
    # --- K -----------------------------------------------------------------
    EvaluationUser(
        key="case-k-one-off-combination",
        case="K",
        exposure=(
            "Four works rated highly. scientific-overreach and "
            "time-manipulation co-occur in exactly one of them; each appears "
            "separately in others."
        ),
        rating_pattern="Uniformly high: 10, 9, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "No combination pattern. A pair seen once cannot be distinguished "
            "from coincidence, however highly that one work was rated -- the "
            "rating attaches to the work, not to any pair of its concepts."
        ),
        interactions=_rated(
            (
                ("anilist", "9253"),
                ("anilist", "5114"),
                ("anilist", "98416"),
                ("anilist", "30025"),
            ),
            (10, 9, 9, 9),
        ),
    ),
    # --- L -----------------------------------------------------------------
    EvaluationUser(
        key="case-l-conflicting-combination",
        case="L",
        exposure=(
            "Six works. Four carry both psychological-depth and mystery; one "
            "carries psychological-depth alone and one mystery alone."
        ),
        rating_pattern=(
            "The four works carrying the pair are rated 10, 9, 3 and 2 -- the "
            "pair is present in both the best and the worst of them."
        ),
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "No confident claim about the pair in either direction. The "
            "combination occurs in works this user loved and works they "
            "disliked, so its own evidence must come out mixed and its "
            "agreement low. An engine that reports a clean preference here has "
            "averaged away the disagreement that is the whole point."
        ),
        interactions=(
            *_rated(
                (
                    ("anilist", "1535"),
                    ("anilist", "19"),
                    ("anilist", "339"),
                    ("anilist", "30"),
                ),
                (10, 9, 3, 2),
            ),
            *_rated((("gutenberg", "5200"),), (9,)),
            *_rated((("anilist", "5"),), (8,)),
        ),
    ),
    # --- M -----------------------------------------------------------------
    EvaluationUser(
        key="case-m-common-feature",
        case="M",
        exposure=(
            "Four works spanning anime and manga/manhwa, all carrying "
            "`tragedy` -- the single most common concept in the corpus, on "
            "eleven of seventeen works."
        ),
        rating_pattern="Uniformly high: 9, 9, 10, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Strong positive evidence for tragedy, undiminished by how common "
            "tragedy is. Phase 1Q established that corpus frequency is not a "
            "reason to discount a user's own ratings; this fixture is what "
            "would catch a regression back into suppressing common concepts."
        ),
        interactions=_rated(
            (
                ("anilist", "1"),
                ("anilist", "9253"),
                ("anilist", "98416"),
                ("anilist", "30642"),
            ),
            (9, 9, 10, 9),
        ),
    ),
    # --- N -----------------------------------------------------------------
    EvaluationUser(
        key="case-n-feature-explosion",
        case="N",
        exposure=(
            "Two works: Fullmetal Alchemist, which carries eighteen concepts, "
            "and Alice in Wonderland, which carries one. They share `fantasy`."
        ),
        rating_pattern="Both rated highly: 10 and 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "One rating on an eighteen-concept work offers 153 candidate "
            "pairs and must yield none of them. The only feature with two "
            "rated works behind it is `fantasy`, and one shared work is not "
            "enough to establish even that. A system that turns one 10/10 "
            "into a page of confident patterns has learned nothing about the "
            "reader except that they liked one thing."
        ),
        interactions=_rated(
            (("anilist", "5114"), ("gutenberg", "11")),
            (10, 9),
        ),
    ),
    # --- O -----------------------------------------------------------------
    # Cases O-W were added in Phase 1T, which selects rather than discovers.
    # They are shaped around redundancy and cardinality: how many patterns a
    # history really supports, and how many of those say the same thing.
    EvaluationUser(
        key="case-o-redundant-candidates",
        case="O",
        exposure=(
            "Four heavily overlapping anime -- DEATH NOTE, MONSTER, serial "
            "experiments lain and Steins;Gate -- which between them carry "
            "more than twenty concepts, many on all four."
        ),
        rating_pattern="Uniformly high: 10, 10, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Many established patterns, few distinct findings. Concepts "
            "carried by exactly the same rated works have identical evidence "
            "and identical confidence, so a profile listing all of them "
            "reports one observation several times. The key section must be "
            "much smaller than the candidate pool."
        ),
        interactions=_rated(
            (
                ("anilist", "1535"),
                ("anilist", "19"),
                ("anilist", "339"),
                ("anilist", "9253"),
            ),
            (10, 10, 9, 9),
        ),
    ),
    # --- P -----------------------------------------------------------------
    EvaluationUser(
        key="case-p-complementary-patterns",
        case="P",
        exposure=(
            "Two groups of three works with little overlap: a crime/mystery "
            "group of anime, and an adventure/war group spanning manga and "
            "manhwa."
        ),
        rating_pattern="All rated highly: 9, 10, 9 and 10, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Several patterns survive together, because they rest on "
            "different rated works. Patterns that share a feature but not "
            "their evidence are genuinely different aspects of a taste, and "
            "collapsing them would lose real information."
        ),
        interactions=(
            *_rated(
                (("anilist", "5"), ("anilist", "1535"), ("anilist", "19")),
                (9, 10, 9),
            ),
            *_rated(
                (("anilist", "98416"), ("anilist", "30025"), ("anilist", "30642")),
                (10, 9, 9),
            ),
        ),
    ),
    # --- Q -----------------------------------------------------------------
    EvaluationUser(
        key="case-q-few-valid-patterns",
        case="Q",
        exposure=(
            "Four works with very little in common: Frankenstein, "
            "Metamorphosis, serial experiments lain and Steins;Gate."
        ),
        rating_pattern="High: 10, 10, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Fewer than five patterns, and no attempt to reach five. A "
            "profile is as large as the evidence makes it; padding it with "
            "weaker candidates would be inventing taste to fill a layout."
        ),
        interactions=_rated(
            (
                ("gutenberg", "84"),
                ("gutenberg", "5200"),
                ("anilist", "339"),
                ("anilist", "9253"),
            ),
            (10, 10, 9, 9),
        ),
    ),
    # --- R -----------------------------------------------------------------
    EvaluationUser(
        key="case-r-many-strong-patterns",
        case="R",
        exposure=(
            "Nine works across all three domains, deliberately varied so "
            "that many different sets of rated works support something."
        ),
        rating_pattern="Uniformly high: 9s and 10s.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "More distinct findings than a profile should show. The key "
            "section is capped at eight; the remainder must be recorded as "
            "having been cut for space rather than silently dropped."
        ),
        interactions=_rated(
            (
                ("anilist", "1"),
                ("anilist", "5"),
                ("anilist", "1535"),
                ("anilist", "5114"),
                ("anilist", "19"),
                ("anilist", "339"),
                ("anilist", "98416"),
                ("anilist", "30642"),
                ("gutenberg", "84"),
            ),
            (9, 10, 9, 10, 9, 10, 9, 10, 9),
        ),
    ),
    # --- S -----------------------------------------------------------------
    EvaluationUser(
        key="case-s-established-combination",
        case="S",
        exposure=(
            "Four works carrying BOTH crime-and-investigation and "
            "urban-modernity, rated highly; two carrying crime without urban "
            "modernity and one carrying urban modernity without crime, rated "
            "poorly."
        ),
        rating_pattern="10, 10, 9, 9 for the four; 3, 2 and 3 for the rest.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "A combination reaches established status, which on this corpus "
            "is rare. It must not crowd out the individual features that are "
            "equally well evidenced, and the profile must not become a list "
            "of pairs."
        ),
        interactions=(
            *_rated(
                (
                    ("anilist", "1"),
                    ("anilist", "5"),
                    ("anilist", "1535"),
                    ("anilist", "19"),
                ),
                (10, 10, 9, 9),
            ),
            *_rated(
                (("anilist", "5114"), ("gutenberg", "1661")),
                (3, 2),
            ),
            # Neon Genesis Evangelion until the Phase 1AG concept audit: its
            # only claim to `urban-modernity` was an AniList "Artificial
            # Intelligence" tag at rank 13, via an alias that named a subject
            # rather than a setting and was corrected out of the vocabulary.
            # Nana carries the concept on its own evidence and, like NGE,
            # carries no `crime-and-investigation` -- which is the premise
            # this third work exists to supply.
            *_rated((("anilist", "30028"),), (3,)),
        ),
    ),
    # --- T -----------------------------------------------------------------
    EvaluationUser(
        key="case-t-negative-pattern",
        case="T",
        exposure=(
            "Three science-fiction works rated highly and three fantasy "
            "works rated poorly."
        ),
        rating_pattern="10, 9, 9 against 3, 2, 2.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Both directions in one profile. A well-evidenced dislike is a "
            "finding, and must be selectable on the same terms as a liking "
            "-- without forcing the two into balance."
        ),
        interactions=(
            *_rated(
                (("anilist", "9253"), ("anilist", "339"), ("gutenberg", "84")),
                (10, 9, 9),
            ),
            *_rated(
                (
                    ("anilist", "30025"),
                    ("gutenberg", "11"),
                    ("anilist", "105398"),
                ),
                (3, 2, 2),
            ),
        ),
    ),
    # --- U -----------------------------------------------------------------
    EvaluationUser(
        key="case-u-emerging-and-established",
        case="U",
        exposure=(
            "Three crime anime rated highly, plus two fantasy works sharing "
            "only one concept between them."
        ),
        rating_pattern="9, 9, 9 for the first group; 10 and 9 for the pair.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Established patterns take the key section; the two-work pattern "
            "is real but unsettled and belongs in early signals. A larger "
            "evidence magnitude must not let it displace a better-supported "
            "finding."
        ),
        interactions=(
            *_rated(
                (("anilist", "5"), ("anilist", "1535"), ("anilist", "19")),
                (9, 9, 9),
            ),
            *_rated(
                (("gutenberg", "11"), ("anilist", "105398")),
                (10, 9),
            ),
        ),
    ),
    # --- V -----------------------------------------------------------------
    EvaluationUser(
        key="case-v-cross-domain-profile",
        case="V",
        exposure=(
            "Science fiction across all three domains -- Frankenstein, Dr. "
            "STONE, Steins;Gate and serial experiments lain -- plus one more "
            "work of literature."
        ),
        rating_pattern="Uniformly high: 9, 10, 9, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Domain provenance survives selection intact. Breadth is "
            "information about where a taste was observed, not a reason to "
            "rank a pattern above one seen in a single medium."
        ),
        interactions=_rated(
            (
                ("gutenberg", "84"),
                ("anilist", "98416"),
                ("anilist", "9253"),
                ("anilist", "339"),
                ("gutenberg", "5200"),
            ),
            (9, 10, 9, 9, 9),
        ),
    ),
    # --- W -----------------------------------------------------------------
    EvaluationUser(
        key="case-w-identical-support",
        case="W",
        exposure=(
            "Three anime that share a great many concepts -- MONSTER, serial "
            "experiments lain and Steins;Gate -- and nothing else."
        ),
        rating_pattern="Identical: 9, 9, 9.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "The extreme case of redundancy. Every concept on all three "
            "works has exactly the same evidence, the same confidence and "
            "the same supporting works; nothing in this history separates "
            "them. One entry may be shown, and the alternatives must be "
            "reported rather than discarded or presented as separate "
            "findings."
        ),
        interactions=_rated(
            (("anilist", "19"), ("anilist", "339"), ("anilist", "9253")),
            (9, 9, 9),
        ),
    ),
    # --- AG ----------------------------------------------------------------
    # Cases AG and AH were added in Phase 1U. The other insight cases (X-AF,
    # AI, AJ) are shapes A-W already provide; rather than clone a fixture to
    # give it a second letter, the Phase 1U tests name the case they cover
    # and use the existing user. These two are genuinely new shapes.
    EvaluationUser(
        key="case-ag-fictional-theme",
        case="AG",
        exposure=(
            "Four works rated highly that all carry `found-family` -- stories "
            "about people assembling a family from those around them, which "
            "in fiction usually means the one they were born into is absent."
        ),
        rating_pattern="Uniformly high: 10, 9, 9, 10.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "A media-taste observation and nothing else. This reader has "
            "shown positive preference evidence for stories about found "
            "family. That is a fact about what they enjoy reading and "
            "watching. It is not evidence about their parents, their "
            "childhood or their relationships, and no field in the insight "
            "representation may be capable of carrying such a claim."
        ),
        interactions=_rated(
            (
                ("anilist", "1"),
                ("anilist", "5114"),
                ("anilist", "19"),
                ("anilist", "30642"),
            ),
            (10, 9, 9, 10),
        ),
    ),
    # --- AH ----------------------------------------------------------------
    EvaluationUser(
        key="case-ah-unknown-cause",
        case="AH",
        exposure="Three works rated at this reader's maximum, sharing several concepts.",
        rating_pattern="Identical: 10, 10, 10.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "Shared features, and no account of why. Noema knows these works "
            "were rated 10 and knows what concepts they carry. It does not "
            "know whether they were chosen out of curiosity, on a friend's "
            "recommendation, from a list, out of obligation or from genuine "
            "interest, and nothing in the output may imply that it does."
        ),
        interactions=_rated(
            (("anilist", "5"), ("anilist", "1535"), ("anilist", "19")),
            (10, 10, 10),
        ),
    ),
    # --- AK ----------------------------------------------------------------
    # AK and AN were added in Phase 1V for the two semantic-bucket boundaries
    # the corpus could not otherwise demonstrate deliberately: a history rich
    # enough to reach `strongly likes`, and negative evidence that must stay
    # out of `dislikes`. The other 1V cases (AL, AM, AO-AZ) are shapes A-AH
    # already provide, and the Phase 1V tests name the case they cover.
    EvaluationUser(
        key="case-ak-strong-and-confident",
        case="AK",
        exposure=(
            "Seven works all carrying `tragedy`, spanning anime and "
            "manga/manhwa. Six of them also carry existential questioning; "
            "one does not, so the two concepts stay separable."
        ),
        rating_pattern="Identical and high: 9 throughout.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "The first history in the library rich enough to earn a strong "
            "claim. Seven consistently high ratings put confidence in the "
            "existing `high` band and the evidence past half the scale, which "
            "is what `strongly likes` requires. A reader with five ratings "
            "gets `mildly likes` instead, and that difference is the whole "
            "point of having two positive groups."
        ),
        interactions=_rated(
            (
                ("anilist", "1"),
                ("anilist", "1535"),
                ("anilist", "5114"),
                ("anilist", "19"),
                ("anilist", "339"),
                ("anilist", "9253"),
                ("anilist", "105398"),
            ),
            (9, 9, 9, 9, 9, 9, 9),
        ),
    ),
    # --- AN ----------------------------------------------------------------
    EvaluationUser(
        key="case-an-uncertain-negative",
        case="AN",
        exposure=(
            "Three science-fiction works rated highly, and two fantasy works "
            "rated poorly -- only two, which is the minimum support."
        ),
        rating_pattern="9, 9, 9 against 3 and 2.",
        reconsumption="None.",
        abandonment="None.",
        expectation=(
            "The negative evidence is real and must not be presented as an "
            "established dislike. Two rated works is exactly the minimum, "
            "which Phase 1S calls emerging rather than established, so this "
            "belongs in early signals. Telling a reader they dislike fantasy "
            "on the strength of two low ratings would be a claim the "
            "evidence does not carry."
        ),
        interactions=(
            *_rated(
                (("anilist", "9253"), ("anilist", "339"), ("gutenberg", "84")),
                (9, 9, 9),
            ),
            *_rated(
                (("gutenberg", "11"), ("anilist", "105398")),
                (3, 2),
            ),
        ),
    ),
)


def user_by_case(case: str) -> EvaluationUser:
    for user in EVALUATION_USERS:
        if user.case == case:
            return user
    raise KeyError(f"no evaluation user for case {case!r}")


def referenced_works() -> set[tuple[str, str]]:
    """Every (source, source_ref) the dataset depends on."""
    return {
        (interaction.source, interaction.source_ref)
        for user in EVALUATION_USERS
        for interaction in user.interactions
    }
