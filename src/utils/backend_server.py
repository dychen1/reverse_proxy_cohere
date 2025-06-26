from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackendServer:
    """
    Represents a backend server in the reverse proxy load balancing pool.

    This class tracks the health, performance metrics, and connection state of a single
    backend server. It maintains rolling statistics for response times and request
    success rates, providing the data needed for intelligent load balancing decisions.

    Attributes:
        url (str): The base URL of the backend server (e.g., "http://localhost:8001")
        healthy (bool): Current health status of the backend server
        last_check_time (float): Timestamp of the last health check (monotonic time)
        active_connections (int): Number of currently active connections to this backend
        total_requests (int): Total number of requests forwarded to this backend
        failed_requests (int): Number of requests that failed or timed out
        response_times (deque[float]): Rolling window of response times (max 100 entries)

    Note:
        - URL is automatically normalized by removing trailing slashes
        - Response times are stored in a bounded deque to prevent memory leaks
        - Health status is updated by the HealthChecker component
    """

    url: str
    healthy: bool = True
    last_check_time: float = 0.0
    active_connections: int = 0
    total_requests: int = 0  # Need to memory bound - depends on how logging metrics will work
    failed_requests: int = 0
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def __post_init__(self) -> None:
        """
        Normalizes the backend URL by removing trailing slashes to ensure
        consistent URL handling when constructing target URLs for requests.

        Example:
            "http://localhost:8001/" → "http://localhost:8001"
        """
        self.url = self.url.rstrip("/")

    @property
    def avg_response_time(self) -> float:
        """
        Calculate the average response time

        This property provides a rolling average of response times, which is used
        by load balancing strategies like "fastest_avg_response" to route requests
        to the most responsive backend servers.

        The calculation uses a bounded deque of the most recent 100 response times
        to provide a current performance snapshot while preventing memory growth.

        Returns:
            float: Average response time in seconds, rounded to 4 decimal places.
                  Returns 0.0 if no response times have been recorded.

        Example:
            >>> backend = BackendServer("http://localhost:8001")
            >>> backend.add_response_time(0.1)
            >>> backend.add_response_time(0.2)
            >>> backend.avg_response_time
            0.15

        Note:
            - Older response times are automatically discarded when the deque is full
            - Useful for load balancing algorithms that prefer faster backends
        """
        if not self.response_times:
            return 0.0
        return round(sum(self.response_times) / len(self.response_times), 4)

    @property
    def success_rate(self) -> float:
        """
        Calculate the success rate percentage for this backend server.

        The success rate is calculated as the percentage of successful requests
        out of the total requests made to this backend. Failed requests include
        timeouts, connection errors, and HTTP error responses (4xx, 5xx).

        Returns:
            float: Success rate as a percentage (0.0 to 100.0), rounded to 2 decimal places.
                  Returns 100.0 if no requests have been made (avoiding division by zero).

        Formula:
            success_rate = ((total_requests - failed_requests) / total_requests) * 100

        Note:
            - Failed requests include timeouts, connection errors, and HTTP errors
        """
        return round((self.total_requests - self.failed_requests) / max(self.total_requests, 1) * 100, 2)

    @property
    def status(self) -> dict[str, Any]:
        """
        Get comprehensive status information for this backend server.

        Note:
            - last_check_time is None if no health check has been performed
            - All numeric values are converted to their appropriate types
            - This data is serializable for JSON responses
        """
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

    def add_response_time(self, time: float) -> None:
        """
        Add a new response time measurement to the rolling statistics.

        Args:
            time (float): Response time in seconds. Should be a positive value
                         representing the time taken for a request to complete.
        Raises:
            ValueError: If time is negative (though not explicitly checked)
        """
        self.response_times.append(time)

    def __str__(self) -> str:
        """
        String representation of the backend server for logging and debugging.
        """
        return f"{self.url}, healthy={self.healthy}, active={self.active_connections})"
