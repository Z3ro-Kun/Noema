"""Embed the existing corpus.

    python -m scripts.embed_corpus                      # everything eligible
    python -m scripts.embed_corpus --domain literature  # one domain
    python -m scripts.embed_corpus --force              # regenerate regardless
    python -m scripts.embed_corpus --enqueue            # hand it to the RQ worker

Runs in-process by default so a first run is easy to watch; --enqueue uses
the same job the API would.
"""

import argparse
import time

from app.models import TEXT_TIERS
from worker.embedding_jobs import embed_corpus


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default=None, help="literature | anime | manhwa")
    parser.add_argument("--tier", default=None, choices=TEXT_TIERS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--force", action="store_true", help="regenerate even if vectors are current"
    )
    parser.add_argument(
        "--enqueue", action="store_true", help="run on the RQ worker instead of in-process"
    )
    parser.add_argument(
        "--report-truncation",
        action="store_true",
        help="also report units longer than the model's window (needs a second pass)",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    kwargs = {
        "domain_slug": args.domain,
        "text_tier": args.tier,
        "limit": args.limit,
        "force": args.force,
        "batch_size": args.batch_size,
        "report_truncation": args.report_truncation,
    }

    if args.enqueue:
        from worker.queue import default_queue

        job = default_queue.enqueue(embed_corpus, job_timeout=3600, **kwargs)
        print(f"enqueued embedding job {job.id} on the default queue")
        return 0

    started = time.perf_counter()
    report = embed_corpus(**kwargs)
    elapsed = time.perf_counter() - started

    print(f"model      : {report['model_name']} (revision {report['model_revision']})")
    print(f"dimension  : {report['dimension']}")
    print(f"eligible   : {report['eligible']}")
    print(f"generated  : {report['generated']}")
    print(f"regenerated: {report['regenerated_stale']} (stale)")
    print(f"skipped    : {report['skipped_current']} (already current)")
    print(f"failed     : {len(report['failed'])}")
    for failure in report["failed"][:5]:
        print(f"    {failure['content_unit_id']}: {failure['reason']}")
    if report["truncated"]:
        print(f"truncated  : {len(report['truncated'])} unit(s) exceeded the model window")
        for item in report["truncated"][:5]:
            print(f"    {item['content_unit_id']}: {item['tokens']} tokens > {item['limit']}")
    print(f"elapsed    : {elapsed:.1f}s")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
