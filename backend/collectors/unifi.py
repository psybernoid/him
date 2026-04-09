import aiohttp
import ssl
import json
from typing import Dict, Any, List


class UnifiCollector:
    """
    Supports two auth modes, tried in order:
      1. API Key (UniFi OS 3.x+) — Bearer token, simplest and most reliable
      2. Username/password session (fallback for older controllers)
    """

    def __init__(self, config: dict):
        self.host = config["host"]
        self.port = int(config.get("port", 443))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.api_key = config.get("api_key", "")
        self.site = config.get("site", "default")
        self.verify_ssl = bool(config.get("verify_ssl", False))
        self.base_url = f"https://{self.host}:{self.port}"
        self._api_prefix = ""
        self._auth_headers: dict = {}

    def _ssl_context(self):
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _make_connector(self):
        return aiohttp.TCPConnector(ssl=self._ssl_context())

    # ── Public API ────────────────────────────────────────────────────────────

    async def collect(self) -> Dict[str, Any]:
        hosts = []
        vlans = []

        async with aiohttp.ClientSession(connector=self._make_connector()) as session:
            await self._authenticate(session)

            # Networks / VLANs
            networks_raw = await self._get(session, f"/api/s/{self.site}/rest/networkconf")
            network_by_id = {}
            for net in networks_raw:
                nid     = net.get("_id", "")
                vlan_id = net.get("vlan")
                subnet  = net.get("ip_subnet", "")
                name    = net.get("name", "")
                purpose = net.get("purpose", "")
                network_by_id[nid] = {"name": name, "vlan": vlan_id, "subnet": subnet}

                # Skip networks without a real subnet
                if not subnet:
                    continue

                # Skip VPN client/peer networks — these are point-to-point tunnels
                # assigned to VLAN 1 by default in UniFi but are not actual subnets.
                # Identify by purpose or by /32 prefix (single-host routes).
                if purpose in ("vpn-client", "vpn-server", "wg-client", "wg-server"):
                    continue
                bits = int(subnet.split("/")[1]) if "/" in subnet else 0
                if bits >= 32:
                    continue  # /32 = single host tunnel endpoint, not a usable subnet

                vlans.append({
                    "uid":          nid,          # unique across all networks
                    "id":           vlan_id if vlan_id is not None else 0,
                    "name":         name,
                    "subnet":       subnet,
                    "purpose":      purpose,
                    "dhcp_enabled": net.get("dhcpd_enabled", False),
                    "dhcp_start":   net.get("dhcpd_start", ""),
                    "dhcp_stop":    net.get("dhcpd_stop", ""),
                    "gateway":      net.get("dhcpd_gateway", "") or (subnet.split("/")[0] if subnet else ""),
                })

            # Active clients
            clients = await self._get(session, f"/api/s/{self.site}/stat/sta")

            # Known clients list includes DHCP reservation info
            known_clients = await self._get(session, f"/api/s/{self.site}/rest/user")
            known_by_mac: dict = {kc.get("mac", "").lower(): kc for kc in known_clients if kc.get("mac")}

            for c in clients:
                ip = c.get("ip")
                if not ip:
                    continue
                mac = c.get("mac", "").lower()
                net_info = network_by_id.get(c.get("network_id", ""), {})

                # IP assignment type
                known = known_by_mac.get(mac, {})
                if known.get("use_fixedip") and known.get("fixed_ip"):
                    ip_assignment = "reserved"   # DHCP reservation in UniFi
                elif c.get("use_fixedip") or c.get("fixed_ip"):
                    ip_assignment = "reserved"
                else:
                    ip_assignment = "dhcp"

                hosts.append({
                    "ip": ip,
                    "hostname": c.get("hostname") or c.get("name") or c.get("display_name") or c.get("mac", ""),
                    "mac": c.get("mac", ""),
                    "vlan": net_info.get("vlan") or c.get("vlan"),
                    "network": net_info.get("name") or c.get("network", ""),
                    "sources": ["unifi-client"],
                    "type": "client",
                    "online": True,
                    "ip_assignment": ip_assignment,
                    "extra": {
                        "signal": c.get("signal"),
                        "essid": c.get("essid"),
                        "is_wired": c.get("is_wired", False),
                        "uptime": c.get("uptime"),
                        "oui": c.get("oui", ""),
                        "ip_assignment": ip_assignment,
                    }
                })

            # Infrastructure devices
            devices = await self._get(session, f"/api/s/{self.site}/stat/device")
            for d in devices:
                ip = d.get("ip")
                if not ip:
                    continue
                hosts.append({
                    "ip": ip,
                    "hostname": d.get("name") or d.get("model", "UniFi Device"),
                    "mac": d.get("mac", ""),
                    "vlan": None,
                    "network": "Management",
                    "sources": ["unifi-device"],
                    "type": self._device_type(d.get("type", "")),
                    "online": d.get("state") == 1,
                    "extra": {
                        "model": d.get("model"),
                        "version": d.get("version"),
                        "uptime": d.get("uptime"),
                    }
                })

        return {"hosts": hosts, "vlans": vlans}

    async def debug_info(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "auth_mode": None,
            "api_prefix": None,
            "errors": [],
        }
        async with aiohttp.ClientSession(connector=self._make_connector()) as session:
            try:
                mode = await self._authenticate(session)
                info["auth_mode"] = mode
                info["api_prefix"] = self._api_prefix
            except Exception as e:
                info["errors"].append(f"Auth failed: {e}")
                return info

            for endpoint in [
                f"/api/s/{self.site}/rest/networkconf",
                f"/api/s/{self.site}/stat/sta",
                f"/api/s/{self.site}/stat/device",
            ]:
                url = f"{self.base_url}{self._api_prefix}{endpoint}"
                try:
                    resp = await session.get(
                        url,
                        headers=self._auth_headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
                    text = await resp.text()
                    try:
                        body = json.loads(text)
                        items = body.get("data", []) if isinstance(body, dict) else body
                        rc = body.get("meta", {}).get("rc") if isinstance(body, dict) else "n/a"
                        info[endpoint] = {"status": resp.status, "count": len(items), "rc": rc}
                    except Exception:
                        info[endpoint] = {"status": resp.status, "parse_error": text[:300]}
                except Exception as e:
                    info[endpoint] = {"error": str(e)}

        return info

    # ── Authentication ────────────────────────────────────────────────────────

    async def _authenticate(self, session: aiohttp.ClientSession) -> str:
        """Try API key first, then username/password. Returns the mode used."""

        # ── Mode 1: API Key (UniFi OS 3.x+) ──────────────────────────────────
        # Generate at: UniFi OS → Settings → Control Plane → API Keys
        if self.api_key:
            # API key is sent as a Bearer token — no login call needed at all
            self._auth_headers = {"X-API-KEY": self.api_key}
            self._api_prefix = "/proxy/network"
            print(f"[UniFi] Using API key auth")
            return "api_key"

        # ── Mode 2: Username / password (session cookie + CSRF) ───────────────
        timeout = aiohttp.ClientTimeout(total=15)

        # Try UniFi OS session login
        try:
            resp = await session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            if resp.status == 401:
                raise ValueError("Credentials rejected (401)")
            if resp.status == 200:
                csrf = resp.headers.get("X-CSRF-Token", "")
                self._auth_headers = {"X-CSRF-Token": csrf} if csrf else {}
                # Merge CSRF into session default headers so _get picks it up
                if csrf:
                    session.headers.update({"X-CSRF-Token": csrf})
                self._api_prefix = "/proxy/network"
                print(f"[UniFi] Session login OK (UniFi OS), CSRF={'yes' if csrf else 'no'}")
                return "unifi_os_session"
        except ValueError:
            raise
        except Exception as e:
            print(f"[UniFi] UniFi OS login failed ({e}), trying classic…")

        # Try classic Network Application login
        resp = await session.post(
            f"{self.base_url}/api/login",
            json={"username": self.username, "password": self.password},
            timeout=timeout,
        )
        body = await resp.json()
        if resp.status == 200 and body.get("meta", {}).get("rc") == "ok":
            self._auth_headers = {}
            self._api_prefix = ""
            print(f"[UniFi] Session login OK (classic)")
            return "classic_session"

        raise RuntimeError(
            f"All auth methods failed. Last response: HTTP {resp.status} — {body}"
        )

    async def _get(self, session: aiohttp.ClientSession, path: str) -> List[Any]:
        url = f"{self.base_url}{self._api_prefix}{path}"
        try:
            resp = await session.get(
                url,
                headers=self._auth_headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status != 200:
                print(f"[UniFi] HTTP {resp.status} on {self._api_prefix}{path}")
                return []
            data = await resp.json()
            if isinstance(data, dict):
                if data.get("meta", {}).get("rc") not in (None, "ok"):
                    print(f"[UniFi] rc={data['meta']['rc']} on {path}")
                return data.get("data", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[UniFi] GET {path} error: {e}")
            return []

    def _device_type(self, utype: str) -> str:
        return {
            "ugw": "gateway", "uap": "access-point",
            "usw": "switch",  "udm": "gateway",
            "uxg": "gateway", "usg": "gateway",
        }.get((utype or "").lower(), "network-device")
