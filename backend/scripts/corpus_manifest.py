"""The canonical development corpus, written down.

Phase 1AC. The corpus used to be whatever had been ingested by hand, in
whatever order, on whatever machine -- which meant nobody could rebuild it
and nobody could say what "the corpus" was without querying a database.

This module is the answer to "which works is Noema supposed to hold?". It is
a list of source coordinates, nothing more: an AniList id or a Gutenberg
ebook id, plus the few facts the literature adapter needs that the plain-text
file does not carry. Everything else -- titles, synopses, creators, genres,
covers, structure -- comes from the source at ingestion time, as it always
has. Nothing here is a substitute for a source, and nothing here is invented.

`scripts.seed_corpus` reads this and ingests whatever is missing, so the
manifest and the database converge rather than drift. The entries that were
already ingested before this phase are listed too, so the file describes the
whole corpus rather than only the additions.

Choosing what is in it

The selection is deliberately varied: different eras, forms, tones and
traditions, because a retrieval and concept system tested against sixteen
similar works learns nothing about itself. It is not a ranking, a
recommendation, or a claim about quality.

Literature is limited to Project Gutenberg, which is the only literature
source Noema has and the only one whose full text it may legitimately hold.
Very long works (War and Peace, Ulysses) are deliberately left out: they
would dominate the content-unit count without adding proportionate variety.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LiteratureEntry:
    """One Project Gutenberg text.

    `title` and `author` are passed to the plain-text adapter because a
    Gutenberg .txt carries no reliable structured header; everything else
    about the work is read out of the file or the catalogue record.
    """

    ebook_id: int
    title: str
    author: str
    note: str = ""

    @property
    def source_ref(self) -> str:
        return str(self.ebook_id)

    @property
    def text_url(self) -> str:
        return f"https://www.gutenberg.org/ebooks/{self.ebook_id}.txt.utf-8"

    @property
    def source_url(self) -> str:
        return f"https://www.gutenberg.org/ebooks/{self.ebook_id}"


@dataclass(frozen=True)
class AniListEntry:
    """One AniList work, by id. The id is the whole record."""

    anilist_id: int
    # For humans reading the manifest only. The ingested title comes from
    # AniList, and may differ from this once the English title is chosen.
    note: str = ""


# --- literature -----------------------------------------------------------
#
# Gothic, satire, realism, sea story, detective fiction, children's writing,
# drama, modernist short stories and early science fiction. The first four
# were the original corpus.

LITERATURE: tuple[LiteratureEntry, ...] = (
    LiteratureEntry(11, "Alice's Adventures in Wonderland", "Lewis Carroll"),
    LiteratureEntry(84, "Frankenstein; Or, The Modern Prometheus", "Mary Wollstonecraft Shelley"),
    LiteratureEntry(5200, "Metamorphosis", "Franz Kafka"),
    LiteratureEntry(1661, "The Adventures of Sherlock Holmes", "Arthur Conan Doyle"),
    LiteratureEntry(1342, "Pride and Prejudice", "Jane Austen"),
    LiteratureEntry(345, "Dracula", "Bram Stoker"),
    LiteratureEntry(174, "The Picture of Dorian Gray", "Oscar Wilde"),
    LiteratureEntry(219, "Heart of Darkness", "Joseph Conrad"),
    LiteratureEntry(35, "The Time Machine", "H. G. Wells"),
    LiteratureEntry(36, "The War of the Worlds", "H. G. Wells"),
    LiteratureEntry(43, "The Strange Case of Dr. Jekyll and Mr. Hyde", "Robert Louis Stevenson"),
    LiteratureEntry(46, "A Christmas Carol in Prose; Being a Ghost Story of Christmas", "Charles Dickens"),
    LiteratureEntry(76, "Adventures of Huckleberry Finn", "Mark Twain"),
    LiteratureEntry(120, "Treasure Island", "Robert Louis Stevenson"),
    LiteratureEntry(768, "Wuthering Heights", "Emily Bronte"),
    LiteratureEntry(1260, "Jane Eyre: An Autobiography", "Charlotte Bronte"),
    LiteratureEntry(1400, "Great Expectations", "Charles Dickens"),
    LiteratureEntry(2542, "A Doll's House", "Henrik Ibsen"),
    LiteratureEntry(2814, "Dubliners", "James Joyce"),
    LiteratureEntry(16, "Peter Pan", "J. M. Barrie"),
)

# --- anime ----------------------------------------------------------------
#
# Television and film, 1997 to 2019; action, quiet episodic drama, political
# fiction, sports, slice of life and fantasy.

ANIME: tuple[AniListEntry, ...] = (
    AniListEntry(1, "Cowboy Bebop"),
    AniListEntry(5, "Cowboy Bebop: the movie"),
    AniListEntry(17205, "Cowboy Bebop: Ein's Summer Vacation"),
    AniListEntry(1535, "Death Note"),
    AniListEntry(5114, "Fullmetal Alchemist: Brotherhood"),
    AniListEntry(19, "Monster"),
    AniListEntry(339, "Serial Experiments Lain"),
    AniListEntry(30, "Neon Genesis Evangelion"),
    AniListEntry(9253, "Steins;Gate"),
    AniListEntry(16498, "Attack on Titan"),
    AniListEntry(11061, "Hunter x Hunter (2011)"),
    AniListEntry(1575, "Code Geass"),
    AniListEntry(457, "Mushishi"),
    AniListEntry(199, "Spirited Away (film)"),
    AniListEntry(164, "Princess Mononoke (film)"),
    AniListEntry(2001, "Gurren Lagann"),
    AniListEntry(4181, "Clannad: After Story"),
    AniListEntry(918, "Gintama"),
    AniListEntry(101922, "Demon Slayer"),
    AniListEntry(20607, "Ping Pong the Animation"),
    AniListEntry(21827, "Violet Evergarden"),
)

# --- manga and manhwa -----------------------------------------------------
#
# AniList types both as MANGA and separates them by country of origin, which
# is why they share one domain. Japanese and Korean works both, so the
# domain is exercised on both traditions.

MANHWA: tuple[AniListEntry, ...] = (
    AniListEntry(98416, "Dr. STONE"),
    AniListEntry(30025, "Fullmetal Alchemist"),
    AniListEntry(105398, "Solo Leveling (KR)"),
    AniListEntry(30642, "Vinland Saga"),
    AniListEntry(30002, "Berserk"),
    AniListEntry(30656, "Vagabond"),
    AniListEntry(30003, "20th Century Boys"),
    AniListEntry(34632, "Goodnight Punpun"),
    AniListEntry(105778, "Chainsaw Man"),
    AniListEntry(63327, "Tokyo Ghoul"),
    AniListEntry(30028, "Nana"),
    AniListEntry(30102, "Fruits Basket"),
    AniListEntry(30051, "Slam Dunk"),
    AniListEntry(30436, "Uzumaki"),
    AniListEntry(30745, "Pluto"),
    AniListEntry(30104, "Yotsuba&!"),
    AniListEntry(30149, "BLAME!"),
    AniListEntry(85143, "Tower of God (KR)"),
    AniListEntry(85141, "The God of High School (KR)"),
    AniListEntry(59983, "Noblesse (KR)"),
    AniListEntry(100954, "Sweet Home (KR)"),
    AniListEntry(86964, "Bastard (KR)"),
    AniListEntry(86848, "Lookism (KR)"),
)


@dataclass(frozen=True)
class DomainPlan:
    slug: str
    source: str
    minimum: int
    literature: tuple[LiteratureEntry, ...] = field(default_factory=tuple)
    anilist: tuple[AniListEntry, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        return len(self.literature) + len(self.anilist)


# The floor each domain is expected to meet. A manifest that no longer
# reaches it is a mistake in the manifest, and `validate_corpus` says so.
MINIMUM_PER_DOMAIN = 20

PLAN: tuple[DomainPlan, ...] = (
    DomainPlan("literature", "gutenberg", MINIMUM_PER_DOMAIN, literature=LITERATURE),
    DomainPlan("anime", "anilist", MINIMUM_PER_DOMAIN, anilist=ANIME),
    DomainPlan("manhwa", "anilist", MINIMUM_PER_DOMAIN, anilist=MANHWA),
)


def source_refs(slug: str) -> set[str]:
    """Every source reference the manifest expects for one domain."""
    for plan in PLAN:
        if plan.slug != slug:
            continue
        return {entry.source_ref for entry in plan.literature} | {
            str(entry.anilist_id) for entry in plan.anilist
        }
    raise KeyError(slug)


def total_works() -> int:
    return sum(plan.size for plan in PLAN)


__all__ = [
    "ANIME",
    "AniListEntry",
    "DomainPlan",
    "LITERATURE",
    "LiteratureEntry",
    "MANHWA",
    "MINIMUM_PER_DOMAIN",
    "PLAN",
    "source_refs",
    "total_works",
]
