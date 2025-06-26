import random
from enum import Enum

from src.utils.backend_server import BackendServer


class LoadBalanceStrategy(Enum):
    LEAST_CONNECTIONS = "least_connections"
    FASTEST_AVG_RESPONSE = "fastest_avg_response"
    RANDOM = "random"


class LoadBalancer:
    """Load balancer which supports stateless load balancing strategies"""

    def __init__(self, strategy: LoadBalanceStrategy = LoadBalanceStrategy.LEAST_CONNECTIONS):
        self.strategy = strategy

    async def select_backend(self, backends: list[BackendServer]) -> BackendServer | None:
        """Select the best backend based on stateless strategy"""
        healthy_backends = [b for b in backends if b.healthy]
        if not healthy_backends:
            return None

        if self.strategy == LoadBalanceStrategy.LEAST_CONNECTIONS:
            return min(healthy_backends, key=lambda b: b.active_connections)
        elif self.strategy == LoadBalanceStrategy.FASTEST_AVG_RESPONSE:
            return min(healthy_backends, key=lambda b: b.avg_response_time)
        else:  # Default to random
            return random.choice(healthy_backends)
