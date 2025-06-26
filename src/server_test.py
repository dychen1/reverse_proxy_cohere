#!/usr/bin/env python3
"""
High-Performance Async Reverse Proxy Server

An async HTTP reverse proxy server using asyncio and aiohttp for maximum performance.
Supports load balancing, health checks, connection pooling, and concurrent request handling.

Requirements:
    pip install aiohttp aiofiles pydantic-settings
"""

import asyncio
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.web_request import Request
from aiohttp.web_response import Response

# Pydantic imports
from pydantic import BaseSettings, Field, root_validator, validator
from pydantic.env_settings import SettingsSourceCallable


# Configuration Classes
class BackendConfig(BaseSettings):
    """Configuration for a single backend server"""

    url: str = Field(..., description="Backend server URL")
    weight: int = Field(1, description="Load balancing weight", ge=1, le=100)
    enabled: bool = Field(True, description="Whether backend is enabled")

    @validator("url")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v.rstrip("/")


class ProxySettings(BaseSettings):
    """Main proxy configuration"""

    # Server settings
    host: str = Field("localhost", description="Host to bind to")
    port: int = Field(8080, description="Port to listen on", ge=1, le=65535)

    # Performance settings
    max_connections: int = Field(1000, description="Maximum concurrent connections", ge=1)
    request_timeout: int = Field(30, description="Request timeout in seconds", ge=1, le=300)
    keepalive_timeout: int = Field(60, description="Keep-alive timeout in seconds", ge=1)

    # Load balancing
    strategy: LoadBalanceStrategy = Field(
        LoadBalanceStrategy.WEIGHTED_ROUND_ROBIN, description="Load balancing strategy"
    )

    # Health checking
    health_check_interval: int = Field(10, description="Health check interval in seconds", ge=1)
    health_check_timeout: int = Field(5, description="Health check timeout in seconds", ge=1)
    health_check_path: str = Field("/health", description="Health check endpoint path")

    # Logging
    log_level: str = Field("INFO", description="Logging level")
    log_format: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format string")
    access_log: bool = Field(True, description="Enable access logging")

    # Backend servers
    backends: List[BackendConfig] = Field(default_factory=list, description="List of backend servers")

    # Security
    allowed_hosts: List[str] = Field(
        default_factory=list, description="List of allowed host headers (empty = allow all)"
    )
    max_body_size: int = Field(
        10 * 1024 * 1024,  # 10MB
        description="Maximum request body size in bytes",
        ge=0,
    )

    # Monitoring
    enable_metrics: bool = Field(True, description="Enable metrics collection")
    metrics_path: str = Field("/_proxy/status", description="Metrics endpoint path")

    @validator("log_level")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()

    @root_validator
    def validate_backends(cls, values):
        backends = values.get("backends", [])
        if not backends:
            # Allow empty backends for dynamic configuration
            pass
        return values

    class Config:
        env_prefix = "PROXY_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # Allow loading from JSON file
        json_file = "proxy_config.json"

        @classmethod
        def customise_sources(
            cls,
            init_settings: SettingsSourceCallable,
            env_settings: SettingsSourceCallable,
            file_secret_settings: SettingsSourceCallable,
        ) -> tuple[SettingsSourceCallable, ...]:
            return (
                init_settings,
                env_settings,
                json_file_settings,
                file_secret_settings,
            )


def json_file_settings(settings: BaseSettings) -> Dict[str, Any]:
    """Load settings from JSON file"""
    json_file = getattr(settings.__config__, "json_file", None)
    if json_file and Path(json_file).exists():
        try:
            with open(json_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Could not load JSON config file {json_file}: {e}")
    return {}


# Initialize settings
settings = ProxySettings()

# Configure logging based on settings
logging.basicConfig(level=getattr(logging, settings.log_level), format=settings.log_format)
logger = logging.getLogger(__name__)


class LoadBalanceStrategy(Enum):
    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"


@dataclass
class BackendServer:
    url: str
    weight: int = 1
    enabled: bool = True
    healthy: bool = True
    last_check: float = 0
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    response_times: List[float] = field(default_factory=list)

    def __post_init__(self):
        self.url = self.url.rstrip("/")

    @classmethod
    def from_config(cls, config: BackendConfig) -> "BackendServer":
        """Create BackendServer from configuration"""
        return cls(url=config.url, weight=config.weight, enabled=config.enabled)

    @property
    def avg_response_time(self) -> float:
        """Calculate average response time from last 100 requests"""
        if not self.response_times:
            return 0.0
        return sum(self.response_times[-100:]) / len(self.response_times[-100:])

    def add_response_time(self, time: float):
        """Add response time and keep only last 100"""
        self.response_times.append(time)
        if len(self.response_times) > 100:
            self.response_times.pop(0)

    def __str__(self):
        return f"{self.url} (weight={self.weight}, healthy={self.healthy}, active={self.active_connections})"


class ConnectionPool:
    """Manages aiohttp client sessions with connection pooling"""

    def __init__(self, max_connections: int = 100, timeout: int = 30):
        self.max_connections = max_connections
        self.timeout = ClientTimeout(total=timeout, connect=5)
        self.connector = aiohttp.TCPConnector(
            limit=max_connections, limit_per_host=max_connections // 4, keepalive_timeout=60, enable_cleanup_closed=True
        )
        self.session = None

    async def __aenter__(self):
        self.session = ClientSession(
            connector=self.connector, timeout=self.timeout, headers={"User-Agent": "AsyncReverseProxy/1.0"}
        )
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()


class LoadBalancer:
    """High-performance load balancer with multiple strategies"""

    def __init__(self, strategy: LoadBalanceStrategy = LoadBalanceStrategy.WEIGHTED_ROUND_ROBIN):
        self.strategy = strategy
        self.current_index = 0
        self._lock = asyncio.Lock()

    async def select_backend(self, backends: List[BackendServer]) -> Optional[BackendServer]:
        """Select the best backend based on strategy"""
        healthy_backends = [b for b in backends if b.healthy]

        if not healthy_backends:
            return None

        async with self._lock:
            if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
                backend = healthy_backends[self.current_index % len(healthy_backends)]
                self.current_index += 1

            elif self.strategy == LoadBalanceStrategy.LEAST_CONNECTIONS:
                backend = min(healthy_backends, key=lambda b: b.active_connections)

            elif self.strategy == LoadBalanceStrategy.WEIGHTED_ROUND_ROBIN:
                # Create weighted list
                weighted_backends = []
                for b in healthy_backends:
                    weighted_backends.extend([b] * b.weight)

                if weighted_backends:
                    backend = weighted_backends[self.current_index % len(weighted_backends)]
                    self.current_index += 1
                else:
                    backend = healthy_backends[0]

            else:
                backend = healthy_backends[0]

        return backend


class HealthChecker:
    """Async health checker for backend servers"""

    def __init__(self, config: ProxySettings):
        self.check_interval = config.health_check_interval
        self.timeout = config.health_check_timeout
        self.health_path = config.health_check_path
        self.running = False
        self.task = None

    async def start(self, backends: List[BackendServer]):
        """Start health checking"""
        self.running = True
        self.task = asyncio.create_task(self._health_check_loop(backends))

    async def stop(self):
        """Stop health checking"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _health_check_loop(self, backends: List[BackendServer]):
        """Main health check loop"""
        while self.running:
            tasks = [self._check_backend(backend) for backend in backends]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(self.check_interval)

    async def _check_backend(self, backend: BackendServer):
        """Check health of a single backend"""
        if not backend.enabled:
            backend.healthy = False
            return

        health_url = f"{backend.url}{self.health_path}"

        try:
            timeout = ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                start_time = time.time()
                async with session.get(health_url) as response:
                    response_time = time.time() - start_time

                    if response.status == 200:
                        if not backend.healthy:
                            logger.info(f"Backend {backend.url} is now healthy")
                        backend.healthy = True
                        backend.add_response_time(response_time)
                    else:
                        backend.healthy = False
                        logger.warning(f"Backend {backend.url} health check failed: {response.status}")

        except Exception as e:
            if backend.healthy:
                logger.warning(f"Backend {backend.url} is now unhealthy: {e}")
            backend.healthy = False

        backend.last_check = time.time()


class AsyncReverseProxy:
    """High-performance async reverse proxy"""

    def __init__(self, config: ProxySettings = None):
        self.config = config or settings

        self.backends: List[BackendServer] = []
        self.load_balancer = LoadBalancer(self.config.strategy)
        self.health_checker = HealthChecker(self.config)
        self.connection_pool = ConnectionPool(self.config.max_connections, self.config.request_timeout)

        self.app = web.Application()
        self._setup_routes()

        self.runner = None
        self.site = None

        # Statistics
        self.total_requests = 0
        self.active_requests = 0
        self.start_time = time.time()

        # Load backends from config
        self._load_backends_from_config()

    def _setup_routes(self):
        """Setup application routes"""
        self.app.router.add_route("*", "/{path:.*}", self._proxy_handler)

        if self.config.enable_metrics:
            self.app.router.add_get(self.config.metrics_path, self._status_handler)
            self.app.router.add_get("/_proxy/health", self._health_handler)

    def _load_backends_from_config(self):
        """Load backends from configuration"""
        for backend_config in self.config.backends:
            backend = BackendServer.from_config(backend_config)
            self.backends.append(backend)
            logger.info(f"Loaded backend from config: {backend}")

    def add_backend(self, url: str, weight: int = 1):
        """Add a backend server"""
        backend = BackendServer(url, weight)
        self.backends.append(backend)
        logger.info(f"Added backend: {backend}")

    def remove_backend(self, url: str):
        """Remove a backend server"""
        url = url.rstrip("/")
        self.backends = [b for b in self.backends if b.url != url]
        logger.info(f"Removed backend: {url}")

    async def _proxy_handler(self, request: Request) -> Response:
        """Main proxy request handler"""
        # Check allowed hosts
        if self.config.allowed_hosts:
            host = request.headers.get("Host", "").split(":")[0]
            if host not in self.config.allowed_hosts:
                return web.Response(text="Host not allowed", status=403)

        # Check body size
        content_length = request.headers.get("Content-Length")
        if content_length and int(content_length) > self.config.max_body_size:
            return web.Response(text="Request body too large", status=413)

        start_time = time.time()
        self.total_requests += 1
        self.active_requests += 1

        if self.config.access_log:
            logger.info(f"{request.remote} {request.method} {request.path}")

        try:
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
                response_time = time.time() - start_time
                backend.add_response_time(response_time)
                return response

            except Exception as e:
                backend.failed_requests += 1
                logger.error(f"Error forwarding to {backend.url}: {e}")
                return web.Response(text=f"Backend error: {str(e)}", status=502, headers={"Content-Type": "text/plain"})
            finally:
                backend.active_connections -= 1

        finally:
            self.active_requests -= 1

    async def _forward_request(self, request: Request, backend: BackendServer) -> Response:
        """Forward request to backend server"""
        # Build target URL
        target_url = f"{backend.url}{request.path_qs}"

        # Prepare headers
        headers = dict(request.headers)

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
        headers["X-Forwarded-For"] = request.remote
        headers["X-Forwarded-Proto"] = request.scheme
        headers["X-Forwarded-Host"] = request.host

        # Read request body
        if request.can_read_body:
            data = await request.read()
        else:
            data = None

        # Forward request using connection pool
        async with self.connection_pool as session:
            async with session.request(
                method=request.method, url=target_url, headers=headers, data=data, allow_redirects=False
            ) as backend_response:
                # Prepare response headers
                response_headers = dict(backend_response.headers)

                # Remove hop-by-hop headers from response
                response_headers = {k: v for k, v in response_headers.items() if k.lower() not in hop_by_hop}

                # Read response body
                body = await backend_response.read()

                return web.Response(body=body, status=backend_response.status, headers=response_headers)

    async def _status_handler(self, request: Request) -> Response:
        """Status endpoint handler"""
        uptime = time.time() - self.start_time

        status = {
            "proxy": {
                "host": self.config.host,
                "port": self.config.port,
                "uptime": uptime,
                "total_requests": self.total_requests,
                "active_requests": self.active_requests,
                "max_connections": self.config.max_connections,
                "strategy": self.config.strategy.value,
            },
            "backends": [
                {
                    "url": b.url,
                    "weight": b.weight,
                    "enabled": b.enabled,
                    "healthy": b.healthy,
                    "active_connections": b.active_connections,
                    "total_requests": b.total_requests,
                    "failed_requests": b.failed_requests,
                    "success_rate": round((b.total_requests - b.failed_requests) / max(b.total_requests, 1) * 100, 2),
                    "avg_response_time": round(b.avg_response_time, 3),
                    "last_check": b.last_check,
                }
                for b in self.backends
            ],
            "config": {
                "health_check_interval": self.config.health_check_interval,
                "health_check_timeout": self.config.health_check_timeout,
                "request_timeout": self.config.request_timeout,
                "max_body_size": self.config.max_body_size,
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

        # Start health checker
        await self.health_checker.start(self.backends)

        # Start web server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.config.host, self.config.port, reuse_address=True, reuse_port=True)
        await self.site.start()

        logger.info(f"Async reverse proxy started on {self.config.host}:{self.config.port}")
        logger.info(f"Load balancing strategy: {self.config.strategy.value}")
        logger.info(f"Configured backends: {[str(b) for b in self.backends]}")
        logger.info(f"Status endpoint: http://{self.config.host}:{self.config.port}{self.config.metrics_path}")
        logger.info(f"Health endpoint: http://{self.config.host}:{self.config.port}/_proxy/health")

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
        await self.connection_pool.__aexit__(None, None, None)


async def main():
    """Example usage of the async reverse proxy"""

    # Load configuration from environment, .env file, or JSON
    config = ProxySettings()

    # If no backends in config, add some examples
    if not config.backends:
        logger.info("No backends found in configuration, adding examples...")
        config.backends = [
            BackendConfig(url="http://localhost:3000", weight=3),
            BackendConfig(url="http://localhost:3001", weight=2),
            BackendConfig(url="http://localhost:3002", weight=1),
        ]

    # Create proxy instance with configuration
    proxy = AsyncReverseProxy(config)

    # Setup signal handlers for graceful shutdown
    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(proxy.stop())

    # Register signal handlers
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda s, f: signal_handler())

    try:
        # Start the proxy
        await proxy.start()

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Error running proxy: {e}")
    finally:
        await proxy.stop()


def create_example_config():
    """Create example configuration files"""

    # Create .env file
    env_content = """# Reverse Proxy Configuration
PROXY_HOST=0.0.0.0
PROXY_PORT=8080
PROXY_LOG_LEVEL=INFO
PROXY_MAX_CONNECTIONS=2000
PROXY_REQUEST_TIMEOUT=60
PROXY_STRATEGY=weighted_round_robin
PROXY_HEALTH_CHECK_INTERVAL=15
PROXY_ACCESS_LOG=true
PROXY_MAX_BODY_SIZE=52428800
"""

    # Create JSON config file
    json_config = {
        "host": "0.0.0.0",
        "port": 8080,
        "log_level": "INFO",
        "max_connections": 2000,
        "request_timeout": 60,
        "strategy": "weighted_round_robin",
        "health_check_interval": 15,
        "backends": [
            {"url": "http://api1.example.com:8000", "weight": 3, "enabled": True},
            {"url": "http://api2.example.com:8000", "weight": 2, "enabled": True},
            {"url": "http://api3.example.com:8000", "weight": 1, "enabled": False},
        ],
        "allowed_hosts": ["example.com", "api.example.com"],
        "enable_metrics": True,
        "access_log": True,
    }

    # Write files if they don't exist
    if not Path(".env").exists():
        with open(".env", "w") as f:
            f.write(env_content)
        print("Created .env file with default configuration")

    if not Path("proxy_config.json").exists():
        with open("proxy_config.json", "w") as f:
            json.dump(json_config, f, indent=2)
        print("Created proxy_config.json with example configuration")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="High-Performance Async Reverse Proxy")
    parser.add_argument("--create-config", action="store_true", help="Create example configuration files")
    parser.add_argument("--config", type=str, help="Path to JSON config file")

    args = parser.parse_args()

    if args.create_config:
        create_example_config()
        sys.exit(0)

    # Override JSON config file if specified
    if args.config:
        ProxySettings.__config__.json_file = args.config
    # Run with high-performance event loop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    asyncio.run(main())
