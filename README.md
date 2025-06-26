# Reverse Proxy Server

My first attempt at building an asynchronous reverse proxy server with Python using aiohttp. This proxy provides load balancing, health checking, request forwarding, and monitoring capabilities for HTTP services.

## Desired Functionalities

1. Forward HTTP requests from port ✅
2. Stream response body + headers back to client/server ✅
3. Stateless load balance strategies
   - least connections ✅
   - fastest_avg_response ✅
   - random ✅
4. Modify headers - partially, capability is there
5. Cache - TODO: Response caching
6. Logging ✅
7. Enable A/B testing / canary deployments - TODO: rule based traffic routing

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Installation

From the root dir of the project, you may follow the below steps:

1. **Set up venv:**

   ```bash
   uv venv
   ```

2. **Install dependencies:**

   ```bash
   uv sync
   ```

3. **Activate venv (optional if you use uv run)**

   ```bash
   source .venv/bin/activate
   ```

4. **Set up environment variables:**
   This project uses a `.env` file & a `.secret` file to store environment variables and secrets.

   For the purpose of this demo, everything is already set and can be found in the `/etc` folder.

5. **Start backend servers (for testing):**

   ```bash
   uv run python -m tests.fastapi_server
   ```

   This will start up 4 test fastapi servers and they will be running in your current terminal panel.

6. **Run the reverse proxy:**

   ```bash
   uv run ./run_server.sh
   ```

   Running this script will start an aiohttp webserver with the reverse proxy functionalities built in. The server will run on your current terminal panel. You can run curl commands to the `/_proxy/health` & `/_proxy/status` endpoints regardless of whether or not downstream servers are avaiable.

   e.g.

   ```bash
   curl -H "Authorization: Bearer secretkey123" localhost:8000/_proxy/status
   ```

   ```bash
   curl localhost:8000/_proxy/health_check
   ```

### Logging

The reverse proxy server logs to both stdout and a file. The file is located at `logs/reverse_proxy.log`. By default, streaming to stdout is disabled. This can be enabled by setting the `STREAM_STDOUT` environment variable to `true` in the `.env` file.

### Running Tests

#### Integration Tests

The main test suite runs a complete integration test that:

1. **Starts Test Infrastructure**:
   - Launches 4 FastAPI backend servers on ports 8001-8004
   - Starts the reverse proxy server on port 8000
   - Waits for all services to be healthy

2. **Runs Functional Tests**:
   - Basic HTTP methods (GET, POST)
   - Health endpoint verification
   - Error handling (404, 503 responses)
   - Large response handling
   - Slow endpoint testing

3. **Load Balancing Verification**:
   - Sends 5000 concurrent requests
   - Verifies requests are distributed across backend servers
   - Reports load distribution statistics

4. **Cleanup**:
   - Gracefully terminates all test processes
   - Provides detailed test results and summary

Tear any running servers (dummy or reverse proxy) before running the integration tests with the following command:

```bash
uv run python -m tests.run_integration_tests
```

#### Test Coverage

The integration tests cover:

- ✅ **Basic Functionality**: HTTP request/response forwarding
- ✅ **Load Balancing**: Distribution across multiple backends
- ✅ **Health Checking**: Backend server health monitoring
- ✅ **Error Handling**: Graceful handling of backend failures
- ✅ **Concurrent Requests**: High-load scenarios
- ✅ **Response Streaming**: Large payload handling
- ✅ **Admin Endpoints**: Status and health check endpoints

#### Test Configuration

The integration tests use the same configuration as the main application:

- Backend server ports: 8001, 8002, 8003, 8004
- Reverse proxy port: 8000
- Health check intervals and timeouts from settings
- Load balancing strategies from configuration

#### Performance Testing

The current test suite includes basic performance testing through:

- Concurrent request handling (5000 simultaneous requests)
- Load balancing verification
- Response time measurement

TODO:

- Fix bug with load distribution average response time display
- Testing with larger payloads
- Measuring memory usage and connection pooling
- Stress testing with thousands of concurrent connections

## Resources Used to Build This Implementation

### Core Technologies

- **aiohttp**: High-performance async HTTP client/server framework
- **Pydantic**: Data validation and settings management
- **asyncio**: Python's built-in async programming library
- **pydantic-settings**: Environment-based configuration management
- **FastAPI**: Used for test backend servers

### Development Tools

- **uv**: Fast Python package manager and installer
- **mypy**: Static type checking
- **ruff**: Fast Python linter and formatter

### Resources Used

Lots of green tea  + googling + claude sonnet 4 & gemini 2.5-pro

## Design Decisions and Limitations

### Design Decisions

1. **Async-First Architecture**: Built entirely with asyncio for high concurrency and non-blocking I/O
2. **Connection Pooling**: Dedicated connection pools for main requests and health checks to optimize resource usage
3. **Streaming Responses**: Responses are streamed chunk-by-chunk to handle large payloads efficiently
4. **Stateless Load Balancing**: Load balancer doesn't maintain state, making it horizontally scalable
5. **Modular Design**: Separated concerns into distinct modules (health checker, load balancer, connection pool, etc.)
6. **Environment-Based Configuration**: All settings configurable via environment variables for containerization
7. **Comprehensive Logging**: Structured logging with both file and stdout output options

### Load Balancing Strategies

- **Least Connections**: Routes to backend with fewest active connections
- **Fastest Average Response**: Routes to backend with lowest average response time
- **Random**: Simple random selection for basic load distribution

NOTE: I only implemented stateless load balancing strategies. This is because I wanted to avoid async locking when having to increment an index for strategies like round robin or weighted round robin.

### Health Checking

- Configurable intervals and timeouts
- Retry logic with exponential backoff
- Separate connection pool for health checks
- Graceful handling of backend failures

### Limitations

1. **No Caching**: Currently no request/response caching implemented.
   - I would like to implement caching for same requests coming in from the same client. Maybe a hash of a request body + headers + client ip using reddis to avoid clogging up memory with LRU cache - there are performance considerations here.
2. **No Request Filtering**: No rule-based request filtering system
3. **No A/B Testing**: Canary deployment features not yet implemented
4. **No TLS Termination**: Requires separate TLS termination server -> intentional design decision, i think seperation of concerns here is good.
5. **No Rate Limiting**: No built-in rate limiting capabilities
6. **No Metrics Export**: No Prometheus/OpenTelemetry metrics
7. **Single Instance**: Not sure if this scales well horizontally
8. **Large files**: In theory shouldn't be a problem, but I haven't tested it yet
9. **No Docker support**: Didnt have time
10. **Memory bounding total connections**: In `/utils/backend_server.py`, there is a `total_requests` variable that is incremented for each request. Need to bound or rollover to prevent overflow.
11. **Dynamic backend server discovery**: Need to implement a way to re-discover backend servers when they are added or removed without having to restart the proxy server.

NOTE: Python is also not the best language to write a reverse proxy server in. I would have preferred to use a more performant language like Rust, but Rust is hard and I havent used it in over a year :).

### Horizontal Scaling

1. **Multiple Proxy Instances**: Run multiple proxy instances behind a load balancer
2. **Database-Backed State**: Use Redis/PostgreSQL to share backend health state across instances
3. **Service Discovery**: Integrate with Consul/etcd for dynamic backend discovery

## Security Enhancements

### Current Security Features

- **Host Filtering**: Configurable allowed hosts
- **Method Validation**: Only allows standard HTTP methods
- **Admin Authentication**: Protected status endpoints with secret token
- **Request Size Limits**: Configurable maximum body size

### Recommended Security Improvements

1. **TLS/SSL Support to a server before hitting the reverse proxy**:

2. **Rate Limiting**:
   - Per-client rate limiting
   - IP-based throttling
   - Burst protection

3. **Security Headers**:
   - Automatic security header injection
   - CORS configuration
   - Content Security Policy (CSP)

## Tools and Resources Used

### Programming Tools

- **Cursor IDE**: Primary development environment with AI assistance
- **Git**: Version control and collaboration

### Development Workflow

- **Type Hints**: Comprehensive type annotations for better code quality
- **Linting**: ruff for code quality and consistency
- **Testing**: Integration tests with FastAPI test servers
- **Documentation**: Inline docstrings and comprehensive README

## Considerations

**Server Restart Strategy:**

- When restarting server → should port stats of backend servers
- When pool of backend servers need to change, we do an A/B swap for proxy server and port the settings to the new proxy server
-> Should record stats in persistent storage like db or redis to avoid losing stats when proxy server restarts

**Health Recovery:**

- Backend servers can be brought back from unhealthy status through health checks -> Need strategy for this

### Request Flow

1. Client sends HTTP request to host:port
   ↓
2. TCPSite accepts the connection
   ↓
3. AppRunner processes the request
   ↓
4. `_proxy_handler()` is called
   ↓
5. Request is forwarded to backend
   ↓
6. Response is sent back to client
