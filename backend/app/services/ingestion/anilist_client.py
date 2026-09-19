"""AniList GraphQL client.

The only module that knows AniList exists over the network. The adapter
(`anime.py`) takes a plain dict, so every parsing test runs against fixtures
with no network involved.
"""

import time

import httpx

ANILIST_API_URL = "https://graphql.anilist.co"

# Everything the anime adapter maps. Requested in one query per work: AniList
# rate-limits per request, not per field.
MEDIA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    siteUrl
    title { romaji english native }
    description(asHtml: false)
    format
    status
    episodes
    duration
    season
    seasonYear
    startDate { year month day }
    endDate { year month day }
    countryOfOrigin
    source
    genres
    averageScore
    popularity
    tags { name rank category isGeneralSpoiler }
    studios { edges { isMain node { id name siteUrl } } }
    staff { edges { role node { id siteUrl name { full native } } } }
    characters(sort: [ROLE, RELEVANCE]) {
      edges { role node { id siteUrl image { large } name { full native } description(asHtml: false) } }
    }
    relations { edges { relationType node { id type format title { romaji english } } } }
    streamingEpisodes { title site url }
  }
}
"""


# The manga/manhwa equivalent. AniList models both under type: MANGA and
# distinguishes them by countryOfOrigin (JP = manga, KR = manhwa), so one
# query serves both. Chapters and volumes replace episodes; there is no
# streamingEpisodes equivalent, and no chapter text of any kind.
MANGA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: MANGA) {
    id
    idMal
    siteUrl
    title { romaji english native }
    description(asHtml: false)
    format
    status
    chapters
    volumes
    startDate { year month day }
    endDate { year month day }
    countryOfOrigin
    source
    genres
    averageScore
    popularity
    tags { name rank category isGeneralSpoiler }
    staff { edges { role node { id siteUrl name { full native } } } }
    characters(sort: [ROLE, RELEVANCE]) {
      edges { role node { id siteUrl name { full native } description(asHtml: false) } }
    }
    relations { edges { relationType node { id type format title { romaji english } } } }
  }
}
"""


class AniListError(RuntimeError):
    """AniList could not be reached or returned an error."""


class AniListNotFoundError(AniListError):
    """No anime exists with the requested id."""


class AniListClient:
    def __init__(self, timeout: float = 30.0, max_retries: int = 2) -> None:
        self.timeout = timeout
        self.max_retries = max_retries

    def fetch_manga(self, anilist_id: int) -> dict:
        """Return the raw AniList `Media` payload for one manga or manhwa."""
        return self._fetch(anilist_id, MANGA_QUERY)

    def fetch_media(self, anilist_id: int) -> dict:
        """Return the raw AniList `Media` payload for one anime."""
        return self._fetch(anilist_id, MEDIA_QUERY)

    def _fetch(self, anilist_id: int, query: str) -> dict:
        payload = {"query": query, "variables": {"id": anilist_id}}

        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(ANILIST_API_URL, json=payload, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise AniListError(f"could not reach AniList: {exc}") from exc

            # AniList rate-limits aggressively; it tells us how long to wait.
            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(float(response.headers.get("Retry-After", 5)))
                continue

            if response.status_code == 404:
                raise AniListNotFoundError(f"no anime with AniList id {anilist_id}")
            if response.status_code >= 400:
                raise AniListError(
                    f"AniList returned HTTP {response.status_code} for id {anilist_id}"
                )

            body = response.json()
            if body.get("errors"):
                message = "; ".join(e.get("message", "?") for e in body["errors"])
                if "not found" in message.lower():
                    raise AniListNotFoundError(f"no anime with AniList id {anilist_id}")
                raise AniListError(f"AniList error for id {anilist_id}: {message}")

            media = (body.get("data") or {}).get("Media")
            if media is None:
                raise AniListNotFoundError(f"no anime with AniList id {anilist_id}")
            return media

        raise AniListError(f"AniList rate-limited the request for id {anilist_id}")
