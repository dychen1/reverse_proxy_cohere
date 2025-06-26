import asyncio
import logging
import time

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.utils.backend_server import BackendServer


class HealthChecker:
    """
    Asynchronous health checker for backend servers with intelligent retry logic.

    Monitors the health of backend servers by periodically sending HTTP requests
    to their health endpoints. Uses exponential backoff, connection pooling, and
    retry mechanisms to provide reliable health monitoring with minimal overhead.

    Attributes:
        check_interval (int): Seconds between health check cycles
        timeout (int): HTTP request timeout in seconds
        max_connections (int): Maximum connections in health check pool
        running (bool): Whether health checker is currently active
        task (asyncio.Task | None): Background health check task
        session (ClientSession | None): HTTP session for health checks
        _backends (list[BackendServer]): List of backends to monitor
        logger (logging.Logger): Logger for health check events
    """

    def __init__(self, check_interval: int, timeout: int, max_connections: int, logger: logging.Logger):
        self.check_interval = check_interval
        self.timeout = timeout
        self.max_connections = max_connections
        self.running = False
        self.task: asyncio.Task | None = None
        self.session: ClientSession | None = None
        self._backends: list[BackendServer] = []
        self.logger = logger

    async def start(self, backends: list[BackendServer]) -> None:
        """
        Initializes the health checker with a dedicated HTTP session and begins the background health monitoring loop.

        Note:
            - Idempotent: safe to call multiple times
            - Creates dedicated connection pool for health checks
            - Starts background task for continuous monitoring
            - Health checks begin immediately after startup
        """
        if self.running:
            self.logger.warning("Health checker is already running")
            return

        self._backends = backends
        self.running = True

        # Create new session manager for healthchecker - hard coded config for connector for now
        connector = aiohttp.TCPConnector(
            limit=self.max_connections,
            limit_per_host=self.max_connections // len(backends),
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        timeout = ClientTimeout(total=self.timeout, connect=self.timeout / 2)
        self.session = ClientSession(timeout=timeout, connector=connector, headers={"User-Agent": "HealthChecker/1.0"})

        self.task = asyncio.create_task(self._health_check_loop())
        self.logger.info(f"Health checker started for {len(backends)} backends")

    async def stop(self) -> None:
        """
        Cancels the background health check task, closes the HTTP session,
        and performs proper cleanup to prevent resource leaks. This method
        should be called during proxy shutdown.

        Note:
            - Idempotent: safe to call multiple times
            - Cancels background task gracefully
            - Closes HTTP session and connection pool
            - Resets internal state for potential reuse
        """
        if not self.running:
            return

        self.running = False

        # Cancel the health check task
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                self.logger.debug("Health check task cancelled")

        # Close the session
        if self.session:
            await self.session.close()
            self.session = None

        self.logger.info("Health checker stopped")

    async def _health_check_loop(self) -> None:
        """
        Main health check loop with error handling and exponential backoff.

        Continuously monitors all backend servers by sending HTTP GET requests
        to their /health endpoints. Uses concurrent requests for efficiency and
        implements exponential backoff on failures to prevent overwhelming
        struggling backends.

        The loop:
        1. Checks all backends concurrently
        2. Updates health status based on responses
        3. Implements exponential backoff on failures
        4. Handles errors gracefully with retry limits
        5. Logs health status changes and errors

        Error handling:
        - Stops after 10 consecutive failures to prevent resource waste
        - Continues operation even if individual backends fail
        """
        consecutive_failures = 0
        max_consecutive_failures = 10

        while self.running:
            try:
                if not self._backends:
                    self.logger.warning("No backends to check")
                    await asyncio.sleep(self.check_interval)
                    continue

                # Check all backends concurrently
                tasks = [self._check_backend(backend) for backend in self._backends]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any exceptions that occurred
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        self.logger.error(f"Unexpected error checking backend {self._backends[i].url}: {result}")

                consecutive_failures = 0

            except Exception as e:
                consecutive_failures += 1
                self.logger.error(f"Health check loop error (attempt {consecutive_failures}): {e}")

                if consecutive_failures >= max_consecutive_failures:
                    self.logger.critical("Too many consecutive health check failures, stopping")
                    break

            # Use exponential backoff on failures
            sleep_time = self.check_interval
            if consecutive_failures > 0:
                sleep_time = min(self.check_interval * (2**consecutive_failures), 60)

            await asyncio.sleep(sleep_time)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _check_backend(self, backend: BackendServer) -> bool:
        """
        Check health of a single backend server with retry logic.

        Sends an HTTP GET request to the backend's /health endpoint and updates
        the backend's health status based on the response. Uses tenacity retry
        logic to handle transient network issues and timeouts.

        Health check logic:
        - HTTP 200: Backend is healthy
        - HTTP 4xx/5xx: Backend is unhealthy
        - Timeout: Backend is unhealthy (with retry)
        - Network error: Backend is unhealthy (with retry)

        Args:
            backend: BackendServer instance to check

        Returns:
            bool: True if backend is healthy, False otherwise

        Retry behavior:
        - Retries up to 2 times on network errors and timeouts
        - Uses exponential backoff between retries (0.5s to 2.0s)
        - Does not retry on HTTP error responses (4xx/5xx)
        - Does not retry on unexpected exceptions

        Note:
            - Updates backend.last_check_time on every check
            - Logs health status changes for monitoring
            - Response times are not recorded for health checks
        """
        if not self.session:
            self.logger.error("No session available for health check")
            return False

        health_url = f"{backend.url.rstrip('/')}/health"

        try:
            start_time = time.monotonic()

            async with self.session.get(health_url) as response:
                response_time = time.monotonic() - start_time

                # Update last check time regardless of result
                backend.last_check_time = time.monotonic()

                if response.status == 200:
                    if not backend.healthy:
                        self.logger.info(f"Backend {backend.url} is now healthy (response time: {response_time:.3f}s)")

                    backend.healthy = True
                    # Dont record response time for health check
                    return True
                else:
                    self.logger.warning(f"Backend {backend.url} health check failed: HTTP {response.status}")
                    backend.healthy = False
                    return False

        except asyncio.TimeoutError:
            if backend.healthy:
                self.logger.warning(f"Backend {backend.url} health check timed out after {self.timeout}s")
            backend.healthy = False
            backend.last_check_time = time.monotonic()
            raise  # Re-raise for tenacity to handle

        except aiohttp.ClientError as e:
            if backend.healthy:
                self.logger.warning(f"Backend {backend.url} health check failed: {type(e).__name__}: {e}")
            backend.healthy = False
            backend.last_check_time = time.monotonic()
            raise  # Re-raise for tenacity to handle

        except Exception as e:
            self.logger.error(f"Unexpected error checking backend {backend.url}: {type(e).__name__}: {e}")
            backend.healthy = False
            backend.last_check_time = time.monotonic()
            return False  # Don't retry unexpected errors
