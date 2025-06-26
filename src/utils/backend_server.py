from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackendServer:
    url: str
    healthy: bool = True
    last_check_time: float = 0.0
    active_connections: int = 0
    total_requests: int = 0  # Need to memory bound - depends on how logging metrics will work
    failed_requests: int = 0
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def __post_init__(self):
        self.url = self.url.rstrip("/")

    @property
    def avg_response_time(self) -> float:
        """Calculate average response time from last 100 requests"""
        if not self.response_times:
            return 0.0
        return round(sum(self.response_times) / len(self.response_times), 4)

    @property
    def success_rate(self) -> float:
        """Calculate success rate for backend server"""
        return round((self.total_requests - self.failed_requests) / max(self.total_requests, 1) * 100, 2)

    @property
    def status(self) -> dict[str, Any]:
        return {
            "url": str(self.url),
            "healthy": bool(self.healthy),
            "active_connections": int(self.active_connections),
            "total_requests": int(self.total_requests),
            "failed_requests": int(self.failed_requests),
            "success_rate": float(self.success_rate),
            "avg_response_time": float(self.avg_response_time),
            "last_check_time": float(self.last_check_time) if self.last_check_time > 0 else None,
        }

    def add_response_time(self, time: float):
        """Add response time and keep only last 100"""
        self.response_times.append(time)

    def __str__(self):
        return f"{self.url}, healthy={self.healthy}, active={self.active_connections})"
