"""Discovery: the browsable corpus as a product surface.

Phase 1Y. Two shapes, and one thing they both refuse to do.

`WorkListResponse` is a *page* of works. The corpus is seventeen works today
and would fit in one response, but an endpoint whose contract is "everything"
has to be redesigned the first time it isn't, and every client written against
it with it. So the page metadata exists from the start and the client never
learns the corpus is small.

`DiscoveryFacets` is what the filters would actually match. It exists because
Noema's metadata is honestly uneven: every work has a domain, most have
concepts, and only AniList-sourced works have genres. A client that hardcoded
a genre list would show literature readers a filter that can never return
anything. Counts let it hide what is empty instead, and nothing here invents a
value to make the three domains look symmetrical.

Both carry `ProductWork`/`WorkPresentation` and therefore inherit its
boundary: no content units, no passages, no embeddings, no adapter names, no
ingestion provenance. Discovery is a different question over the same safe
projection, not a new one.
"""

from pydantic import BaseModel, Field

from app.schemas.product import WorkPresentation


class FacetValue(BaseModel):
    """One value a filter can take, and how many works it matches.

    `value` is what a client sends back -- a domain slug, a concept slug, or
    a genre exactly as the source worded it. `label` is what it shows. They
    differ for slugs and are identical for genres, because renaming a
    source's own label would be editing what the source said.
    """

    value: str
    label: str
    count: int


class DiscoveryFacets(BaseModel):
    """What can be filtered on right now, with honest counts.

    Domains are listed even at zero: a domain is part of what Noema says it
    covers, and a filter vanishing because a corpus is thin would
    misrepresent the product rather than the data. Concepts and genres are
    listed only where something is behind them.
    """

    domains: list[FacetValue] = Field(default_factory=list)
    concepts: list[FacetValue] = Field(default_factory=list)
    # Empty for literature by nature, not by omission. See the module
    # docstring; the count is the honest signal, not a bug to paper over.
    genres: list[FacetValue] = Field(default_factory=list)


class WorkListResponse(BaseModel):
    """One page of the canonical corpus.

    `items` carry the caller's own `user_state` when they are signed in and
    `null` when they are not; `work` is byte-identical either way, which is
    the same guarantee `/works/{id}` gives.
    """

    items: list[WorkPresentation] = Field(default_factory=list)
    # Matching works in total, not works on this page -- so a client can say
    # "17 works" without asking for all of them.
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


__all__ = ["DiscoveryFacets", "FacetValue", "WorkListResponse"]
