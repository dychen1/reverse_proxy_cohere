from aiohttp import ClientSession, ClientTimeout, TCPConnector


class ConnectionPool:
    """Manages aiohttp client sessions with connection pooling"""

    def __init__(
        self,
        n_hosts: int,
        max_connections: int,
        request_timeout: int = 30,
        connection_timeout: int = 5,
        keepalive_timeout: int = 60,
    ):
        self.max_connections = max_connections
        self.timeout = ClientTimeout(total=request_timeout, connect=connection_timeout)
        self.connector = TCPConnector(
            limit=max_connections,
            limit_per_host=max_connections // n_hosts,
            keepalive_timeout=keepalive_timeout,
            enable_cleanup_closed=True,
        )
        self.session: ClientSession | None = None

    async def start(self):
        if self.session is None:
            self.session = ClientSession(
                connector=self.connector, timeout=self.timeout, headers={"User-Agent": "AsyncReverseProxy/1.0"}
            )

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
