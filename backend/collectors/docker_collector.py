import aiohttp
import ssl
from typing import List, Dict, Any, Optional


def _ip_in_subnet(ip: str, subnet: str) -> bool:
    try:
        base, bits = subnet.split("/")
        bits = int(bits)
        mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
        def to_int(a):
            p = a.split(".")
            return sum(int(x) * (256 ** (3 - i)) for i, x in enumerate(p))
        return (to_int(ip) & mask) == (to_int(base) & mask)
    except Exception:
        return False


# Docker default bridge ranges that are internal-only and not routable
# on the homelab network. Any container IP in these ranges should be
# treated as "bridge container" and reported under the host IP instead.
def _is_docker_internal(ip: str) -> bool:
    """
    Return True if the IP is in a Docker-internal bridge range and not
    routable on a typical homelab network.

    Only 172.16.0.0/12 (172.16.x.x – 172.31.x.x) is reliably "Docker only"
    across all homelab setups. Docker's default bridge is 172.17.0.0/16 and
    custom bridges are allocated from the rest of 172.16/12.

    We intentionally do NOT mark 10.x.x.x or 192.168.x.x as internal because
    those are extremely common homelab address ranges for real hosts and macvlan
    containers. The subnet filter in main.py handles any non-homelab IPs that
    slip through.
    """
    if not ip:
        return True
    try:
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4:
            return False
        # 172.16.0.0/12 = 172.16.x.x through 172.31.x.x
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return True
    except (ValueError, IndexError):
        pass
    return False


def _parse_ports(details: dict) -> List[Dict]:
    """
    Extract published ports from a container inspect response.
    Returns list of {host_port, container_port, protocol, host_ip}

    Docker's PortBindings (in HostConfig) shows what's configured.
    Ports (in NetworkSettings) shows what's actually bound (running containers).
    We use NetworkSettings.Ports as it reflects runtime state.
    """
    ports = []
    net_ports = details.get("NetworkSettings", {}).get("Ports") or {}
    for container_port_proto, bindings in net_ports.items():
        # container_port_proto is like "80/tcp" or "443/tcp"
        try:
            cp, proto = container_port_proto.split("/")
            container_port = int(cp)
        except (ValueError, AttributeError):
            continue
        if not bindings:
            # Port is exposed but not published to host
            continue
        for b in bindings:
            host_port = b.get("HostPort", "")
            host_ip   = b.get("HostIp", "0.0.0.0")
            if host_port:
                ports.append({
                    "port":           int(host_port),
                    "container_port": container_port,
                    "protocol":       proto,
                    "host_ip":        host_ip,
                    "name":           "",   # no name known from Docker
                })
    return sorted(ports, key=lambda x: x["port"])


class DockerCollector:
    """
    Collects container IPs from a Docker host via the Docker API.

    Bridge-network containers: reported with the Docker host's IP since they
    have no routable address of their own. Published ports are extracted from
    the container inspect so no port scan is needed.

    Network mode classification:
      macvlan / ipvlan  → own routable IP, reported directly
      host              → shares host IP, reported under host IP with ports
      bridge (default)  → internal IP only, reported under host IP with ports
      none / other      → no network, still surfaced without IP

    Required socket proxy permissions: CONTAINERS=1, NETWORKS=1 (optional)
    """

    def __init__(self, config: Dict[str, Any], known_subnets: List[str] = None):
        self.name         = config.get("name", config.get("host", "docker"))
        self.host         = config["host"]
        self.port         = int(config.get("port", 2375))
        self.tls          = bool(config.get("tls", False))
        self.ca           = config.get("ca")
        self.cert         = config.get("cert")
        self.key          = config.get("key")
        self.known_subnets = known_subnets or []
        scheme            = "https" if self.tls else "http"
        self.base_url     = f"{scheme}://{self.host}:{self.port}"

    def _is_routable(self, ip: str, driver: str) -> bool:
        """
        Return True if this IP is routable on the homelab network.
        Uses UniFi subnets as source of truth when available.
        macvlan/ipvlan driver always wins — those are explicitly routable by design.
        Falls back to _is_docker_internal heuristic if no subnets are known.
        """
        if not ip:
            return False
        # macvlan/ipvlan = always routable, whatever the IP range
        if driver in ("macvlan", "ipvlan"):
            return True
        # If we have UniFi subnets, use them as the definitive test
        if self.known_subnets:
            return any(_ip_in_subnet(ip, s) for s in self.known_subnets)
        # Fallback: heuristic range check
        return not _is_docker_internal(ip)

    def _connector(self):
        if self.tls:
            ctx = ssl.create_default_context(cafile=self.ca)
            if self.cert and self.key:
                ctx.load_cert_chain(self.cert, self.key)
            return aiohttp.TCPConnector(ssl=ctx)
        return aiohttp.TCPConnector(ssl=False)

    async def collect(self) -> List[Dict[str, Any]]:
        hosts = []
        connector = self._connector()

        async with aiohttp.ClientSession(connector=connector) as session:

            # ── Network metadata (best-effort) ────────────────────────────────
            net_info: Dict[str, Dict] = {}
            networks_raw = await self._get_list(session, "/networks", warn_on_403=False)
            if networks_raw is None:
                print(f"[Docker:{self.name}] /networks blocked by socket proxy — "
                      "add NETWORKS=1 to enable network driver metadata")
            else:
                for net in networks_raw:
                    nid  = net.get("Id", "")
                    ipam = net.get("IPAM", {}).get("Config", [])
                    if nid:
                        net_info[nid] = {
                            "name":   net.get("Name", ""),
                            "driver": net.get("Driver", ""),
                            "subnet": ipam[0].get("Subnet", "") if ipam else "",
                        }

            # ── Pass 1: inspect all containers, collect details ───────────────
            containers = await self._get_list(session, "/containers/json?all=true")
            if not containers:
                return hosts

            # name → {id, ip, details} for service: resolution
            container_details: Dict[str, Dict] = {}
            # pending: containers with network_mode=service:<name> to resolve after pass 1
            pending_service: List[Dict] = []
            container_id_to_name: Dict[str, str] = {}  # id/prefix -> name

            for c in containers:
                cid    = c.get("Id", "")
                cname  = (c.get("Names") or ["unknown"])[0].lstrip("/")
                cstate = c.get("State", "")
                image  = c.get("Image", "")
                if not cid:
                    continue

                # Register full ID and 12-char prefix for service: resolution
                container_id_to_name[cid]      = cname
                container_id_to_name[cid[:12]] = cname

                details = await self._get_dict(session, f"/containers/{cid}/json")
                if not details:
                    continue

                published_ports = _parse_ports(details)
                host_config     = details.get("HostConfig", {})
                net_mode        = host_config.get("NetworkMode", "bridge")
                net_settings    = details.get("NetworkSettings", {}).get("Networks", {})

                # ── service:<name> network mode ───────────────────────────────
                # This container shares another container's network stack.
                # Defer it — we need to resolve the primary container's IP first.
                if net_mode.startswith("service:"):
                    primary_name = net_mode[len("service:"):]
                    pending_service.append({
                        "cid": cid, "cname": cname, "cstate": cstate,
                        "image": image, "net_mode": net_mode,
                        "primary_name": primary_name,
                        "published_ports": published_ports,
                    })
                    continue

                # ── host network mode ─────────────────────────────────────────
                if net_mode == "host":
                    hosts.append(self._make(
                        ip=None, cname=cname, image=image, cstate=cstate,
                        cid=cid, network="host", driver="host",
                        ports=published_ports,
                        extra={"network_mode": "host", "host_ip": self.host},
                    ))
                    container_details[cname] = {"ip": None, "cstate": cstate}
                    continue

                # ── Standard network attachments ──────────────────────────────
                added = False
                resolved_ip = None
                for net_name, net_data in net_settings.items():
                    raw_ip = net_data.get("IPAddress", "")
                    net_id = net_data.get("NetworkID", "")
                    ni     = net_info.get(net_id, {})
                    driver = ni.get("driver", "")

                    is_routable_ip = self._is_routable(raw_ip, driver)

                    if is_routable_ip:
                        resolved_ip = raw_ip
                        hosts.append(self._make(
                            ip=raw_ip, cname=cname, image=image, cstate=cstate,
                            cid=cid, network=net_name, driver=driver,
                            ports=published_ports,
                            extra={
                                "network_mode":   net_mode,
                                "network_driver": driver,
                                "macvlan":        driver == "macvlan",
                            },
                        ))
                        added = True
                    else:
                        hosts.append(self._make(
                            ip=None, cname=cname, image=image, cstate=cstate,
                            cid=cid, network=net_name, driver=driver or "bridge",
                            ports=published_ports,
                            extra={
                                "network_mode":       net_mode,
                                "network_driver":     driver or "bridge",
                                "bridge_on_host":     True,
                                "host_ip":            self.host,
                                "internal_bridge_ip": raw_ip,
                            },
                        ))
                        added = True

                if not added:
                    hosts.append(self._make(
                        ip=None, cname=cname, image=image, cstate=cstate,
                        cid=cid, network=net_mode, driver="",
                        ports=[],
                        extra={"network_mode": net_mode},
                    ))

                container_details[cname] = {"ip": resolved_ip, "cstate": cstate}

            # ── Pass 2: resolve service: network_mode containers ─────────────
            # These containers share their primary's IP. We surface them as
            # separate rows under that IP with their own ports noted.
            for p in pending_service:
                # Docker stores service: references as container ID at runtime,
                # not as the compose service name. Resolve via id_to_name first.
                raw_ref = p["primary_name"]
                resolved_name = container_id_to_name.get(raw_ref, raw_ref)
                primary = container_details.get(resolved_name, {})
                primary_ip = primary.get("ip")  # may be None for bridge primaries

                hosts.append(self._make(
                    ip=primary_ip,
                    cname=p["cname"],
                    image=p["image"],
                    cstate=p["cstate"],
                    cid=p["cid"],
                    network=p["net_mode"],
                    driver="service",
                    ports=p["published_ports"],
                    extra={
                        "network_mode":    p["net_mode"],
                        "network_driver":  "service",
                        "service_primary": p["primary_name"],
                        "primary_ip":      primary_ip or "",
                    },
                ))

        return hosts

    def _make(self, *, ip, cname, image, cstate, cid, network, driver, ports, extra) -> Dict:
        running = cstate == "running"
        return {
            "ip":                 ip,
            "hostname":           cname,
            "mac":                "",
            "network":            network,
            "vlan":               None,
            "sources":            [f"docker:{self.name}"],
            "type":               "container",
            "online":             running,
            "online_authoritative": running,   # don't let ping overwrite this
            "ports":              ports,
            "extra": {
                "docker_host":  self.name,
                "container_id": cid[:12],
                "image":        image,
                "state":        cstate,
                **extra,
            },
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get_list(self, session, path: str, warn_on_403: bool = True) -> Optional[List]:
        url = f"{self.base_url}{path}"
        try:
            resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=10))
            if resp.status == 403:
                if warn_on_403:
                    print(f"[Docker:{self.name}] 403 on {path} — blocked by socket proxy")
                return None
            if resp.status != 200:
                print(f"[Docker:{self.name}] HTTP {resp.status} on {path}")
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[Docker:{self.name}] GET {path} failed: {e}")
            return []

    async def _get_dict(self, session, path: str) -> Optional[Dict]:
        url = f"{self.base_url}{path}"
        try:
            resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=10))
            if resp.status == 403:
                print(f"[Docker:{self.name}] 403 on {path} — blocked by socket proxy")
                return {}
            if resp.status != 200:
                print(f"[Docker:{self.name}] HTTP {resp.status} on {path}")
                return {}
            data = await resp.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[Docker:{self.name}] GET {path} failed: {e}")
            return {}
