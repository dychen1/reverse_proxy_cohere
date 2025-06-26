from aiohttp import ClientSession, ClientTimeout, TCPConnector


class ConnectionPool:
    """
    Manages aiohttp client sessions with connection pooling for backend requests.

    Provides efficient HTTP connection reuse, timeout management, and connection
    limits to optimize performance when forwarding requests to backend servers.
    """

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

    async def start(self) -> None:
        """
        Create and configure the aiohttp client session for backend requests.

        Initializes the HTTP client session with the configured connection pool,
        timeouts, and custom headers.

        The session includes:
        - Connection pooling with configured limits
        - Request and connection timeouts
        - Custom User-Agent header for backend identification
        - Automatic connection cleanup for closed connections

        Note:
            - Idempotent: safe to call multiple times
            - Must be called before making any backend requests
            - Session is shared across all backend requests for efficiency
        """
        if self.session is None:
            self.session = ClientSession(
                connector=self.connector, timeout=self.timeout, headers={"User-Agent": "AsyncReverseProxy/1.0"}
            )

    async def close(self) -> None:
        """
        Close the client session and cleanup all connections.

        Gracefully shuts down the HTTP client session, closing all active
        connections to backend servers and freeing associated resources.
        This method should be called during proxy shutdown to ensure proper
        cleanup and prevent connection leaks.

        The cleanup process:
        - Closes all active HTTP connections
        - Cancels any pending requests
        - Releases connection pool resources
        - Resets session to None for potential reuse

        Note:
            - Idempotent: safe to call multiple times
            - Should be awaited to ensure complete cleanup
            - Must be called before proxy shutdown to prevent resource leaks
        """
        if self.session:
            await self.session.close()
            self.session = None
