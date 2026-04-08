import asyncio
import platform
import re
import time
from typing import Dict, List, Any


class PingChecker:
    def __init__(self, timeout: float = 2.0, concurrent: int = 50):
        self.timeout = timeout
        self.concurrent = concurrent

    async def ping_one(self, ip: str) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            # Use -W for timeout on Linux, -t on macOS
            is_linux = platform.system() == "Linux"
            if is_linux:
                cmd = ["ping", "-c", "1", "-W", str(int(self.timeout)), ip]
            else:
                cmd = ["ping", "-c", "1", "-t", str(int(self.timeout)), ip]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout + 1)
            except asyncio.TimeoutError:
                proc.kill()
                return {"online": False, "latency_ms": None, "ip": ip}

            elapsed = (time.monotonic() - start) * 1000
            output = stdout.decode()

            if proc.returncode == 0:
                # Try to extract RTT from output
                latency = self._parse_rtt(output) or round(elapsed, 2)
                return {"online": True, "latency_ms": latency, "ip": ip}
            else:
                return {"online": False, "latency_ms": None, "ip": ip}

        except Exception as e:
            return {"online": False, "latency_ms": None, "ip": ip, "error": str(e)}

    def _parse_rtt(self, output: str) -> float | None:
        # Match: time=1.23 ms or time=1.23ms
        match = re.search(r"time[<=]([\d.]+)\s*ms", output)
        if match:
            return round(float(match.group(1)), 2)
        return None

    async def ping_all(self, ips: List[str]) -> Dict[str, Dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.concurrent)
        results = {}

        async def bounded_ping(ip):
            async with semaphore:
                result = await self.ping_one(ip)
                results[ip] = result

        tasks = [bounded_ping(ip) for ip in ips]
        await asyncio.gather(*tasks)
        return results
