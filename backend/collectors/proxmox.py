import aiohttp
import ssl
from typing import List, Dict, Any, Optional


class ProxmoxCollector:
    """
    Collects IPs from Proxmox VE via the REST API.

    Auth:
      Token:    {"token_id": "user@pam!token", "token_secret": "uuid"}
      Password: {"user": "root@pam", "password": "secret"}
    """

    def __init__(self, config: Dict[str, Any]):
        self.name      = config.get("name", config.get("host", "proxmox"))
        self.host      = config["host"]
        self.port      = int(config.get("port", 8006))
        self.user      = config.get("user", "root@pam")
        self.password  = config.get("password", "")
        self.token_id  = config.get("token_id", "")
        self.token_sec = config.get("token_secret", "")
        self.verify_ssl = bool(config.get("verify_ssl", False))
        self.base      = f"https://{self.host}:{self.port}"
        self.api       = f"{self.base}/api2/json"
        # Set after login
        self._csrf:   Optional[str] = None
        self._ticket: Optional[str] = None

    def _ssl_ctx(self):
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _auth_headers(self) -> dict:
        """Headers required for authenticated requests."""
        headers = {}
        if self.token_id and self.token_sec:
            headers["Authorization"] = f"PVEAPIToken={self.token_id}={self.token_sec}"
        else:
            # Send the cookie manually as a raw header to prevent aiohttp from
            # quoting the value. aiohttp wraps cookie values containing special
            # characters (/, +, =) in double-quotes, but Proxmox expects the raw
            # ticket string without quotes.
            if self._ticket:
                headers["Cookie"] = f"PVEAuthCookie={self._ticket}"
            if self._csrf:
                headers["CSRFPreventionToken"] = self._csrf
        return headers

    async def _login(self, session: aiohttp.ClientSession):
        """
        For token auth: nothing to do.
        For password auth: POST /access/ticket, store CSRF token, and inject
        the PVEAuthCookie directly into the session's cookie jar so it is sent
        automatically on all subsequent requests.
        """
        if self.token_id:
            print(f"[Proxmox:{self.name}] Using API token: {self.token_id}")
            return

        print(f"[Proxmox:{self.name}] Password login as {self.user}")
        resp = await session.post(
            f"{self.api}/access/ticket",
            data={"username": self.user, "password": self.password},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(
                f"Proxmox login failed HTTP {resp.status}: {text[:300]}"
            )

        body = await resp.json()
        d = body.get("data") or {}
        ticket = d.get("ticket")
        csrf   = d.get("CSRFPreventionToken")

        if not ticket:
            raise RuntimeError(
                f"Proxmox returned 200 but no ticket in response — full body: {body}"
            )

        print(f"[Proxmox:{self.name}] Ticket obtained, prefix: {ticket[:30]}...")
        print(f"[Proxmox:{self.name}] CSRF: {csrf}")

        self._csrf   = csrf
        self._ticket = ticket
        print(f"[Proxmox:{self.name}] Login OK, ticket obtained")

    # ── Core HTTP ─────────────────────────────────────────────────────────────

    async def _get(self, session: aiohttp.ClientSession, path: str) -> Any:
        """GET {api}{path}, return data field."""
        url = f"{self.api}{path}"
        try:
            resp = await session.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status == 401:
                text = await resp.text()
                print(f"[Proxmox:{self.name}] 401 on {path}: {text[:200]}")
                return []
            if resp.status == 403:
                print(f"[Proxmox:{self.name}] 403 on {path} — insufficient permissions")
                return []
            if resp.status != 200:
                text = await resp.text()
                print(f"[Proxmox:{self.name}] HTTP {resp.status} on {path}: {text[:200]}")
                return []
            body = await resp.json()
            return body.get("data", [])
        except Exception as e:
            print(f"[Proxmox:{self.name}] GET {path} error: {e}")
            return []

    # ── Main collect ──────────────────────────────────────────────────────────

    async def collect(self) -> List[Dict[str, Any]]:
        hosts: List[Dict[str, Any]] = []
        connector = aiohttp.TCPConnector(ssl=self._ssl_ctx())

        async with aiohttp.ClientSession(connector=connector) as session:
            await self._login(session)

            nodes = await self._get(session, "/nodes")
            if not isinstance(nodes, list):
                print(f"[Proxmox:{self.name}] Unexpected /nodes response: {nodes}")
                return hosts

            for node in nodes:
                node_name   = node.get("node", "")
                node_online = node.get("status") == "online"
                if not node_name:
                    continue

                # Node itself
                node_ip = await self._node_ip(session, node_name)
                if node_ip:
                    hosts.append(self._make_host(
                        ip=node_ip, hostname=node_name, htype="proxmox-node",
                        online=node_online, network="management",
                        extra={"node": node_name},
                    ))

                # VMs
                vms = await self._get(session, f"/nodes/{node_name}/qemu")
                for vm in (vms or []):
                    await self._collect_vm(session, node_name, vm, hosts)

                # LXCs
                lxcs = await self._get(session, f"/nodes/{node_name}/lxc")
                for lxc in (lxcs or []):
                    await self._collect_lxc(session, node_name, lxc, hosts)

        return hosts

    # ── VM ────────────────────────────────────────────────────────────────────

    async def _collect_vm(self, session, node: str, vm: dict, hosts: list):
        vmid    = vm.get("vmid")
        name    = vm.get("name", f"vm-{vmid}")
        status  = vm.get("status", "stopped")
        running = status == "running"
        ips: List[str] = []
        ip_assignment   = "unknown"

        if running:
            ips = await self._vm_agent_ips(session, node, vmid)
            if ips:
                ip_assignment = "dhcp"   # agent-reported = runtime address (may be DHCP)
        if not ips:
            cfg = await self._get(session, f"/nodes/{node}/qemu/{vmid}/config")
            ips = self._ips_from_config(cfg)
            if ips:
                ip_assignment = "static"  # defined in VM config = static

        if not ips:
            hosts.append(self._make_host(
                ip=None, hostname=name, htype="vm", online=running, network=node,
                ip_assignment="unknown",
                extra={"node": node, "vmid": vmid, "status": status,
                       "cpus": vm.get("cpus"), "maxmem": vm.get("maxmem")},
            ))
            return
        for ip in ips:
            hosts.append(self._make_host(
                ip=ip, hostname=name, htype="vm", online=running, network=node,
                ip_assignment=ip_assignment,
                extra={"node": node, "vmid": vmid, "status": status,
                       "cpus": vm.get("cpus"), "maxmem": vm.get("maxmem")},
            ))

    async def _vm_agent_ips(self, session, node: str, vmid) -> List[str]:
        data = await self._get(
            session, f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces"
        )
        # Agent wraps: {"data": {"result": [...]}}
        if isinstance(data, dict):
            data = data.get("result", [])
        return self._ips_from_ifaces(data)

    # ── LXC ───────────────────────────────────────────────────────────────────

    async def _collect_lxc(self, session, node: str, lxc: dict, hosts: list):
        vmid    = lxc.get("vmid")
        name    = lxc.get("name", f"lxc-{vmid}")
        status  = lxc.get("status", "stopped")
        running = status == "running"
        ips: List[str] = []
        ip_assignment   = "unknown"

        if running:
            iface_data = await self._get(session, f"/nodes/{node}/lxc/{vmid}/interfaces")
            ips = self._ips_from_ifaces(
                iface_data if isinstance(iface_data, list) else []
            )
            if ips:
                # Check config to determine if static or DHCP
                cfg = await self._get(session, f"/nodes/{node}/lxc/{vmid}/config")
                net_fields = {k: v for k, v in (cfg or {}).items() if k.startswith("net")}
                is_dhcp = any("ip=dhcp" in str(v) for v in net_fields.values())
                ip_assignment = "dhcp" if is_dhcp else "static"

        if not ips:
            cfg = await self._get(session, f"/nodes/{node}/lxc/{vmid}/config")
            ips = self._ips_from_config(cfg)
            if ips:
                ip_assignment = "static"

        if not ips:
            hosts.append(self._make_host(
                ip=None, hostname=name, htype="lxc", online=running, network=node,
                ip_assignment="unknown",
                extra={"node": node, "vmid": vmid, "status": status},
            ))
            return
        for ip in ips:
            hosts.append(self._make_host(
                ip=ip, hostname=name, htype="lxc", online=running, network=node,
                ip_assignment=ip_assignment,
                extra={"node": node, "vmid": vmid, "status": status},
            ))

    # ── IP helpers ────────────────────────────────────────────────────────────

    def _ips_from_ifaces(self, ifaces: list) -> List[str]:
        """
        Parse IPs from interface data. Handles two formats:
          - Proxmox LXC /interfaces: ip-address-type is "inet"/"inet6"
          - QEMU guest agent:         ip-address-type is "ipv4"/"ipv6"
        Skips loopback, docker, bridge (br-*), and veth interfaces.
        """
        ips = []
        if not isinstance(ifaces, list):
            return ips
        for iface in ifaces:
            name = iface.get("name", "")
            # Skip loopback, docker bridge, virtual bridge, veth, and unnamed
            if not name:
                continue
            if (name.startswith("lo") or
                name.startswith("docker") or
                name.startswith("br-") or
                name.startswith("veth") or
                name.startswith("vmbr")):
                continue
            for addr in iface.get("ip-addresses", []):
                ip   = addr.get("ip-address", "")
                kind = addr.get("ip-address-type", "")
                # Accept both "ipv4" (QEMU agent) and "inet" (LXC interfaces)
                if ip and kind in ("ipv4", "inet") and not ip.startswith("127."):
                    ips.append(ip)
        return ips

    def _ips_from_config(self, cfg) -> List[str]:
        """
        Parse IPs from net* config strings.
        QEMU: net0=virtio=AA:BB:...,bridge=vmbr0,ip=10.0.0.5/24
        LXC:  net0=name=eth0,bridge=vmbr0,ip=10.0.0.6/24,gw=10.0.0.1
        """
        ips = []
        if not isinstance(cfg, dict):
            return ips
        for key, val in cfg.items():
            if not isinstance(val, str):
                continue
            if key.startswith("net"):
                for part in val.split(","):
                    part = part.strip()
                    if part.startswith("ip=") or part.startswith("ip6="):
                        raw = part.split("=", 1)[1]
                        ip  = raw.split("/")[0]
                        if ip and ip not in ("dhcp", "auto") and not ip.startswith("127.") and ":" not in ip:
                            ips.append(ip)
            elif key == "ip" and "/" in val:
                ip = val.split("/")[0]
                if ip and not ip.startswith("127."):
                    ips.append(ip)
        return ips

    # ── Node IP ───────────────────────────────────────────────────────────────

    async def _node_ip(self, session, node_name: str) -> Optional[str]:
        ifaces = await self._get(session, f"/nodes/{node_name}/network")
        if not isinstance(ifaces, list):
            return None
        for preferred in ("vmbr0", "vmbr1", "eth0", "ens18", "ens3"):
            for iface in ifaces:
                if iface.get("iface") == preferred:
                    addr = iface.get("address") or iface.get("cidr", "")
                    if addr:
                        return addr.split("/")[0]
        for iface in ifaces:
            if iface.get("iface", "").startswith("lo"):
                continue
            addr = iface.get("address") or iface.get("cidr", "")
            if addr:
                return addr.split("/")[0]
        return None

    # ── Factory ───────────────────────────────────────────────────────────────

    def _make_host(self, *, ip, hostname, htype, online, network, extra=None, ip_assignment="unknown") -> dict:
        return {
            "ip":            ip,
            "hostname":      hostname,
            "mac":           "",
            "network":       network,
            "vlan":          None,
            "sources":       [f"proxmox:{self.name}"],
            "type":          htype,
            "online":        online,
            "ip_assignment": ip_assignment,
            "extra":         {**(extra or {}), "proxmox_host": self.name},
        }
