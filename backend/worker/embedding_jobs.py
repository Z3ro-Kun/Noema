"""Background embedding jobs, run on the existing RQ worker.

Embedding is far too slow to sit inside a request, so the API enqueues this
instead. The job loads the model once per execution and works in batches --
never a model load per unit. Nothing here needs a GPU, and nothing here
introduces a second queue or task framework.

RQ workers are synchronous, which is why the embedding service is sync: this
is offline batch work with no I/O concurrency to exploit. Semantic search,
which does run inside a request, stays async.
"""

from dataclasses import asdict

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.embedding.encoder import get_encoder
from app.services.embedding.service import (
    eligible_content_units,
    embed_content_units,
    record_truncation,
)

_session_factory = None


def _get_session_factory() -> sessionmaker:
    """One engine per worker process, created on first use."""
    global _session_factory
    if _session_factory is None:
        engine = create_engine(get_settings().sync_database_url, pool_pre_ping=True)
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def build_and_embed_contextual_passages(
    domain_slug: str | None = "literature",
    force: bool = False,
    batch_size: int = 32,
) -> dict:
    """Rebuild contextual passages for a domain, then embed them.

    Grouping needs the model's tokenizer for its length bound, so the encoder
    is loaded once and used for both phases.
    """
    from app.services.embedding.contextual import build_passages_for_corpus, embed_passages
    from app.services.embedding.grouping import GroupingConfig

    factory = _get_session_factory()
    encoder = get_encoder()
    config = GroupingConfig()

    with factory() as session:
        build = build_passages_for_corpus(
            session, encoder.count_tokens, domain_slug=domain_slug, config=config
        )
        embed = embed_passages(
            session,
            encoder,
            grouping_config=config.key,
            domain_slug=domain_slug,
            force=force,
            batch_size=batch_size,
        )
        session.commit()

    return {"build": asdict(build), "embed": asdict(embed), "grouping_config": config.key}


def embed_corpus(
    domain_slug: str | None = None,
    text_tier: str | None = None,
    limit: int | None = None,
    force: bool = False,
    batch_size: int = 32,
    report_truncation: bool = False,
) -> dict:
    """Embed every eligible ContentUnit, skipping vectors already current."""
    factory = _get_session_factory()
    encoder = get_encoder()

    with factory() as session:
        units = eligible_content_units(
            session, domain_slug=domain_slug, text_tier=text_tier, limit=limit
        )
        report = embed_content_units(
            session, encoder, units=units, force=force, batch_size=batch_size
        )
        if report_truncation:
            record_truncation(report, encoder, units)
        session.commit()

    return asdict(report)
