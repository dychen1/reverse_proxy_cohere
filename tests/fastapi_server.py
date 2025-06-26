import asyncio
import multiprocessing
import random
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src import TEST_SERVER_PORTS

app = FastAPI()


def create_message(request: Request) -> str:
    "Helper function to create the response message."
    method = request.method
    host = request.url.hostname
    port = request.url.port

    return f"hello: {method} - {host}:{port}"


@app.get("/health")
async def health():
    print()
    return {"status": "healthy"}


@app.get("/")
async def root(request: Request):
    print("get request")
    return {"message": create_message(request), "timestamp": time.time(), "server_id": "test-server"}


@app.post("/")
async def echo(request: Request):
    print("post request")
    request_data = await request.json()
    return {"method": "POST", "data": request_data, "server_id": "test-server"}


@app.get("/slow")
async def slow_endpoint():
    print("slow request")
    await asyncio.sleep(random.uniform(0.5, 2.0))
    return {"message": "Slow response"}


@app.get("/error")
async def error_endpoint():
    print("error request")
    return JSONResponse(content={"error": "Service unavailable"}, status_code=503)


@app.get("/large")
async def large_response():
    print("large request")
    large_data = {"data": "x" * 10000}
    return large_data


def run_server(port: int):
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
