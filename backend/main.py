from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Any

from .database import get_engine, ConfigStore
from .collectors.unifi import UnifiCollector
from .collectors.docker_collector import DockerCollector
from .collectors.proxmox import ProxmoxCollector
from .collectors.ping import PingChecker
from .collectors.portscan import scan_ports, COMMON_PORTS

HIM_VERSION = "1.19"

def _ip_to_int(ip: str) -> int:
    p = ip.split(".")
    return sum(int(x) * (256 ** (3 - i)) for i, x in enumerate(p))

def _int_to_ip(n: int) -> str:
    return ".".join(str((n >> (8 * i)) & 0xFF) for i in range(3, -1, -1))

def _ip_in_subnet(ip: str, subnet: str) -> bool:
    try:
        base, bits = subnet.split("/")
        bits = int(bits)
        mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
        return (_ip_to_int(ip) & mask) == (_ip_to_int(base) & mask)
    except Exception:
        return False

app = FastAPI(title="HIM - Homelab IP Monitor")

# ── Optional Basic Auth ───────────────────────────────────────────────────────
_AUTH_USER = os.getenv("HIM_USERNAME", "").strip()
_AUTH_PASS = os.getenv("HIM_PASSWORD", "").strip()
_AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)

if _AUTH_ENABLED:
    _EXPECTED = base64.b64encode(f"{_AUTH_USER}:{_AUTH_PASS}".encode()).decode()

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # Allow static assets through without auth (needed for login page to load)
            if request.url.path.startswith("/assets/"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Basic ") and auth[6:] == _EXPECTED:
                return await call_next(request)
            return Response(
                content="Unauthorised",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="HIM"'},
            )

    app.add_middleware(BasicAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = get_engine()
store = ConfigStore(engine)

state: dict[str, Any] = {
    "hosts": [],
    "vlans": [],
    "last_updated": None,
    "scanning": False,
    "errors": [],
}

async def collect_all():
    state["scanning"] = True
    state["errors"] = []
    all_hosts = []

    unifi_cfg = store.get_unifi_config()
    if unifi_cfg["host"]:
        try:
            uc = UnifiCollector(unifi_cfg)
            data = await uc.collect()
            all_hosts.extend(data["hosts"])
            state["vlans"] = data["vlans"]
        except Exception as e:
            state["errors"].append(f"UniFi: {e}")

    # UniFi runs first — its subnets become the source of truth for
    # what's "routable" when Docker collectors run below.
    known_subnets = [v["subnet"] for v in state.get("vlans", []) if v.get("subnet")]

    for dhost in store.get_docker_hosts():
        try:
            dc = DockerCollector(dhost, known_subnets=known_subnets)
            hosts = await dc.collect()
            all_hosts.extend(hosts)
        except Exception as e:
            state["errors"].append(f"Docker:{dhost['name']}: {e}")

    for phost in store.get_proxmox_hosts():
        try:
            pc = ProxmoxCollector(phost)
            hosts = await pc.collect()
            all_hosts.extend(hosts)
        except Exception as e:
            state["errors"].append(f"Proxmox:{phost['name']}: {e}")

    ip_map: dict[str, dict] = {}
    for h in all_hosts:
        ip    = h.get("ip")
        htype = h.get("type", "")
        name  = h.get("hostname", "")
        source = (h.get("sources") or ["unknown"])[0]

        # No IP — stable key so stopped VMs / no-port containers don't duplicate
        if not ip:
            vmid = h.get("extra", {}).get("vmid", "")
            cid  = h.get("extra", {}).get("container_id", "")
            ip_map[f"__noip_{source}_{name}_{vmid}{cid}"] = h
            continue

        # Containers ALWAYS get their own row, never merged with anything else.
        # A Docker container and a Proxmox LXC at the same IP are distinct entries.
        # Bridge/host containers intentionally share the Docker host's IP with each
        # other and with the host record — each still needs its own row.
        if htype == "container":
            cid = h.get("extra", {}).get("container_id", "")
            ip_map[f"__container_{source}_{name}_{cid}"] = h
            continue

        # Non-container hosts: merge by IP (UniFi client + Proxmox LXC = same physical host)
        if ip in ip_map:
            existing      = ip_map[ip]
            existing_type = existing.get("type", "")
            existing["sources"] = list(set(existing.get("sources", []) + h.get("sources", [])))
            # Higher-priority type wins over generic "client"
            type_priority = {"proxmox-node": 5, "vm": 4, "lxc": 4,
                             "gateway": 3, "switch": 3, "access-point": 3,
                             "network-device": 3, "client": 1}
            if type_priority.get(htype, 0) > type_priority.get(existing_type, 0):
                existing["type"]          = htype
                existing["hostname"]      = h.get("hostname") or existing.get("hostname")
                existing["ip_assignment"] = h.get("ip_assignment") or existing.get("ip_assignment")
                existing["extra"]         = {**existing.get("extra", {}), **h.get("extra", {})}
            if h.get("ports") and not existing.get("ports"):
                existing["ports"] = h["ports"]
        else:
            ip_map[ip] = h

    state["hosts"] = list(ip_map.values())

    # Filter to only IPs within known UniFi subnets. Since the Docker collector
    # already uses UniFi subnets as source of truth for routable vs internal,
    # this mainly catches any remaining noise (link-local, etc.)
    if known_subnets:
        def in_any_subnet(h):
            ip = h.get("ip")
            if not ip:
                return True   # keep no-IP hosts
            if h.get("type") == "container":
                extra = h.get("extra", {})
                if extra.get("bridge_on_host") or extra.get("network_mode") == "host":
                    return True   # host IP, already routable
            for subnet in known_subnets:
                if _ip_in_subnet(ip, subnet):
                    return True
            return False
        state["hosts"] = [h for h in state["hosts"] if in_any_subnet(h)]

    state["last_updated"] = time.time()
    state["scanning"] = False
    asyncio.create_task(run_pings())

async def run_pings():
    """
    Only ping hosts whose online status cannot be determined from the source.
    Hosts with online_authoritative=True (running Docker containers, running
    Proxmox VMs/LXCs) are skipped — their state is definitively known and
    pinging bridge-network containers would always return false negatives.
    """
    pingable = []
    for h in state["hosts"]:
        ip = h.get("ip")
        if not ip:
            continue
        if h.get("online_authoritative"):
            # Source says running — trust it, don't ping
            continue
        pingable.append(ip)

    if not pingable:
        return

    checker = PingChecker()
    results = await checker.ping_all(pingable)
    for h in state["hosts"]:
        ip = h.get("ip")
        if ip and ip in results and not h.get("online_authoritative"):
            h["online"]     = results[ip]["online"]
            h["latency_ms"] = results[ip].get("latency_ms")

async def periodic_refresh():
    while True:
        try:
            interval = int(store.get("refresh_interval", "300"))
        except Exception:
            interval = 300
        await collect_all()
        await asyncio.sleep(interval)

@app.on_event("startup")
async def startup():
    asyncio.create_task(periodic_refresh())

@app.get("/api/hosts")
async def get_hosts():
    return {
        "hosts":        state["hosts"],
        "vlans":        state["vlans"],
        "last_updated": state["last_updated"],
        "scanning":     state["scanning"],
        "errors":       state["errors"],
        "total":        len(state["hosts"]),
        "online":       sum(1 for h in state["hosts"] if h.get("online")),
        "offline":      sum(1 for h in state["hosts"] if h.get("online") is False),
        "version":      HIM_VERSION,
    }

@app.post("/api/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    if not state["scanning"]:
        background_tasks.add_task(collect_all)
    return {"status": "refreshing"}

@app.get("/api/ping/{ip}")
async def ping_single(ip: str):
    host = next((h for h in state["hosts"] if h.get("ip") == ip), None)
    if host and host.get("online_authoritative"):
        return {
            "ip":        ip,
            "online":    True,
            "latency_ms": None,
            "note":      "Status reported by source (Docker/Proxmox); ICMP skipped",
        }
    checker = PingChecker()
    result  = await checker.ping_one(ip)
    if host:
        host["online"]     = result["online"]
        host["latency_ms"] = result.get("latency_ms")
    return result

@app.get("/api/portscan/{ip}")
async def portscan(ip: str):
    """Scan common ports on a single IP. Results are cached on the host record."""
    ports = await scan_ports(ip)
    for h in state["hosts"]:
        if h.get("ip") == ip:
            h["ports"] = ports
            break
    return {"ip": ip, "ports": ports}

@app.get("/api/subnet-map/{uid}")
async def subnet_map(uid: str):
    """
    Returns a full IP map for the given network uid.
    uid is the UniFi internal _id string, which uniquely identifies a network.
    """
    vlan = next((v for v in state["vlans"] if v.get("uid") == uid), None)
    if not vlan:
        raise HTTPException(404, f"Network {uid} not found")
    subnet = vlan.get("subnet", "")
    if not subnet:
        raise HTTPException(400, f"Network {uid} has no subnet defined")

    base_str, bits_str = subnet.split("/")
    bits = int(bits_str)
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    base_int  = _ip_to_int(base_str) & mask
    total     = (1 << (32 - bits))
    network   = _int_to_ip(base_int)
    broadcast = _int_to_ip(base_int + total - 1)
    gateway   = vlan.get("gateway", "")

    # DHCP range bounds
    dhcp_start_int = _ip_to_int(vlan["dhcp_start"]) if vlan.get("dhcp_start") else None
    dhcp_stop_int  = _ip_to_int(vlan["dhcp_stop"])  if vlan.get("dhcp_stop")  else None

    # Build known-IP index from current state
    ip_index: dict[str, dict] = {}
    for h in state["hosts"]:
        hip = h.get("ip")
        if hip and _ip_in_subnet(hip, subnet):
            ip_index[hip] = h

    entries = []
    for i in range(total):
        ip = _int_to_ip(base_int + i)
        is_network   = (i == 0)
        is_broadcast = (i == total - 1)
        is_gateway   = (ip == gateway)
        in_dhcp = (
            dhcp_start_int is not None and
            dhcp_stop_int  is not None and
            dhcp_start_int <= _ip_to_int(ip) <= dhcp_stop_int
        )

        if is_network or is_broadcast:
            status = "reserved"
        elif ip in ip_index:
            h = ip_index[ip]
            status = "online" if h.get("online") else "offline"
        elif is_gateway:
            status = "gateway"
        elif in_dhcp:
            status = "dhcp"
        else:
            status = "free"

        host = ip_index.get(ip)
        entries.append({
            "ip":          ip,
            "status":      status,
            "hostname":    host.get("hostname") if host else None,
            "type":        host.get("type") if host else None,
            "mac":         host.get("mac") if host else None,
            "is_gateway":  is_gateway,
            "in_dhcp":     in_dhcp,
            "sources":     host.get("sources") if host else [],
            "ip_assignment": host.get("ip_assignment") if host else None,
        })

    return {
        "vlan":      vlan,
        "subnet":    subnet,
        "total_ips": total - 2,   # usable (excl network + broadcast)
        "used":      sum(1 for e in entries if e["status"] in ("online", "offline", "gateway")),
        "online":    sum(1 for e in entries if e["status"] == "online"),
        "offline":   sum(1 for e in entries if e["status"] == "offline"),
        "free":      sum(1 for e in entries if e["status"] == "free"),
        "entries":   entries,
    }

@app.get("/api/settings")
async def get_settings():
    kv = store.get_all_kv()
    return {
        "unifi": {
            "host":       kv.get("unifi_host", ""),
            "port":       kv.get("unifi_port", "443"),
            "username":   kv.get("unifi_username", ""),
            "password":   kv.get("unifi_password", ""),
            "api_key":    kv.get("unifi_api_key", ""),
            "site":       kv.get("unifi_site", "default"),
            "verify_ssl": kv.get("unifi_verify_ssl", "false") == "true",
        },
        "general": {
            "refresh_interval": kv.get("refresh_interval", "300"),
        },
        "docker_hosts":  store.get_all_docker_hosts(),
        "proxmox_hosts": store.get_all_proxmox_hosts(),
    }

@app.post("/api/settings/unifi")
async def save_unifi(data: dict):
    store.set("unifi_host",       data.get("host", ""))
    store.set("unifi_port",       str(data.get("port", 443)))
    store.set("unifi_username",   data.get("username", ""))
    store.set("unifi_site",       data.get("site", "default"))
    store.set("unifi_verify_ssl", "true" if data.get("verify_ssl") else "false")
    pw = data.get("password", "")
    if pw and pw != "••••••••":
        store.set("unifi_password", pw)
    ak = data.get("api_key", "")
    if ak and ak != "••••••••":
        store.set("unifi_api_key", ak)
    return {"ok": True}

@app.post("/api/settings/general")
async def save_general(data: dict):
    store.set("refresh_interval", str(int(data.get("refresh_interval", 300))))
    return {"ok": True}

@app.post("/api/settings/test/unifi")
async def test_unifi():
    unifi_cfg = store.get_unifi_config()
    if not unifi_cfg["host"]:
        raise HTTPException(400, "UniFi host not configured")
    try:
        uc = UnifiCollector(unifi_cfg)
        data = await uc.collect()
        return {"ok": True, "clients": len(data["hosts"]), "vlans": len(data["vlans"])}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/settings/test/unifi/debug")
async def debug_unifi():
    """Returns raw counts and status codes from each UniFi API endpoint — use to diagnose empty results."""
    unifi_cfg = store.get_unifi_config()
    if not unifi_cfg["host"]:
        raise HTTPException(400, "UniFi host not configured")
    uc = UnifiCollector(unifi_cfg)
    return await uc.debug_info()

@app.get("/api/settings/docker")
async def get_docker_hosts():
    return store.get_all_docker_hosts()

@app.post("/api/settings/docker")
async def upsert_docker(data: dict):
    return store.upsert_docker_host(data)

@app.delete("/api/settings/docker/{hid}")
async def delete_docker(hid: int):
    store.delete_docker_host(hid)
    return {"ok": True}

@app.post("/api/settings/test/docker/{hid}")
async def test_docker(hid: int):
    hosts = store.get_all_docker_hosts()
    h = next((x for x in hosts if x["id"] == hid), None)
    if not h:
        raise HTTPException(404, "Host not found")
    try:
        dc = DockerCollector(h, known_subnets=[v["subnet"] for v in state.get("vlans", []) if v.get("subnet")])
        result = await dc.collect()
        return {"ok": True, "containers": len(result)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/settings/proxmox")
async def get_proxmox_hosts():
    return store.get_all_proxmox_hosts()

@app.post("/api/settings/proxmox")
async def upsert_proxmox(data: dict):
    return store.upsert_proxmox_host(data)

@app.delete("/api/settings/proxmox/{hid}")
async def delete_proxmox(hid: int):
    store.delete_proxmox_host(hid)
    return {"ok": True}

@app.post("/api/settings/test/proxmox/{hid}")
async def test_proxmox(hid: int):
    hosts = store.get_proxmox_hosts()
    h = next((x for x in hosts if x["id"] == hid), None)
    if not h:
        raise HTTPException(404, "Host not found")
    try:
        pc = ProxmoxCollector(h)
        result = await pc.collect()
        return {"ok": True, "vms_lxcs": len(result)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/debug/sources")
async def debug_sources():
    """Show each host's sources — useful for diagnosing filter issues."""
    return [
        {
            "ip": h.get("ip"),
            "hostname": h.get("hostname"),
            "type": h.get("type"),
            "sources": h.get("sources"),
        }
        for h in state["hosts"]
    ]

@app.get("/api/settings/test/proxmox/{hid}/debug")
async def debug_proxmox(hid: int):
    """Raw Proxmox API responses for troubleshooting."""
    hosts = store.get_proxmox_hosts()
    h = next((x for x in hosts if x["id"] == hid), None)
    if not h:
        raise HTTPException(404, "Host not found")

    import aiohttp as _aio
    pc = ProxmoxCollector(h)
    info: dict = {
        "host": h["host"],
        "auth": "token" if h.get("token_id") else "password",
        "errors": [],
    }

    connector = _aio.TCPConnector(ssl=pc._ssl_ctx())
    async with _aio.ClientSession(
        connector=connector,
        cookie_jar=_aio.CookieJar(unsafe=True),
    ) as session:
        try:
            await pc._login(session)
            info["login"] = "ok"
            info["csrf_present"] = bool(pc._csrf)
        except Exception as e:
            info["errors"].append(f"Login: {e}")
            return info

        for path in ["/nodes"]:
            resp = await session.get(
                f"{pc.api}{path}",
                headers=pc._auth_headers(),
                timeout=_aio.ClientTimeout(total=5),
            )
            try:
                body = await resp.json()
                data = body.get("data", [])
                info[path] = {"status": resp.status, "count": len(data) if isinstance(data, list) else str(type(data))}
            except Exception as e:
                info[path] = {"status": resp.status, "error": str(e)}

        nodes_data = await pc._get(session, "/nodes")
        info["nodes"] = [n.get("node") for n in nodes_data] if isinstance(nodes_data, list) else str(nodes_data)

        for node in (nodes_data or [])[:1]:
            node_name = node.get("node", "")
            vms  = await pc._get(session, f"/nodes/{node_name}/qemu")
            lxcs = await pc._get(session, f"/nodes/{node_name}/lxc")
            info[f"{node_name}_vms"]  = len(vms)  if isinstance(vms,  list) else str(vms)
            info[f"{node_name}_lxcs"] = len(lxcs) if isinstance(lxcs, list) else str(lxcs)

    return info

static_path = Path("/app/frontend/dist")
if static_path.exists():
    app.mount("/assets", StaticFiles(directory=str(static_path / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        return FileResponse(str(static_path / "index.html"))
