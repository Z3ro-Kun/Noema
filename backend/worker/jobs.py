"""Example background jobs.

Real jobs (embedding generation, corpus ingestion, entity/concept
extraction) land here in later phases. `ping` exists only to verify the
worker can receive and execute a job end-to-end.
"""

from datetime import datetime, timezone


def ping() -> dict:
    return {"message": "pong", "executed_at": datetime.now(timezone.utc).isoformat()}
