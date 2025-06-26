import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

from aiohttp import web
from aiohttp.web import Request, Response

from src.settings import Settings
from src.utils.auth import auth_required
from src.utils.backend_server import BackendServer
from src.utils.connection_pool import ConnectionPool
from src.utils.health_checker import HealthChecker
from src.utils.load_balancer import LoadBalancer, LoadBalanceStrategy
from src.utils.logging import get_queue_logger


class ReverseProxy:
    """
    High-performance asynchronous reverse proxy server with load balancing and health checking.

    This proxy forwards HTTP requests to multiple backend servers using configurable
    load balancing strategies. It provides health monitoring, connection pooling,
    and streaming response handling for optimal performance.

    Attributes:
        settings (Settings): Configuration settings for the proxy
        logger (logging.Logger): Logger instance for proxy operations
        backends (list[BackendServer]): List of configured backend servers
        load_balancer (LoadBalancer): Load balancing strategy implementation
        connection_pool (ConnectionPool): HTTP connection pool for backend requests
        start_time (float): Timestamp when the proxy was started
        active_requests (int): Current number of active requests being processed
        max_body_size_bytes (int): Maximum request body size in bytes
        app (web.Application): aiohttp web application instance
        runner (web.AppRunner | None): Application runner for the web server
        site (web.TCPSite | None): TCP site for serving HTTP requests
    """

    def __init__(self, settings: Settings, logger: logging.Logger):
        """
        Initialize the reverse proxy with configuration and logging.

        Args:
            settings (Settings): Proxy configuration including backend URLs,
                               load balancing strategy, and connection settings
            logger (logging.Logger): Logger instance for recording proxy operations

        Raises:
            ValueError: If no backend URLs are configured in settings
        """
        self.settings = settings
        self.logger = logger
        self.backends: list[BackendServer] = [BackendServer(backend_url) for backend_url in self.settings.backend_urls]
        self.load_balancer = LoadBalancer(LoadBalanceStrategy(self.settings.load_balance_strategy))
        self.connection_pool = ConnectionPool(
            len(self.settings.backend_urls),
            self.settings.max_connections,
            self.settings.request_timeout,
            self.settings.connection_timeout,
            self.settings.keepalive_timeout,
        )
        self.start_time: float = time.monotonic()
        self.active_requests: int = 0
        self.max_body_size_bytes = self.settings.max_body_size * 1024 * 1024  # Convert to bytes
        self.app = web.Application(client_max_size=self.max_body_size_bytes)  # Application level max body size
        self._setup_routes()

        # Server components
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    @property
    def status(self) -> dict[str, Any]:
        """
        Get current proxy status and metrics.
        """
        return {
            "uptime": float(time.monotonic() - self.start_time),
            "active_requests": int(self.active_requests),
            "backends": [b.status for b in self.backends],
            "settings": self.settings.model_dump(mode="json"),
        }

    def _setup_routes(self) -> None:
        """
        Configure application routes and endpoints.

        Sets up the main proxy handler for all incoming requests and
        administrative endpoints for status and health monitoring.

        Routes configured:
        - "* /{path:.*}": Catch-all proxy handler for forwarding requests
        - "GET /_proxy/status": Administrative status endpoint (requires auth)
        - "GET /_proxy/health": Health check endpoint for monitoring
        """
        # Catch all routes with any method
        self.app.router.add_route("*", "/{path:.*}", self._proxy_handler)

        # Add status and health endpoints
        self.app.router.add_get("/_proxy/status", self._status_handler)
        self.app.router.add_get("/_proxy/health", self._health_handler)

    async def _proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Main proxy request handler for forwarding HTTP requests to backend servers.

        This method processes incoming HTTP requests by:
        1. Validating host headers and request methods
        2. Selecting an appropriate backend using load balancing
        3. Forwarding the request to the selected backend
        4. Streaming the response back to the client
        5. Tracking metrics and handling errors

        Args:
            request (web.Request): Incoming HTTP request from client

        Returns:
            web.StreamResponse: HTTP response to send back to client

        Raises:
            web.HTTPForbidden (403): If host is not in allowed_hosts list
            web.HTTPMethodNotAllowed (405): If HTTP method is not supported
            web.HTTPServiceUnavailable (503): If no healthy backends available
            web.HTTPBadGateway (502): If backend request fails
            web.HTTPInternalServerError (500): If proxy encounters internal error
        """
        # Check allowed hosts
        if self.settings.allowed_hosts:
            host_domain: str | None = urlparse(f"http://{request.headers.get('Host', '')}").hostname
            if host_domain is None or host_domain not in self.settings.allowed_hosts:
                return web.Response(text="Host not allowed", status=403)

        # Check request method
        if request.method not in ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]:
            return web.Response(text="Method not allowed", status=405)

        self.logger.info(f"{request.remote} - {request.method} - {request.path}")
        start_time: float = time.monotonic()
        try:
            self.active_requests += 1
            # Select backend
            backend = await self.load_balancer.select_backend(self.backends)
            if not backend:
                return web.Response(
                    text="No healthy backend servers available", status=503, headers={"Content-Type": "text/plain"}
                )

            # Track connection
            backend.increment_active_connections()
            backend.total_requests += 1

            try:
                response = await self._forward_request(request, backend)
                response_time = time.monotonic() - start_time
                backend.add_response_time(response_time)
                return response

            except Exception as e:
                backend.failed_requests += 1
                self.logger.error(f"Error forwarding to {backend.url}: {e}")
                return web.Response(text=f"Backend error: {str(e)}", status=502, headers={"Content-Type": "text/plain"})
            finally:
                backend.decrement_active_connections()

        except Exception as e:
            self.logger.error(f"Error selecting backend: {e}")
            return web.Response(text=f"Proxy error: {str(e)}", status=500, headers={"Content-Type": "text/plain"})
        finally:
            self.active_requests -= 1

    async def _forward_request(self, request: Request, backend: BackendServer) -> web.StreamResponse:
        """
        Forward an HTTP request to a specific backend server.

        This method handles the actual forwarding of requests to backend servers,
        including header manipulation, request body streaming, and response streaming.
        It ensures proper HTTP semantics are maintained while adding proxy-specific
        headers for backend identification.

        Args:
            request (Request): Original client request to forward
            backend (BackendServer): Target backend server for the request

        Returns:
            web.StreamResponse: Streaming response from the backend server

        Raises:
            asyncio.CancelledError: If client disconnects during response streaming
            aiohttp.ClientError: If backend request fails
            Exception: For other forwarding errors

        Note:
            - Removes hop-by-hop headers that shouldn't be forwarded
            - Adds X-Forwarded-* headers for backend identification
            - Streams request body without loading into memory
            - Streams response body in chunks for memory efficiency
        """
        # Build target URL
        target_url = f"{backend.url}{request.path_qs}"

        # Prepare headers
        headers: dict[str, str] = dict(request.headers)

        # Remove hop-by-hop headers
        hop_by_hop = {
            "connection",
            "upgrade",
            "proxy-authorization",
            "proxy-authenticate",
            "te",
            "trailers",
            "transfer-encoding",
        }
        headers = {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}

        # Add forwarded headers
        headers["X-Forwarded-For"] = request.remote if request.remote else "unknown"
        headers["X-Forwarded-Proto"] = request.scheme
        headers["X-Forwarded-Host"] = request.host

        # Forward request using connection pool
        assert self.connection_pool.session is not None
        async with self.connection_pool.session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.content,  # Stream body without reading it into memory
            allow_redirects=False,
        ) as backend_response:
            # Prepare response headers for streaming
            response_headers = {k: v for k, v in backend_response.headers.items() if k.lower() not in hop_by_hop}

            # Stream the response back to the client
            response = web.StreamResponse(
                status=backend_response.status,
                headers=response_headers,
                reason=backend_response.reason,
            )
            await response.prepare(request)

            try:
                # Read from backend and write to client chunk by chunk
                while True:
                    chunk = await backend_response.content.read(8192)
                    if not chunk:
                        break
                    await response.write(chunk)

                await response.write_eof()
                return response

            except asyncio.CancelledError:
                self.logger.warning(f"Client disconnected before response was fully sent for {request.path}")
                raise  # Re-raising is important for aiohttp to handle cleanup

    @auth_required
    async def _status_handler(self, request: Request) -> Response:
        """
        Administrative endpoint for proxy status and metrics.

        Provides detailed information about the proxy's current state including:
        uptime, active requests, backend health status, and configuration settings.
        This endpoint requires authentication via the @auth_required decorator.

        Args:
            request (Request): HTTP request for status information

        Returns:
            Response: JSON response containing proxy status and metrics

        Security:
            Requires valid Authorization header with Bearer token
        """
        return web.json_response(self.status)

    async def _health_handler(self, request: Request) -> Response:
        """
        Health check endpoint for monitoring and load balancer integration.

        Provides a simple health status indicating whether the proxy has
        any healthy backend servers available. This endpoint is typically
        used by external monitoring systems and load balancers to determine
        if the proxy should receive traffic.

        Args:
            request (Request): HTTP request for health check

        Returns:
            Response: JSON response with health status:
                - status: "healthy" if backends available, "unhealthy" otherwise
                - healthy_backends: Number of currently healthy backends
                - total_backends: Total number of configured backends

        Note:
            Returns HTTP 503 status if no healthy backends are available
        """
        healthy_backends = len([b for b in self.backends if b.healthy])

        if healthy_backends > 0:
            return web.json_response(
                {"status": "healthy", "healthy_backends": healthy_backends, "total_backends": len(self.backends)}
            )
        else:
            return web.json_response(
                {"status": "unhealthy", "healthy_backends": 0, "total_backends": len(self.backends)}, status=503
            )

    async def start(self) -> None:
        """
        Start the reverse proxy server and all associated components.

        This method initializes and starts the proxy server, including:
        1. Connection pool initialization
        2. Web server setup and binding
        3. Health checker startup
        4. Logging of startup information

        The server will begin accepting HTTP requests on the configured
        host and port once this method completes successfully.

        Raises:
            Exception: If no backend servers are configured
            OSError: If the server cannot bind to the specified host/port
            Exception: If connection pool or health checker fails to start
        """
        if not self.backends:
            raise Exception("No backend servers configured")

        self.logger.info(f"Reverse proxy starting with {len(self.backends)} backends")

        # Start connection pool
        await self.connection_pool.start()

        # Start web server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(
            self.runner, self.settings.host, self.settings.port, reuse_address=True, reuse_port=True
        )  # Allows to run multiple of the same application on the same port - No prod downtime if switching using same port
        await self.site.start()

        self.logger.info(f"Reverse proxy started - Status\n{self.status}")
        self.logger.info(f"Status endpoint: http://{self.settings.host}:{self.settings.port}/_proxy/status")
        self.logger.info(f"Health endpoint: http://{self.settings.host}:{self.settings.port}/_proxy/health")

    async def stop(self) -> None:
        """
        Gracefully stop the reverse proxy server and cleanup resources.

        This method performs a clean shutdown of all proxy components:
        1. Stops accepting new HTTP requests
        2. Closes the web server and TCP site
        3. Closes the connection pool and all backend connections
        4. Logs shutdown information

        The method ensures that in-flight requests are allowed to complete
        before shutting down, providing a graceful termination.

        Note:
            This method is typically called during application shutdown
            and should be awaited to ensure proper cleanup.
        """
        self.logger.info("Shutting down proxy server...")

        # Stop web server
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

        # Close connection pool
        await self.connection_pool.close()


async def main() -> None:
    """
    Main entry point for the reverse proxy server application.

    This function initializes and runs the complete reverse proxy server,
    including all supporting components like health checking and logging.
    It handles graceful shutdown on keyboard interrupt and ensures proper
    cleanup of all resources.

    The function runs indefinitely until interrupted, maintaining the proxy
    server in a running state while continuously monitoring backend health
    and handling incoming requests.

    Components started:
    - Settings configuration
    - Logging system with file and stdout output
    - Reverse proxy server
    - Health checker for backend monitoring

    Signal handling:
    - SIGINT (Ctrl+C): Graceful shutdown
    - SIGTERM: Graceful shutdown

    Raises:
        KeyboardInterrupt: When user interrupts the application
        Exception: If any component fails to start or run properly
    """
    settings = Settings()
    logger, listener = get_queue_logger(
        file_path=settings.log_path,
        log_to_file=settings.log_to_file,
        stream_stdout=settings.stream_stdout,
        debug=settings.debug,
    )
    proxy = ReverseProxy(settings, logger)
    health_checker = HealthChecker(
        settings.health_check_interval,
        settings.health_check_timeout,
        settings.health_check_max_connections,
        logger,
    )

    try:
        await health_checker.start(proxy.backends)
        await proxy.start()
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await health_checker.stop()
        await proxy.stop()
        listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
