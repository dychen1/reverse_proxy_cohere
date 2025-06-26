"""
Integration test script for the reverse proxy server.
This script starts the FastAPI backend servers, the reverse proxy server,
and then runs a series of tests to verify everything is working correctly.
"""

import asyncio
import multiprocessing
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from src import TEST_SERVER_PORTS
from src.reverse_proxy_server import main as reverse_proxy_server
from src.settings import settings
from tests.fastapi_server import run_server


def run_async_main():
    """Wrapper to run the async main function in a new event loop."""
    asyncio.run(reverse_proxy_server())


class IntegrationTestRunner:
    def __init__(self) -> None:
        self.reverse_proxy_process: Optional[multiprocessing.Process] = None
        self.backend_processes: List[multiprocessing.Process] = []
        self.test_results: List[Dict] = []

    async def start_reverse_proxy(self) -> None:
        """Start the reverse proxy server in the background."""
        print("🚀 Starting reverse proxy server...")

        self.reverse_proxy_process = multiprocessing.Process(target=run_async_main)
        self.reverse_proxy_process.start()

        # Wait a bit for the server to start
        await asyncio.sleep(1)

        # Check if the process is still running
        if not self.reverse_proxy_process.is_alive():
            raise RuntimeError("Reverse proxy failed to start")

        print("✅ Reverse proxy server started successfully")

    def start_backend_servers(self) -> None:
        """Start the FastAPI backend servers."""
        print("🚀 Starting FastAPI backend servers...")

        for port in TEST_SERVER_PORTS:
            process = multiprocessing.Process(target=run_server, args=(port,))
            self.backend_processes.append(process)
            print(f"Starting backend server on port {port}...")
            process.start()
            time.sleep(1)  # Give each server time to start

        print("✅ Backend servers started successfully")

    async def wait_for_servers(self) -> None:
        """Wait for all servers to be ready."""
        print("⏳ Waiting for servers to be ready...")

        # Wait for backend servers
        for port in [8001, 8002, 8003, 8004]:
            for _ in range(30):  # Wait up to 30 seconds
                try:
                    timeout = aiohttp.ClientTimeout(total=5)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(f"http://localhost:{port}/health") as response:
                            if response.status == 200:
                                print(f"✅ Backend server on port {port} is ready")
                                break
                except:
                    await asyncio.sleep(1)
            else:
                raise RuntimeError(f"Backend server on port {port} failed to start")

        # Wait for reverse proxy
        for _ in range(30):  # Wait up to 30 seconds
            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"http://{settings.host}:{settings.port}/_proxy/health") as response:
                        if response.status == 200:
                            print("✅ Reverse proxy is ready")
                            break
            except:
                await asyncio.sleep(1)
        else:
            raise RuntimeError("Reverse proxy failed to start")

    async def _run_test(
        self,
        name: str,
        method: str,
        path: str,
        expected_status: int = 200,
        data: Optional[Dict] = None,
        timeout: int = 10,
        display: bool = True,
    ) -> Dict:
        """Run a single test against the reverse proxy."""
        if display:
            print(f"🧪 Running test: {name}")

        url = f"http://{settings.host}:{settings.port}{path}"
        start_time = time.monotonic()

        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                if method.upper() == "GET":
                    async with session.get(url) as response:
                        status = response.status
                        response_text = await response.text()
                        try:
                            response_data = await response.json()
                        except Exception:
                            response_data = response_text
                elif method.upper() == "POST":
                    async with session.post(url, json=data) as response:
                        status = response.status
                        response_text = await response.text()
                        try:
                            response_data = await response.json()
                        except Exception:
                            response_data = response_text
                else:
                    raise ValueError(f"Unsupported method: {method}")

            duration = time.monotonic() - start_time
            success = status == expected_status

            result = {
                "name": name,
                "method": method,
                "url": url,
                "status": status,
                "expected_status": expected_status,
                "success": success,
                "duration": duration,
                "response": response_data,
            }

            if success:
                if display:
                    print(f"✅ {name} - PASSED ({duration:.3f}s)")
            else:
                print(f"❌ {name} - FAILED (expected {expected_status}, got {status})")

            return result

        except Exception as e:
            duration = time.monotonic() - start_time
            result = {
                "name": name,
                "method": method,
                "url": url,
                "status": None,
                "expected_status": expected_status,
                "success": False,
                "duration": duration,
                "error": str(e),
            }
            print(f"❌ {name} - ERROR: {e}")
            return result

    async def run_all_tests(self) -> None:
        """Run all integration tests concurrently."""
        print("\n🧪 Starting integration tests...")

        tests: List[Tuple[Any, ...]] = [
            # Basic functionality tests
            ("GET Root", "GET", "/", 200),
            ("POST Root", "POST", "/", 200, {"test": "data"}),
            ("GET Health", "GET", "/health", 200),
            # Load balancing tests (multiple requests to see if they hit different servers)
            ("Load Balance Test 1", "GET", "/", 200),
            ("Load Balance Test 2", "GET", "/", 200),
            ("Load Balance Test 3", "GET", "/", 200),
            ("Load Balance Test 4", "GET", "/", 200),
            ("Load Balance Test 5", "GET", "/", 200),
            ("Load Balance Test 6", "GET", "/", 200),
            ("Load Balance Test 7", "GET", "/", 200),
            ("Load Balance Test 8", "GET", "/", 200),
            ("Load Balance Test 9", "GET", "/", 200),
            ("Load Balance Test 10", "GET", "/", 200),
            # Special endpoint tests
            ("Slow Endpoint", "GET", "/slow", 200),
            ("Large Response", "GET", "/large", 200),
            # Error handling tests
            ("Error Endpoint", "GET", "/error", 503),
            ("Not Found", "GET", "/nonexistent", 404),
        ]

        tasks = []
        for test in tests:
            if len(test) == 4:
                name, method, path, expected_status = test
                tasks.append(self._run_test(str(name), str(method), str(path), int(expected_status)))
            elif len(test) == 5:
                name, method, path, expected_status, data = test
                tasks.append(self._run_test(str(name), str(method), str(path), int(expected_status), data))
            else:
                raise ValueError(f"Invalid test format: {test}")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"❌ Test task failed with an exception: {result}")
            else:
                self.test_results.append(result)

    async def verify_load_balancing(self, num_requests: int = settings.test_num_requests) -> bool:
        """
        Sends a burst of requests to verify that they are distributed
        across multiple backend servers.
        """
        print(f"\n🔬 Verifying load balancing with {num_requests} requests...")

        tasks = [self._run_test(f"LB Check {i + 1}", "GET", "/", 200, display=False) for i in range(num_requests)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        server_hits: dict[str, int] = {}
        server_response_times: dict[str, list[float]] = {}
        successful_requests = 0
        for res in results:
            if isinstance(res, dict) and res.get("success"):
                successful_requests += 1
                response_data = res.get("response", {})
                duration = res.get("duration", 1000)
                if isinstance(response_data, dict):
                    server_id = response_data.get("server_id")
                    if server_id:
                        server_hits[server_id] = server_hits.get(server_id, 0) + 1
                        server_response_times.setdefault(server_id, []).append(duration)

        if not server_hits:
            print("❌ Load balancing verification failed: No successful requests with server_id found.")
            return False

        print("📊 Load Distribution:")
        for server, count in sorted(server_hits.items()):
            percentage = (count / successful_requests) * 100
            avg_response_time = (
                sum(server_response_times[server]) / len(server_response_times[server])
                if server_response_times[server]
                else 0.0
            )
            print(f"  - {server}: {count} hits ({percentage:.1f}%) - Avg: {avg_response_time:.3f}s")

        unique_servers_hit = len(server_hits)
        total_backends = len(self.backend_processes)

        print(f"✅ Hit {unique_servers_hit} out of {total_backends} unique backend servers.")

        # For a small number of backends (like 4), we should ideally hit all of them.
        if unique_servers_hit >= total_backends * 0.75:
            print("✅ Load distribution looks reasonable.")
            return True
        else:
            print("❌ Load distribution seems poor. Too few backends were hit.")
            return False

    def _print_summary(self) -> None:
        """Print a summary of test results."""
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)

        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results if result["success"])
        failed_tests = total_tests - passed_tests

        print(f"Total tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {failed_tests}")
        print(f"Success rate: {(passed_tests / total_tests) * 100:.1f}%")

        if failed_tests > 0:
            print("\n❌ Failed tests:")
            for result in self.test_results:
                if not result["success"]:
                    print(f"  - {result['name']}: {result.get('error', f'Status {result.get("status")}')}")

        print("\n📋 Detailed Results:")
        for result in self.test_results:
            status_icon = "✅" if result["success"] else "❌"
            print(f"{status_icon} {result['name']}: {result.get('status', 'ERROR')} ({result['duration']:.3f}s)")

    def cleanup(self) -> None:
        """Clean up all running processes."""
        print("\n🧹 Cleaning up...")

        # Stop backend servers
        for process in self.backend_processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()

        # Stop reverse proxy
        if self.reverse_proxy_process:
            self.reverse_proxy_process.terminate()
            try:
                self.reverse_proxy_process.join(timeout=5)
            except multiprocessing.TimeoutError:
                self.reverse_proxy_process.kill()

        print("✅ Cleanup completed")

    async def run(self) -> None:
        """Run the complete integration test suite."""
        try:
            # Start servers
            self.start_backend_servers()
            await self.start_reverse_proxy()
            await self.wait_for_servers()

            # Run tests
            await self.run_all_tests()

            # Verify load balancing
            lb_success = await self.verify_load_balancing()

            # Print results
            self._print_summary()

            # Determine exit code
            failed_tests = sum(1 for result in self.test_results if not result["success"])
            if failed_tests > 0 or not lb_success:
                print(
                    f"\n❌ Integration tests failed: {failed_tests} functional test(s) failed. "
                    f"Load balancing check: {'PASSED' if lb_success else 'FAILED'}"
                )
                sys.exit(1)
            else:
                print("\n🎉 All integration tests passed!")
                sys.exit(0)

        except Exception as e:
            print(f"\n💥 Integration test failed with error: {e}")
            sys.exit(1)
        finally:
            self.cleanup()


async def main() -> None:
    """Main entry point."""
    print("🚀 Starting Reverse Proxy Integration Tests")
    print("=" * 60)

    runner = IntegrationTestRunner()
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
