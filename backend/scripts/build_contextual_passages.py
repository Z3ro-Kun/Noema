"""Build and embed contextual passages (Phase 1E experiment).

    python -m scripts.build_contextual_passages                 # literature
    python -m scripts.build_contextual_passages --enqueue       # via RQ
    python -m scripts.build_contextual_passages --force

Baseline ContentUnit embeddings are left untouched so the two
representations can be compared.
"""

import argparse
import statistics
import time

from worker.embedding_jobs import build_and_embed_contextual_passages


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="literature")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--enqueue", action="store_true", help="run on the RQ worker")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    kwargs = {
        "domain_slug": args.domain,
        "force": args.force,
        "batch_size": args.batch_size,
    }

    if args.enqueue:
        from worker.queue import default_queue

        job = default_queue.enqueue(
            build_and_embed_contextual_passages, job_timeout=3600, **kwargs
        )
        print(f"enqueued contextual passage job {job.id}")
        return 0

    started = time.perf_counter()
    result = build_and_embed_contextual_passages(**kwargs)
    elapsed = time.perf_counter() - started

    build, embed = result["build"], result["embed"]
    counts, chars = build["unit_counts"], build["char_lengths"]

    print(f"grouping    : {result['grouping_config']}")
    print(f"containers  : {build['containers_processed']}")
    print(
        f"passages    : {build['passages_created']} created | "
        f"{build['passages_unchanged']} unchanged | {build['passages_removed']} replaced"
    )
    if counts:
        chars_sorted = sorted(chars)
        print(
            f"units/passage: mean={statistics.mean(counts):.2f} "
            f"min={min(counts)} max={max(counts)}"
        )
        print(
            f"chars       : mean={statistics.mean(chars):.0f} "
            f"median={chars_sorted[len(chars_sorted) // 2]} max={max(chars)}"
        )
    print(f"model       : {embed['model_name']} ({embed['model_revision']})")
    print(
        f"embeddings  : {embed['generated']} generated | "
        f"{embed['regenerated_stale']} regenerated | {embed['skipped_current']} skipped"
    )
    print(f"failed      : {len(embed['failed'])}")
    for failure in embed["failed"][:5]:
        print(f"    {failure}")
    print(f"elapsed     : {elapsed:.1f}s")
    return 1 if embed["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
