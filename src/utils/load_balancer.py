import random
from enum import Enum

from src.utils.backend_server import BackendServer


class LoadBalanceStrategy(Enum):
    """
    Enumeration of available load balancing strategies.

    Attributes:
        LEAST_CONNECTIONS: Routes to backend with fewest active connections
        FASTEST_AVG_RESPONSE: Routes to backend with lowest average response time
        RANDOM: Routes to randomly selected backend for basic distribution
    """

    LEAST_CONNECTIONS = "least_connections"
    FASTEST_AVG_RESPONSE = "fastest_avg_response"
    RANDOM = "random"


class LoadBalancer:
    """
    Stateless load balancer supporting multiple distribution strategies.

    The balancer only considers healthy backends and returns None if no
    healthy backends are available, allowing the proxy to return appropriate
    error responses to clients.

    Attributes:
        strategy (LoadBalanceStrategy): Current load balancing algorithm
    """

    def __init__(self, strategy: LoadBalanceStrategy = LoadBalanceStrategy.LEAST_CONNECTIONS):
        self.strategy = strategy

    async def select_backend(self, backends: list[BackendServer]) -> BackendServer | None:
        """
        Select the optimal backend server based on current strategy and metrics.

        Args:
            backends: List of all configured backend servers to evaluate

        Returns:
            BackendServer | None: Selected backend server, or None if no healthy
                                 backends are available

        Selection process:
            1. Filters backends to only healthy ones (healthy=True)
            2. Returns None if no healthy backends available
            3. Applies strategy-specific selection logic
            4. Returns the selected backend server

        Performance characteristics:
            - O(n) time complexity where n is number of backends
            - Stateless: no memory of previous selections
            - Thread-safe: can be called concurrently
            - Immediate: uses current metrics, not historical data

        Example:
            >>> backends = [backend1, backend2, backend3]
            >>> selected = await balancer.select_backend(backends)
            >>> if selected:
            ...     print(f"Selected: {selected.url}")
            ... else:
            ...     print("No healthy backends available")
        """
        healthy_backends = [b for b in backends if b.healthy]
        if not healthy_backends:
            return None

        if self.strategy == LoadBalanceStrategy.LEAST_CONNECTIONS:
            return min(healthy_backends, key=lambda b: b.active_connections)
        elif self.strategy == LoadBalanceStrategy.FASTEST_AVG_RESPONSE:
            return min(healthy_backends, key=lambda b: b.avg_response_time)
        else:  # Default to random
            return random.choice(healthy_backends)
