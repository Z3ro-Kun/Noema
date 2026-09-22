"""MediaWiki client tests, driven by a mock transport. No network."""

import json

import httpx
import pytest

from app.services.ingestion.wikipedia_client import (
    USER_AGENT,
    PageNotFoundError,
    WikipediaClient,
    WikipediaError,
)


def make_client(handler) -> WikipediaClient:
    return WikipediaClient(transport=httpx.MockTransport(handler), max_retries=1)


def page_response(
    *, title="List of Test Series episodes", pageid=42, revid=987, content="wikitext here",
    redirects=None,
):
    body = {
        "query": {
            "pages": [
                {
                    "pageid": pageid,
                    "title": title,
                    "revisions": [
                        {
                            "revid": revid,
                            "timestamp": "2026-08-11T19:29:15Z",
                            "slots": {"main": {"content": content}},
                        }
                    ],
                }
            ]
        }
    }
    if redirects:
        body["query"]["redirects"] = redirects
    return httpx.Response(200, json=body)


def test_fetch_page_returns_wikitext_and_revision() -> None:
    client = make_client(lambda request: page_response())

    page = client.fetch_page("List of Test Series episodes")

    assert page.title == "List of Test Series episodes"
    assert page.pageid == 42
    assert page.revid == "987"
    assert page.wikitext == "wikitext here"
    assert page.url == "https://en.wikipedia.org/wiki/List_of_Test_Series_episodes"


def test_request_identifies_the_client_per_api_etiquette() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        seen["params"] = dict(request.url.params)
        return page_response()

    make_client(handler).fetch_page("X")

    assert seen["ua"] == USER_AGENT
    assert "Noema" in seen["ua"]
    # Redirect following is requested server-side rather than reimplemented.
    assert seen["params"]["redirects"] == "1"
    assert seen["params"]["formatversion"] == "2"


def test_redirects_are_reported() -> None:
    client = make_client(
        lambda request: page_response(
            title="List of Test Series episodes",
            redirects=[{"from": "Test Series episodes", "to": "List of Test Series episodes"}],
        )
    )

    page = client.fetch_page("Test Series episodes")

    assert page.redirected_from == "Test Series episodes"
    assert page.title == "List of Test Series episodes"


def test_missing_page_raises_page_not_found() -> None:
    body = {"query": {"pages": [{"title": "Nope", "missing": True}]}}
    client = make_client(lambda request: httpx.Response(200, json=body))

    with pytest.raises(PageNotFoundError):
        client.fetch_page("Nope")


def test_resolve_page_asks_once_which_candidates_exist() -> None:
    """Candidate lists are mostly misses, so existence is one request.

    Only the survivors are fetched in full, and the caller's order still
    decides which of them wins.
    """
    attempted = []

    def handler(request: httpx.Request) -> httpx.Response:
        title = request.url.params["titles"]
        attempted.append(title)
        if "|" in title:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {"title": "List of Test Series episodes", "missing": True},
                            {"title": "Test Series", "pageid": 42},
                        ]
                    }
                },
            )
        return page_response(title=title)

    page = make_client(handler).resolve_page(["List of Test Series episodes", "Test Series"])

    assert attempted == ["List of Test Series episodes|Test Series", "Test Series"]
    assert page.title == "Test Series"


def test_resolve_page_keeps_the_callers_order_among_survivors() -> None:
    """Two candidates exist; the earlier one is still the answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        title = request.url.params["titles"]
        if "|" in title:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {"title": "Test Series (webtoon)", "pageid": 7},
                            {"title": "Test Series", "pageid": 42},
                        ]
                    }
                },
            )
        return page_response(title=title)

    page = make_client(handler).resolve_page(["Test Series (webtoon)", "Test Series"])

    assert page.title == "Test Series (webtoon)"


def test_resolve_page_falls_back_to_trying_each_when_the_probe_fails() -> None:
    """Slower and always correct, rather than giving up on a probe error."""
    attempted = []

    def handler(request: httpx.Request) -> httpx.Response:
        title = request.url.params["titles"]
        attempted.append(title)
        if "|" in title:
            return httpx.Response(500)
        if title == "List of Test Series episodes":
            return httpx.Response(
                200, json={"query": {"pages": [{"title": title, "missing": True}]}}
            )
        return page_response(title=title)

    page = make_client(handler).resolve_page(["List of Test Series episodes", "Test Series"])

    assert attempted[1:] == ["List of Test Series episodes", "Test Series"]
    assert page.title == "Test Series"


def test_existing_titles_maps_the_answer_back_to_what_was_asked() -> None:
    """The wiki normalizes titles; the caller gets its own spellings back."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "query": {
                    "normalized": [{"from": "test_series", "to": "Test series"}],
                    "pages": [
                        {"title": "Test series", "pageid": 42},
                        {"title": "Nothing Here", "missing": True},
                    ],
                }
            },
        )

    found = make_client(handler).existing_titles(["test_series", "Nothing Here"])

    assert found == {"test_series"}


def test_resolve_page_raises_when_no_candidate_exists() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200, json={"query": {"pages": [{"title": "x", "missing": True}]}}
        )
    )

    with pytest.raises(PageNotFoundError, match="none of these pages exist"):
        client.resolve_page(["A", "B"])


def test_api_error_is_surfaced() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"error": {"code": "badvalue"}})
    )

    with pytest.raises(WikipediaError, match="badvalue"):
        client.fetch_page("X")


def test_http_error_is_surfaced() -> None:
    client = make_client(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(WikipediaError, match="HTTP 500"):
        client.fetch_page("X")


def test_transient_status_is_retried_once_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return page_response()

    page = make_client(handler).fetch_page("X")

    assert calls["n"] == 2
    assert page.revid == "987"


def test_retries_are_bounded() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "0"})

    with pytest.raises(WikipediaError):
        make_client(handler).fetch_page("X")

    # max_retries=1 -> at most two attempts, then give up.
    assert calls["n"] == 2


def test_page_without_revision_content_is_an_error() -> None:
    body = {"query": {"pages": [{"pageid": 1, "title": "X", "revisions": [{"revid": 5, "slots": {}}]}]}}
    client = make_client(lambda request: httpx.Response(200, json=body))

    with pytest.raises(WikipediaError, match="no wikitext"):
        client.fetch_page("X")


def test_fetch_rightsinfo_returns_the_declared_licence() -> None:
    rights = {
        "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
        "text": "Creative Commons Attribution-Share Alike 4.0",
    }
    client = make_client(
        lambda request: httpx.Response(200, json={"query": {"rightsinfo": rights}})
    )

    assert client.fetch_rightsinfo() == rights


def test_legacy_slot_content_key_is_tolerated() -> None:
    body = json.loads(page_response().content)
    body["query"]["pages"][0]["revisions"][0]["slots"]["main"] = {"*": "legacy shape"}
    client = make_client(lambda request: httpx.Response(200, json=body))

    assert client.fetch_page("X").wikitext == "legacy shape"
