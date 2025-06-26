import asyncio
import logging
import time

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.utils.backend_server import BackendServer


class HealthChecker:
    """Async health checker for backend servers with reusable session and improved error handling"""

    def __init__(self, check_interval: int, timeout: int, max_connections: int, logger: logging.Logger):
        self.check_interval = check_interval
        self.timeout = timeout
        self.max_connections = max_connections
        self.running = False
        self.task: asyncio.Task | None = None
        self.session: ClientSession | None = None
        self._backends: list[BackendServer] = []
        self.logger = logger

    async def start(self, backends: list[BackendServer]):
        """Start health checking with proper session management"""
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

    async def stop(self):
        """Graceful exits health checking and cleanup resources"""
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

    async def _health_check_loop(self):
        """Main health check loop with proper error handling"""
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
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _check_backend(self, backend: BackendServer) -> bool:
        """Check health of a single backend with tenacity retry logic"""
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
