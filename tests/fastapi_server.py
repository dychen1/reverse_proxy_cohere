import asyncio
import multiprocessing
import random
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src import TEST_SERVER_PORTS

# Global variable to store the current server port
current_server_port: int = 0

app = FastAPI()


def create_message(request: Request) -> str:
    "Helper function to create the response message."
    method = request.method
    host = request.url.hostname
    port = request.url.port

    return f"hello: {method} - {host}:{port}"


@app.get("/health")
async def health():
    return {"status": "healthy", "server_id": current_server_port}


@app.get("/")
async def root(request: Request):
    return {"message": create_message(request), "timestamp": time.time(), "server_id": current_server_port}


@app.post("/")
async def echo(request: Request):
    request_data = await request.json()
    return {"method": "POST", "data": request_data, "server_id": current_server_port}


@app.get("/slow")
async def slow_endpoint():
    await asyncio.sleep(random.uniform(0.5, 2.0))
    return {"message": "Slow response", "server_id": current_server_port}


@app.get("/error")
async def error_endpoint():
    return JSONResponse(content={"error": "Service unavailable"}, status_code=503)


@app.get("/large")
async def large_response():
    large_data = {"data": "x" * 10000}
    return {"message": large_data, "server_id": current_server_port}


def run_server(port: int):
    global current_server_port
    current_server_port = port
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    processes = []

    for port in TEST_SERVER_PORTS:
        process = multiprocessing.Process(target=run_server, args=(port,))
        processes.append(process)
        print(f"Starting server on port {port}...")
        process.start()
        print("Server started")
        time.sleep(1)

    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
