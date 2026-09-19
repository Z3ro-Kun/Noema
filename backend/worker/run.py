"""Entrypoint for an RQ worker process: `python -m worker.run`.

RQ's default `Worker` forks a child process per job (`os.fork`) and enforces
job timeouts via `SIGALRM` -- neither exists on Windows. On Windows we use
`SimpleWorker` (runs jobs in-process instead of forking) with
`TimerDeathPenalty` (a thread-based timeout instead of a signal-based one).
This trades away per-job crash isolation and signal-precise timeouts, which
matter more for the containerized deployment (Linux, where the normal
`Worker` is used) than for local dev.
"""

import os

from rq import Worker
from rq.worker import SimpleWorker

from worker.queue import default_queue, redis_conn

if __name__ == "__main__":
    if os.name == "nt":
        from rq.timeouts import TimerDeathPenalty

        class WindowsWorker(SimpleWorker):
            death_penalty_class = TimerDeathPenalty

        worker_cls = WindowsWorker
    else:
        worker_cls = Worker

    worker = worker_cls([default_queue], connection=redis_conn)
    worker.work()
