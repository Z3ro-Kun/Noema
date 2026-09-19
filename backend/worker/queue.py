from redis import Redis
from rq import Queue

from app.core.config import get_settings

settings = get_settings()

redis_conn = Redis.from_url(settings.redis_url)

# A single default queue for now. As background work grows (embedding
# generation, corpus ingestion, ...) split into named queues here rather
# than introducing a heavier task framework.
default_queue = Queue("default", connection=redis_conn)
