"""MediaWiki Action API client.

The only module here that touches the network. The narrative parser takes the
dataclasses this returns, so every parsing test runs offline against fixtures.

Uses the Action API rather than rendered HTML: wikitext gives us the episode
templates with their own episode numbers, which is exactly the structured
evidence the matcher needs and which parsing HTML would throw away.
"""

import time
from dataclasses import dataclass

import httpx

from app import __version__

DEFAULT_API_URL = "https://en.wikipedia.org/w/api.php"
DEFAULT_SITE_URL = "https://en.wikipedia.org/wiki/"

# Wikimedia's API etiquette asks for a descriptive User-Agent identifying the
# client so operators can get in touch about misbehaving traffic.
USER_AGENT = f"Noema/{__version__} (research project; contact via repository)"


class WikipediaError(RuntimeError):
    """Wikipedia could not be reached or returned an error."""


class PageNotFoundError(WikipediaError):
    """No page exists under the requested title."""


@dataclass(frozen=True)
class WikiPage:
    """A retrieved page revision, plus where it came from."""

    title: str
    pageid: int
    revid: str | None
    revision_timestamp: str | None
    wikitext: str
    url: str
    # True when the requested title differed from the one actually served.
    redirected_from: str | None = None


class WikipediaClient:
    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        site_url: str = DEFAULT_SITE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url
        self.site_url = site_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport

    def _get(self, params: dict) -> dict:
        # formatversion 2 returns pages as a list and drops the legacy
        # pageid-keyed dict, which is what the parsing below expects.
        params = {**params, "format": "json", "formatversion": "2"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    headers={"User-Agent": USER_AGENT},
                    transport=self._transport,
                ) as client:
                    response = client.get(self.api_url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise WikipediaError(f"could not reach Wikipedia: {exc}") from exc

            # 429/503 are the transient cases worth one bounded retry.
            if response.status_code in (429, 503) and attempt < self.max_retries:
                time.sleep(float(response.headers.get("Retry-After", 1.0 * (attempt + 1))))
                continue

            if response.status_code >= 400:
                raise WikipediaError(f"Wikipedia returned HTTP {response.status_code}")

            body = response.json()
            if "error" in body:
                raise WikipediaError(f"Wikipedia API error: {body['error'].get('code')}")
            return body

        raise WikipediaError(f"Wikipedia request failed: {last_error}")

    def page_url(self, title: str) -> str:
        return self.site_url + title.replace(" ", "_")

    def fetch_page(self, title: str) -> WikiPage:
        """Fetch one page's wikitext and revision, following redirects."""
        body = self._get(
            {
                "action": "query",
                "prop": "revisions",
                "titles": title,
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "rvlimit": 1,
                "redirects": 1,
            }
        )

        query = body.get("query") or {}
        pages = query.get("pages") or []
        if not pages:
            raise PageNotFoundError(f"no page found for {title!r}")

        page = pages[0]
        if "missing" in page or page.get("pageid") is None:
            raise PageNotFoundError(f"no page found for {title!r}")

        revisions = page.get("revisions") or []
        if not revisions:
            raise WikipediaError(f"page {page.get('title')!r} returned no revision content")

        revision = revisions[0]
        main_slot = (revision.get("slots") or {}).get("main") or {}
        # formatversion 2 uses "content"; older responses use "*".
        content = main_slot.get("content", main_slot.get("*"))
        if content is None:
            raise WikipediaError(f"page {page.get('title')!r} returned no wikitext")

        redirected_from = None
        for redirect in query.get("redirects") or []:
            if redirect.get("to") == page.get("title"):
                redirected_from = redirect.get("from")

        return WikiPage(
            title=page["title"],
            pageid=page["pageid"],
            revid=str(revision["revid"]) if revision.get("revid") is not None else None,
            revision_timestamp=revision.get("timestamp"),
            wikitext=content,
            url=self.page_url(page["title"]),
            redirected_from=redirected_from,
        )

    def fetch_rightsinfo(self) -> dict:
        """The wiki's own declared content licence."""
        body = self._get({"action": "query", "meta": "siteinfo", "siprop": "rightsinfo"})
        return (body.get("query") or {}).get("rightsinfo") or {}

    def existing_titles(self, titles: list[str]) -> set[str]:
        """Which of these pages exist, in one request.

        The Action API takes many titles at once and marks the absent ones
        `missing`, so asking about twenty candidates costs one round trip
        instead of twenty. It normalizes what it is given -- underscores to
        spaces, first letter capitalized -- so the answer is mapped back to
        the titles the caller actually passed.
        """
        body = self._get({"action": "query", "titles": "|".join(titles)})
        query = body.get("query") or {}

        # normalized: [{"from": "<as asked>", "to": "<as the wiki spells it>"}]
        back: dict[str, str] = {}
        for entry in query.get("normalized") or []:
            back[entry.get("to")] = entry.get("from")

        found: set[str] = set()
        for page in query.get("pages") or []:
            if "missing" in page or page.get("pageid") is None:
                continue
            title = page.get("title")
            found.add(back.get(title, title))
        return found

    def resolve_page(self, candidates: list[str]) -> WikiPage:
        """Return the first candidate title that exists.

        Anime episode coverage lives under several established shapes -- a
        dedicated "List of X episodes" article, a section of the main article,
        per-season sub-articles -- so callers pass the candidates worth trying
        for their corpus rather than this guessing a universal scheme.

        Candidate lists are mostly misses by design -- a series is filed under
        one of the shapes, not all of them -- so existence is established in a
        single request and only the survivors are fetched in full. The order
        is the caller's, unchanged: the first surviving candidate wins, exactly
        as when each was tried in turn. If that probe fails for any reason the
        candidates are simply tried one by one, which is slower and always
        correct.
        """
        unique = list(dict.fromkeys(title for title in candidates if title))
        if not unique:
            raise PageNotFoundError("no candidate titles were offered")

        ordered = unique
        if len(unique) > 1:
            try:
                existing = self.existing_titles(unique)
            except WikipediaError:
                existing = None
            if existing is not None:
                ordered = [title for title in unique if title in existing]

        attempted: list[str] = []
        for title in ordered:
            try:
                return self.fetch_page(title)
            except PageNotFoundError:
                attempted.append(title)
        raise PageNotFoundError(f"none of these pages exist: {unique}")
