import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue

from app.core.config import get_settings
from worker.jobs import ping


def test_ping_job_returns_pong() -> None:
    result = ping()

    assert result["message"] == "pong"
    assert "executed_at" in result


@pytest.fixture(scope="session")
def redis_available() -> bool:
    conn = Redis.from_url(get_settings().redis_url)
    try:
        return conn.ping()
    except RedisConnectionError:
        return False


def test_ping_job_executes_via_rq_worker(redis_available: bool) -> None:
    """End-to-end: enqueue `ping` on the real default queue and run it.

    Uses the same Windows-compatible worker class as `worker/run.py`
    (`Worker`'s default relies on `os.fork`/`SIGALRM`, neither available on
    Windows) so this test exercises the same code path the app actually runs.
    """
    if not redis_available:
        pytest.skip("requires a live Redis instance")

    import os

    from rq import Worker
    from rq.worker import SimpleWorker

    conn = Redis.from_url(get_settings().redis_url)
    queue = Queue("default", connection=conn)

    job = queue.enqueue(ping)

    if os.name == "nt":
        from rq.timeouts import TimerDeathPenalty

        class WindowsWorker(SimpleWorker):
            death_penalty_class = TimerDeathPenalty

        worker_cls = WindowsWorker
    else:
        worker_cls = Worker

    worker_cls([queue], connection=conn).work(burst=True)

    job.refresh()
    assert job.get_status() == "finished"
    assert job.result["message"] == "pong"
