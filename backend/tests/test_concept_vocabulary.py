"""The canonical concept vocabulary. Pure functions; no database, no network."""

import pytest

from app.services.concepts.vocabulary import (
    BY_SLUG,
    VOCABULARY,
    resolve_label,
    resolve_source_label,
    slugify,
)


# --- the vocabulary is internally consistent -----------------------------


def test_slugs_and_names_are_unique() -> None:
    """Two entries meaning one thing is the drift this module exists to stop."""
    slugs = [entry.slug for entry in VOCABULARY]
    names = [entry.name for entry in VOCABULARY]

    assert len(slugs) == len(set(slugs))
    assert len(names) == len(set(names))
    assert len(BY_SLUG) == len(VOCABULARY)


def test_every_entry_is_fully_specified() -> None:
    for entry in VOCABULARY:
        assert entry.slug == slugify(entry.slug)
        assert entry.name and entry.description
        assert entry.concept_type in {"theme", "motif", "genre"}


def test_no_alias_is_claimed_by_two_concepts() -> None:
    """An ambiguous alias would make mapping depend on definition order."""
    owners: dict[str, str] = {}
    for entry in VOCABULARY:
        for alias in entry.aliases:
            key = slugify(alias)
            assert key not in owners or owners[key] == entry.slug, (
                f"alias {alias!r} claimed by both {owners.get(key)} and {entry.slug}"
            )
            owners[key] = entry.slug


@pytest.mark.parametrize(
    "variant",
    ["Coming of Age", "coming-of-age", "COMING OF AGE", "  Coming  Of  Age  ", "Coming_of_Age"],
)
def test_spelling_variants_collapse_to_one_slug(variant: str) -> None:
    """The drift case from the spec: one idea must not become several rows."""
    assert slugify(variant) == "coming-of-age"
    assert resolve_label(variant).slug == "coming-of-age"


# --- mapping external labels ---------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Psychological", "psychological-depth"),
        ("Philosophy", "existential-questioning"),
        ("Anti-Hero", "moral-ambiguity"),
        ("Crime", "crime-and-investigation"),
        ("Sci-Fi", "science-fiction"),
    ],
)
def test_anilist_labels_map_to_concepts(label: str, expected: str) -> None:
    assert resolve_source_label(label).slug == expected


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Psychological fiction", "psychological-depth"),
        ("Horror tales", "horror"),
        ("Gothic fiction", "horror"),
        ("Science fiction", "science-fiction"),
        ("Fantasy fiction", "fantasy"),
        # Subdivisions are stripped to reach the main heading.
        ("Monsters -- Fiction", "the-supernatural"),
        ("Imaginary places -- Juvenile fiction", "fantasy"),
        # Language qualifiers after a comma are stripped too.
        ("Detective and mystery stories, English", "crime-and-investigation"),
        ("Private investigators -- England -- Fiction", "crime-and-investigation"),
    ],
)
def test_library_of_congress_headings_map_to_concepts(heading: str, expected: str) -> None:
    """LCSH is what lets literature participate without guessing at its prose."""
    assert resolve_source_label(heading).slug == expected


@pytest.mark.parametrize(
    "label",
    [
        # Demographic and advisory tags: not narrative features.
        "Shounen",
        "Male Protagonist",
        "Primarily Adult Cast",
        "Gore",
        "Nudity",
        "Trains",
        # LCSH headings naming a character or a readership, not a subject.
        "Alice (Fictitious character from Carroll) -- Juvenile fiction",
        "Children's stories",
        "Scientists -- Fiction",
    ],
)
def test_non_narrative_labels_are_left_unmapped(label: str) -> None:
    """Returning None is the correct outcome, not a failure to be patched."""
    assert resolve_source_label(label) is None


def test_an_unknown_label_never_invents_a_concept() -> None:
    assert resolve_source_label("Completely Made Up Tag") is None
    assert resolve_source_label("") is None


def test_a_concept_is_reachable_by_its_own_name_and_slug() -> None:
    for entry in VOCABULARY:
        assert resolve_label(entry.name).slug == entry.slug
        assert resolve_label(entry.slug).slug == entry.slug


def test_specific_headings_win_over_their_stripped_form() -> None:
    """The full heading is tried first, so a narrower mapping is not lost."""
    assert resolve_source_label("Science fiction").slug == "science-fiction"
    assert resolve_source_label("Detective and mystery stories").slug == "crime-and-investigation"
