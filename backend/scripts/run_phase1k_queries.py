"""Phase 1K retrieval check: six historical queries, plus manga sanity queries.

    python -m scripts.run_phase1k_queries

Two sets, reported separately and never merged:

  HISTORICAL   The same six queries, verbatim, since Phase 1D. They exist to
               show what adding a domain did to results that already had a
               baseline -- so they are never reworded, and never chosen to
               suit the new corpus.

  MANGA        New queries about the manga works just ingested. These say
               whether the new text is reachable at all. They are new, so
               they have no baseline and cannot be compared with the six.

No weighting, quotas, reranking or query expansion: this reports what the
existing retrieval path returns, including when that is unbalanced.
"""

import argparse
import asyncio
import time

from app.core.db import async_session_factory, engine
from app.services.embedding.encoder import get_encoder
from app.services.embedding.search import semantic_search

HISTORICAL_QUERIES = [
    "isolation and being utterly alone",
    "a chase across a city to catch a criminal",
    "transformation, growing or shrinking",
    "confusion about who you really are",
    "a strange trial with absurd rules",
    "grief over someone who is gone",
]

# Written against what the manga corpus is actually about (Norse raiding and
# revenge, alchemy and its price, rebuilding technology after a catastrophe),
# not against phrasings already known to retrieve well.
MANGA_QUERIES = [
    "a young warrior seeking revenge for his father",
    "rebuilding civilisation with science after a catastrophe",
    "two brothers pay a terrible price for forbidden alchemy",
    "a slave sold to work the fields",
    "a levelling system that makes the weakest hunter strong",
]

TOP_K = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    return parser.parse_args(argv)


async def run_set(session, encoder, label: str, queries: list[str], top_k: int) -> None:
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")

    for query in queries:
        started = time.perf_counter()
        hits = await semantic_search(session, encoder, query=query, top_k=top_k)
        elapsed_ms = (time.perf_counter() - started) * 1000

        domains = [hit.domain_slug for hit in hits]
        mix = ", ".join(f"{d}={domains.count(d)}" for d in sorted(set(domains)))
        print(f"\n{query!r}  [{elapsed_ms:.0f}ms | {mix}]")

        for rank, hit in enumerate(hits, start=1):
            where = hit.work_title
            if hit.container_sequence_number is not None:
                where += f" #{hit.container_sequence_number}"
            excerpt = " ".join((hit.text_excerpt or "").split())[:150]
            print(f"  {rank}. {hit.similarity:.4f}  [{hit.domain_slug}/{hit.text_tier}] {where}")
            print(f"     {excerpt}")


async def run(args: argparse.Namespace) -> int:
    encoder = get_encoder()
    try:
        async with async_session_factory() as session:
            await run_set(session, encoder, "HISTORICAL QUERIES (baseline since 1D)",
                          HISTORICAL_QUERIES, args.top_k)
            await run_set(session, encoder, "MANGA SANITY QUERIES (new, no baseline)",
                          MANGA_QUERIES, args.top_k)
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
