"""Compare a candidate embedding model against the production baseline.

    python -m scripts.run_model_experiment --embed     # generate candidate vectors
    python -m scripts.run_model_experiment             # run the comparison

Holds everything fixed except the model: same corpus, same `content_unit`
representation, same six queries (verbatim from Phase 1D/1E), same top-5,
same literature/primary filter, same cosine metric.

Writes only to `experiment_embeddings`. The production baseline and the
served default are untouched.
"""

import argparse
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.embedding.encoder import get_encoder
from app.services.embedding.model_experiment import candidate_search, embed_with_candidate
from app.services.embedding.search import build_search_query
from app.services.embedding.service import OWNER_TYPE_CONTENT_UNIT

CANDIDATE_MODEL = "sentence-transformers/all-mpnet-base-v2"
CANDIDATE_DIMENSION = 768

# Verbatim. Not reworded between phases -- that is the point.
QUERIES = [
    "isolation and being utterly alone",
    "a chase across a city to catch a criminal",
    "transformation, growing or shrinking",
    "confusion about who you really are",
    "a strange trial with absurd rules",
    "grief over someone who is gone",
]
TOP_K = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=CANDIDATE_MODEL)
    parser.add_argument("--dimension", type=int, default=CANDIDATE_DIMENSION)
    parser.add_argument("--embed", action="store_true", help="generate candidate vectors first")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--batch-size", type=int, default=16)
    return parser.parse_args(argv)


def baseline_hits(session, encoder, query, top_k):
    """Production path: the 384-d model over `embeddings`."""
    prepared_vector = encoder.encode([query])[0]
    statement = build_search_query(
        prepared_vector,
        model_name=encoder.model_name,
        top_k=top_k,
        domain_slug="literature",
        text_tier="primary",
    )
    return [
        (round(1.0 - float(distance), 6), unit, container)
        for unit, _work, container, _domain, _ts, distance in session.execute(statement).all()
    ]


def main() -> int:
    args = parse_args()
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    candidate = get_encoder(model_name=args.model, dimension=args.dimension)
    baseline = get_encoder()  # production default, 384-d

    try:
        if args.embed:
            started = time.perf_counter()
            with factory() as session:
                report = embed_with_candidate(
                    session, candidate, force=args.force, batch_size=args.batch_size
                )
                session.commit()
            elapsed = time.perf_counter() - started
            print(f"candidate  : {report.model_name} ({report.model_revision})")
            print(f"dimension  : {report.dimension}")
            print(f"eligible   : {report.eligible}")
            print(f"generated  : {report.generated}")
            print(f"regenerated: {report.regenerated}")
            print(f"skipped    : {report.skipped_current}")
            print(f"failed     : {len(report.failed)}")
            for failure in report.failed[:5]:
                print(f"    {failure}")
            print(f"elapsed    : {elapsed:.1f}s")
            print()
            return 1 if report.failed else 0

        # Warm both models so timings measure retrieval, not loading.
        baseline.encode(["warmup"])
        candidate.encode(["warmup"])

        with factory() as session:
            for query in QUERIES:
                print("=" * 104)
                print(f'QUERY: "{query}"   (literature / primary, top-{TOP_K})')
                print("=" * 104)

                t = time.perf_counter()
                base = baseline_hits(session, baseline, query, TOP_K)
                base_ms = (time.perf_counter() - t) * 1000

                t = time.perf_counter()
                cand = candidate_search(
                    session,
                    candidate,
                    query=query,
                    top_k=TOP_K,
                    domain_slug="literature",
                    text_tier="primary",
                )
                cand_ms = (time.perf_counter() - t) * 1000

                print(f"  BASELINE  MiniLM-L6-v2 (384d)   {base_ms:.1f}ms")
                for rank, (similarity, unit, container) in enumerate(base, start=1):
                    where = f"{container.title or container.container_type} {container.sequence_number}"
                    excerpt = " ".join((unit.text_content or "").split())[:150]
                    print(f"     {rank}. {similarity:.4f}  {where}")
                    print(f"        {excerpt}")

                print(f"  CANDIDATE mpnet-base-v2 (768d)  {cand_ms:.1f}ms")
                for rank, hit in enumerate(cand, start=1):
                    where = (
                        f"{hit.container_title or hit.container_type} "
                        f"{hit.container_sequence_number}"
                    )
                    print(f"     {rank}. {hit.similarity:.4f}  {where}")
                    print(f"        {hit.text_excerpt[:150]}")
                print()

            print("=" * 104)
            print("pgvector retrieval cost (no model inference)")
            print("=" * 104)
            for label, table, where in (
                ("baseline 384d", "embeddings", "owner_type='content_unit'"),
                (
                    "candidate 768d",
                    "experiment_embeddings",
                    f"owner_type='{OWNER_TYPE_CONTENT_UNIT}' "
                    f"AND model_name='{args.model}'",
                ),
            ):
                probe = session.execute(
                    text(f"SELECT vector FROM {table} WHERE {where} LIMIT 1")
                ).scalar_one()
                timings = []
                for _ in range(20):
                    start = time.perf_counter()
                    session.execute(
                        text(
                            f"SELECT id FROM {table} WHERE {where} "
                            f"ORDER BY vector <=> :v LIMIT {TOP_K}"
                        ),
                        {"v": str(probe)},
                    ).all()
                    timings.append((time.perf_counter() - start) * 1000)
                timings.sort()
                print(f"  {label:<16} median={timings[10]:.2f}ms  p95={timings[18]:.2f}ms")
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
