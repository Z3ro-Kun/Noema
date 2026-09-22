"""What a load balancer is told, and deliberately nothing more.

Three words and a version. No connection strings, no configuration, no error
text and no stack trace: this endpoint is reachable by anyone who can reach
the API, so everything it says has to be safe to say to a stranger.
"""

from typing import Literal

from pydantic import BaseModel

ServiceStatus = Literal["connected", "unavailable"]


class HealthResponse(BaseModel):
    # "ok" only when every backing service answered. A deployment platform
    # reads this one field; the two below say which of them is at fault.
    status: Literal["ok", "degraded"]
    database: ServiceStatus
    # Redis backs the embedding worker queue. The API serves reads without it,
    # so a Redis outage is "degraded" rather than "down" -- and saying which
    # is the point of reporting them apart.
    redis: ServiceStatus
    version: str
