import asyncio
from typing import Dict, List

# Common homelab ports to check
COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 443, 
    631, 993, 995,
    1883, 2375, 2376, 2379, 2380,
    3000, 3001, 3306, 3389, 
    4443, 4789,
    5000, 5001, 5432, 5900, 5901,
    6443, 6881,
    7474, 8006, 8080, 8086, 8088, 8096, 8123, 8200, 8443, 8444, 8448, 8880, 8888, 8889,
    9000, 9001, 9090, 9091, 9100, 9200, 9443,
    10250, 10443,
    15672, 
    27017, 27018,
    32400,
    51820,
]

PORT_NAMES: Dict[int, str] = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 443: "HTTPS",
    631: "IPP", 993: "IMAPS", 995: "POP3S",
    1883: "MQTT", 2375: "Docker", 2376: "Docker-TLS",
    2379: "etcd", 2380: "etcd-peer",
    3000: "Grafana/App", 3001: "App", 3306: "MySQL", 3389: "RDP",
    4443: "HTTPS-alt", 4789: "VXLAN",
    5000: "App", 5001: "App", 5432: "PostgreSQL",
    5900: "VNC", 5901: "VNC-1",
    6443: "K8s API", 6881: "BitTorrent",
    7474: "Neo4j",
    8006: "Proxmox", 8080: "HTTP-alt", 8086: "InfluxDB",
    8088: "App", 8096: "Jellyfin", 8123: "Home Assistant",
    8200: "Vault", 8443: "HTTPS-alt", 8444: "HTTPS-alt",
    8448: "Matrix", 8880: "HTTP-alt", 8888: "App", 8889: "App",
    9000: "Portainer/App", 9001: "App", 9090: "Prometheus",
    9091: "Transmission", 9100: "Node Exporter", 9200: "Elasticsearch",
    9443: "Portainer-TLS",
    10250: "Kubelet", 10443: "App",
    15672: "RabbitMQ",
    27017: "MongoDB", 27018: "MongoDB",
    32400: "Plex",
    51820: "WireGuard",
}


async def scan_ports(ip: str, ports: List[int] = None, timeout: float = 0.5, concurrency: int = 50) -> List[Dict]:
    """
    Async TCP connect scan. Returns list of open port dicts.
    Does NOT do banner grabbing — just checks if the port accepts a connection.
    """
    if ports is None:
        ports = COMMON_PORTS

    semaphore = asyncio.Semaphore(concurrency)
    open_ports = []

    async def check(port: int):
        async with semaphore:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=timeout,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                open_ports.append({
                    "port": port,
                    "name": PORT_NAMES.get(port, ""),
                    "protocol": "tcp",
                })
            except Exception:
                pass

    await asyncio.gather(*[check(p) for p in ports])
    return sorted(open_ports, key=lambda x: x["port"])
