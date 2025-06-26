import asyncio
import time
from urllib.parse import urlparse

from aiohttp import web
from aiohttp.web import Request, Response

from src.settings import Settings
from src.utils.backend_server import BackendServer
from src.utils.connection_pool import ConnectionPool
from src.utils.health_checker import HealthChecker
from src.utils.load_balancer import LoadBalancer, LoadBalanceStrategy
from src.utils.logging import get_app_logger

logger = get_app_logger()


class ReverseProxy:
    """High-performance async reverse proxy"""

    def __init__(self, settings: Settings):
        self.settings = settings

        self.backends: list[BackendServer] = [BackendServer(backend_url) for backend_url in self.settings.backend_urls]
        self.load_balancer = LoadBalancer(LoadBalanceStrategy(self.settings.load_balance_strategy))
        self.health_checker = HealthChecker(
            self.settings.health_check_interval,
            self.settings.health_check_timeout,
            self.settings.health_check_max_connections,
        )
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

    def _setup_routes(self):
        """Setup application routes"""
        # Catch all routes with any method
        self.app.router.add_route("*", "/{path:.*}", self._proxy_handler)

        # Add status and health endpoints
        self.app.router.add_get("/_proxy/status", self._status_handler)
        self.app.router.add_get("/_proxy/health", self._health_handler)

    async def _proxy_handler(self, request: web.Request) -> web.Response:
        """Main proxy request handler"""
        # Check allowed hosts
        if self.settings.allowed_hosts:
            host_domain: str | None = urlparse(f"http://{request.headers.get('Host', '')}").hostname
            if host_domain is None or host_domain not in self.settings.allowed_hosts:
                return web.Response(text="Host not allowed", status=403)

        # Check request method
        if request.method not in ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]:
            return web.Response(text="Method not allowed", status=405)

        logger.info(f"{request.remote} - {request.method} - {request.path}")
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
            backend.active_connections += 1
            backend.total_requests += 1

            try:
                response = await self._forward_request(request, backend)
                response_time = time.monotonic() - start_time
                backend.add_response_time(response_time)
                return response

            except Exception as e:
                backend.failed_requests += 1
                logger.error(f"Error forwarding to {backend.url}: {e}")
                return web.Response(text=f"Backend error: {str(e)}", status=502, headers={"Content-Type": "text/plain"})
            finally:
                backend.active_connections -= 1

        except Exception as e:
            logger.error(f"Error selecting backend: {e}")
            return web.Response(text=f"Proxy error: {str(e)}", status=500, headers={"Content-Type": "text/plain"})
        finally:
            self.active_requests -= 1

    async def _forward_request(self, request: Request, backend: BackendServer) -> Response:
        """Forward request to backend server"""
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
            # Prepare response headers
            response_headers = dict(backend_response.headers)

            # Remove hop-by-hop headers from response
            response_headers = {k: v for k, v in response_headers.items() if k.lower() not in hop_by_hop}

            # TODO: Cache response headers and body
            return web.Response(body=backend_response.content, status=backend_response.status, headers=response_headers)

    async def _status_handler(self, request: Request) -> Response:
        """Status endpoint handler"""
        uptime = time.monotonic() - self.start_time

        status = {
            "proxy": {
                "host": self.settings.host,
                "port": self.settings.port,
                "uptime": uptime,
                "active_requests": self.active_requests,
                "max_connections": self.settings.max_connections,
                "strategy": self.settings.load_balance_strategy,
            },
            "backends": [
                {
                    "url": b.url,
                    "healthy": b.healthy,
                    "active_connections": b.active_connections,
                    "total_requests": b.total_requests,
                    "failed_requests": b.failed_requests,
                    "success_rate": round((b.total_requests - b.failed_requests) / max(b.total_requests, 1) * 100, 2),
                    "avg_response_time": round(b.avg_response_time, 3),
                    "last_check_time": b.last_check_time,
                }
                for b in self.backends
            ],
            "config": {
                "health_check_interval": self.settings.health_check_interval,
                "health_check_timeout": self.settings.health_check_timeout,
                "request_timeout": self.settings.request_timeout,
            },
        }

        return web.json_response(status)

    async def _health_handler(self, request: Request) -> Response:
        """Health check endpoint"""
        healthy_backends = len([b for b in self.backends if b.healthy])

        if healthy_backends > 0:
            return web.json_response(
                {"status": "healthy", "healthy_backends": healthy_backends, "total_backends": len(self.backends)}
            )
        else:
            return web.json_response(
                {"status": "unhealthy", "healthy_backends": 0, "total_backends": len(self.backends)}, status=503
            )

    async def start(self):
        """Start the reverse proxy server"""
        if not self.backends:
            raise Exception("No backend servers configured")

        # Start connection pool
        await self.connection_pool.start()

        # Start health checker
        await self.health_checker.start(self.backends)

        # Start web server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(
            self.runner, self.settings.host, self.settings.port, reuse_address=True, reuse_port=True
        )
        await self.site.start()

        logger.info(f"Async reverse proxy started on {self.settings.host}:{self.settings.port}")
        logger.info(f"Load balancing strategy: {self.settings.load_balance_strategy}")
        logger.info(f"Configured backends: {[str(b) for b in self.backends]}")
        logger.info(f"Status endpoint: http://{self.settings.host}:{self.settings.port}/_proxy/status")
        logger.info(f"Health endpoint: http://{self.settings.host}:{self.settings.port}/_proxy/health")

    async def stop(self):
        """Stop the reverse proxy server"""
        logger.info("Shutting down proxy server...")

        # Stop health checker
        await self.health_checker.stop()

        # Stop web server
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

        # Close connection pool
        await self.connection_pool.close()


async def main():
    """Main entry point"""
    # Create settings instance
    settings = Settings()

    # Create and start proxy
    proxy = ReverseProxy(settings)

    try:
        await proxy.start()
        # Keep running
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await proxy.stop()


if __name__ == "__main__":
    asyncio.run(main())
