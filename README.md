functionalities required:
1 - forward http requests from port
2 - return response back to client/server
3 - load balance
    - start with round robin -> could check server average response time to determine if server is over loaded
4 - modify headers
5 - cache
6 - filter requests using rules
7 - logging
8 - enable A/B testing / carnary deployments

-- client -> HTTPS --> TLS termination server (certificates installed) -> HTTP -> Reverse proxy -> Backend server

- Seperate server for TLS for seperation of concerns

-- restarting server -> should port stats of backend servers.
    - when pool of backend servers need to change, we do a A/B swap for proxy server and port the settings to the new proxy server

-- how to bring backend servers back from unhealthy status

1. Client sends HTTP request to host:port
   ↓
2. TCPSite accepts the connection
   ↓
3. AppRunner processes the request
   ↓
4. Your _proxy_handler() is called
   ↓
5. Request is forwarded to backend
   ↓
6. Response is sent back to client

- can add request body validation if needed
