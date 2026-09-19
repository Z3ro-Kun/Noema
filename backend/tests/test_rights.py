"""Rights assessment tests. The central rule: unknown is never permitted."""

from app.services.ingestion.rights import assess_mediawiki_rights, unknown_rights

CC_BY_SA_4 = {
    "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
    "text": "Creative Commons Attribution-Share Alike 4.0",
}


def assess(rightsinfo, revision="987"):
    return assess_mediawiki_rights(
        rightsinfo,
        page_title="List of Test Series episodes",
        page_url="https://en.wikipedia.org/wiki/List_of_Test_Series_episodes",
        revision_ref=revision,
    )


def test_declared_cc_by_sa_permits_storage() -> None:
    rights = assess(CC_BY_SA_4)

    assert rights.licence == "CC-BY-SA-4.0"
    assert rights.permits_storage is True
    assert rights.requires_attribution is True
    assert rights.share_alike is True
    assert rights.licence_url == CC_BY_SA_4["url"]


def test_licence_version_comes_from_the_declaration() -> None:
    older = {"url": "https://creativecommons.org/licenses/by-sa/3.0/", "text": "CC BY-SA 3.0"}

    assert assess(older).licence == "CC-BY-SA-3.0"


def test_attribution_text_is_renderable_and_identifies_the_revision() -> None:
    attribution = assess(CC_BY_SA_4).attribution_text

    assert "List of Test Series episodes" in attribution
    assert "Wikipedia contributors" in attribution
    assert "revision 987" in attribution
    assert "https://en.wikipedia.org/wiki/List_of_Test_Series_episodes" in attribution


def test_missing_revision_still_produces_attribution() -> None:
    attribution = assess(CC_BY_SA_4, revision=None).attribution_text

    assert "revision" not in attribution
    assert "List of Test Series episodes" in attribution


def test_unrecognised_licence_refuses_storage() -> None:
    rights = assess({"url": "https://example.invalid/licence", "text": "All rights reserved"})

    assert rights.permits_storage is False
    assert rights.licence == "unknown"
    assert "unrecognised licence" in rights.basis


def test_absent_rightsinfo_refuses_storage() -> None:
    for empty in ({}, None, {"url": "", "text": ""}):
        rights = assess(empty)
        assert rights.permits_storage is False
        assert rights.licence == "unknown"


def test_non_share_alike_cc_licence_is_not_silently_accepted() -> None:
    """Only BY-SA is recognised here; anything else must be assessed explicitly."""
    rights = assess({"url": "https://creativecommons.org/licenses/by/4.0/", "text": "CC BY 4.0"})

    assert rights.permits_storage is False


def test_unknown_rights_helper_defaults_to_refusal() -> None:
    rights = unknown_rights("no idea")

    assert rights.permits_storage is False
    assert rights.is_known is False
    # Attribution is assumed required when unknown -- the cautious direction.
    assert rights.requires_attribution is True


def test_every_assessment_records_its_basis() -> None:
    assert assess(CC_BY_SA_4).basis
    assert assess({}).basis
