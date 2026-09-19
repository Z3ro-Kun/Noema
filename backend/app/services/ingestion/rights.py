"""Turning a source's declared licence into a storage decision.

The rule this module exists to enforce: **unknown is not permitted**. If a
licence cannot be recognised from what the source itself declares, storage is
refused and the reason is recorded, rather than defaulting to yes because the
data happened to be reachable. API availability is not a grant of rights.

What this produces is an ingestion-time reading of source-declared licensing,
recorded so it can be re-checked later. It is not legal advice, and
publishing or redistributing derived data may need separate review.
"""

import re
from dataclasses import dataclass

# Matches the CC BY-SA deed URLs MediaWiki reports in its rightsinfo block,
# e.g. https://creativecommons.org/licenses/by-sa/4.0/deed.en
_CC_BY_SA_URL_RE = re.compile(
    r"creativecommons\.org/licenses/by-sa/(?P<version>\d+\.\d+)", re.IGNORECASE
)


@dataclass(frozen=True)
class RightsAssessment:
    licence: str
    licence_url: str | None
    attribution_text: str | None
    requires_attribution: bool
    share_alike: bool
    permits_storage: bool
    # Why we concluded the above, in plain words, so a reader can audit it.
    basis: str

    @property
    def is_known(self) -> bool:
        return self.licence != "unknown"


def unknown_rights(reason: str, licence_url: str | None = None) -> RightsAssessment:
    return RightsAssessment(
        licence="unknown",
        licence_url=licence_url,
        attribution_text=None,
        requires_attribution=True,
        share_alike=False,
        permits_storage=False,
        basis=reason,
    )


def assess_mediawiki_rights(
    rightsinfo: dict | None,
    page_title: str,
    page_url: str | None,
    revision_ref: str | None,
) -> RightsAssessment:
    """Assess rights from a wiki's own `meta=siteinfo&siprop=rightsinfo`.

    Read from the live wiki rather than hardcoded, so a licence change shows
    up as an unknown (and therefore a refusal to store) instead of silently
    continuing under a stale assumption.
    """
    declared_url = (rightsinfo or {}).get("url") or ""
    declared_text = (rightsinfo or {}).get("text") or ""

    if not declared_url and not declared_text:
        return unknown_rights("the wiki declared no rightsinfo; storage refused")

    match = _CC_BY_SA_URL_RE.search(declared_url)
    if match is None:
        return unknown_rights(
            f"unrecognised licence declared by source: {declared_text or declared_url!r}; "
            "storage refused",
            licence_url=declared_url or None,
        )

    version = match.group("version")
    licence = f"CC-BY-SA-{version}"

    attribution = f'"{page_title}", Wikipedia contributors'
    if revision_ref:
        attribution += f" (revision {revision_ref})"
    if page_url:
        attribution += f", {page_url}"
    attribution += f", licensed under {declared_text or licence}."

    return RightsAssessment(
        licence=licence,
        licence_url=declared_url,
        attribution_text=attribution,
        requires_attribution=True,
        # BY-SA: reusing this text in a derived published dataset can carry
        # the share-alike obligation onto that dataset.
        share_alike=True,
        permits_storage=True,
        basis=(
            f"source declared {declared_text or licence} at {declared_url}; "
            "CC BY-SA permits storage and redistribution with attribution"
        ),
    )
