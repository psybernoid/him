# HIM — Homelab IP Monitor

> **AI Disclosure:** HIM was built with the assistance of Claude (Anthropic). The architecture, code, and documentation were produced through an iterative human–AI collaboration. Review all code before deploying in your environment.

A self-hosted Docker application for consolidating and monitoring every IP address in your homelab. Pulls live data from **UniFi**, **Docker hosts**, and **Proxmox VE**, deduplicates and enriches it, then presents everything in a terminal-aesthetic web dashboard with ping checks, port scanning, and per-subnet IP allocation maps.

---

## Features

- **UniFi integration** — active clients, infrastructure devices, VLANs/subnets, DHCP ranges, IP assignment type (DHCP / reserved)
- **Docker integration** — all containers including macvlan, host-network, and bridge containers with published ports; works via direct TCP or socket proxy
- **Proxmox VE integration** — VMs (QEMU) and LXC containers with IPs from guest agent or config; password and API token auth
- **Subnet IP maps** — visual grid of every address in a subnet showing online/offline/DHCP/free status
- **Ping checks** — ICMP latency checks after each scan and on-demand per host
- **Port scanning** — async TCP connect scan against ~50 common homelab ports; Docker containers show published ports automatically
- **IP assignment badges** — Static (S), Reserved DHCP (R), or Dynamic DHCP (D) on each host
- **Subnet filtering** — click any VLAN chip to filter the device table to that subnet
- **Optional Basic Auth** — set two env vars to password-protect the interface
- **Web-based configuration** — all credentials stored encrypted in SQLite; no env vars needed for secrets
- **Encryption at rest** — passwords and tokens encrypted with Fernet (AES-128-CBC)

---

## Quick Start

```yaml
services:
  him:
    image: ghcr.io/psybernoid/him:latest
    container_name: him
    restart: unless-stopped
    ports:
      - "8080:8000"
    volumes:
      - ./data:/data
      # Optional: mount TLS certs for Docker hosts using TLS
      # - ./certs:/certs:ro
    environment:
      # ── Authentication (optional) ──────────────────────────────────────
      # Set both to enable HTTP Basic Auth. Leave empty to disable.
      HIM_USERNAME: ""
      HIM_PASSWORD: ""
    cap_add:
      - NET_RAW   # required for ICMP ping
```

Access at **http://your-host:8080**, then click **⚙ CONFIG** to add your sources.

---

The `./data` directory stores `him.db` (SQLite) and `secret.key` (Fernet encryption key). Back both up to preserve configuration.

---

## Configuration

All configuration is done through the **⚙ CONFIG** web interface. Nothing needs to be in environment variables except optional authentication.

---

## Source Setup

### UniFi Network

HIM supports **UniFi API Keys** (recommended, UniFi OS 3.x+) or username/password.

#### API Key (recommended)

1. Log in to your UniFi OS console (UDM / UDM-Pro / UDM-SE / Cloud Key Gen2+)
2. Go to **Integrations**
3. Click **Create API Key**, give it a name (e.g. `him-readonly`), copy the key
4. In HIM Config → UniFi: enter your controller IP, select **API KEY**, paste the key

> API keys work without a user account and bypass CSRF/session issues. Requires UniFi OS 3.x+.

#### Username / Password (older controllers)

1. In UniFi Network Application go to **Settings → Admins & Users → Admins**
2. Add a new admin with **View Only** role
3. In HIM Config → UniFi: enter host, port (443), username, password, site name (`default`)

**What HIM reads from UniFi:**
- Active clients with IP, MAC, hostname, VLAN, DHCP/reserved status
- Network devices (APs, switches, gateways)
- Network config: VLAN IDs, subnets, DHCP ranges and gateway addresses

---

### Docker Hosts

HIM connects to the Docker API over TCP. Two options:

#### Option 1 — Socket Proxy (recommended)

Deploy `tecnativa/docker-socket-proxy` alongside your existing stacks on each Docker host. This exposes only the read-only endpoints HIM needs, with no access to write operations.

```yaml
services:
  docker_socket_proxy:
    container_name: docker_socket_proxy
    image: tecnativa/docker-socket-proxy
    restart: unless-stopped
    privileged: true
    ports:
      - 2375:2375
    environment:
      - CONTAINERS=1
      - IMAGES=1
      - POST=0        # disables all write operations
      - NETWORKS=1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

In HIM Config → Docker: add host with IP of the machine running the proxy, port `2375`.

#### Option 2 — Direct TCP API

Add to `/etc/docker/daemon.json` on the target host:

```json
{
  "hosts": ["tcp://0.0.0.0:2375", "unix:///var/run/docker.sock"]
}
```

Then restart Docker: `sudo systemctl restart docker`

> ⚠️ Only expose port 2375 on a trusted internal network. Use the socket proxy or TLS (port 2376) for anything else.

**What HIM reads from Docker:**
- All containers (running and stopped)
- Network mode and IP addresses (macvlan, host, bridge)
- Published port mappings — shown automatically without needing a port scan
- Container image and state

---

### Proxmox VE

HIM supports both **API token auth** (recommended) and **password auth**.

#### API Token (recommended)

1. In the Proxmox web UI go to **Datacenter → Permissions → Users**
2. Click **Add**, create user `him@pve` (or `him@pam`)
3. Go to **Datacenter → Permissions → Add → User Permission**
   - Path: `/`
   - User: `him@pve`
   - Role: **PVEAuditor** (read-only)
   - Propagate: ✓
4. Go to **Datacenter → Permissions → API Tokens → Add**
   - User: `him@pve`
   - Token ID: `himtoken`
   - **Uncheck** "Privilege Separation" (so the token inherits the user's permissions)
   - Copy the token secret — it's only shown once
5. In HIM Config → Proxmox: enter host IP, select **API TOKEN**, enter:
   - Token ID: `him@pve!himtoken`
   - Token Secret: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

#### Password Auth

In HIM Config → Proxmox: enter host IP, select **PASSWORD**, enter user (`root@pam`) and password.

> Password auth uses ticket-based sessions. API token auth is stateless and more reliable.

**What HIM reads from Proxmox:**
- All QEMU VMs — IPs via QEMU guest agent (running) or config (static)
- All LXC containers — IPs via `/interfaces` endpoint (running) or config (static)
- Proxmox node management IP
- IP assignment type (static config vs DHCP)

**Note:** QEMU guest agent must be installed and running inside VMs for IP detection of running VMs with DHCP addresses (`apt install qemu-guest-agent` / `dnf install qemu-guest-agent`).

---

## Subnet IP Maps

The **Maps** tab shows a card for each VLAN/subnet from UniFi. Click any card to open the full IP allocation grid:

| Colour | Meaning |
|--------|---------|
| 🟢 Green | Host online (seen in this scan) |
| 🔴 Red | Host offline (seen before, currently unreachable) |
| 🔵 Blue | Gateway address |
| Amber tint | Within UniFi DHCP pool range |
| Dark | Free / unassigned |
| Grey | Network or broadcast address |

Click any occupied cell to see hostname, type, MAC, assignment type, and source.

---

## Authentication

Add to `docker-compose.yml` to enable HTTP Basic Auth:

```yaml
environment:
  HIM_USERNAME: "admin"
  HIM_PASSWORD: "your-secure-password"
```

Leave both empty (the default) to disable authentication. The password is checked server-side; credentials are never stored in the database.

---

## Data & Encryption

| File | Contents |
|------|---------|
| `./data/him.db` | SQLite database — all configuration |
| `./data/secret.key` | Fernet encryption key |

Passwords and API tokens are encrypted with **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256) before being written to the database. The key is auto-generated on first run.

**Backup:**
```bash
docker run --rm -v $(pwd)/data:/data -v $(pwd):/backup alpine \
  tar czf /backup/him-backup-$(date +%Y%m%d).tar.gz /data
```

**Restore:**
```bash
tar xzf him-backup-YYYYMMDD.tar.gz -C /
docker compose restart
```

---

## Architecture

```
┌─────────────────────────────────────────────┐
│                HIM Container                │
│                                             │
│  FastAPI backend  ◄──  React SPA (Vite)     │
│  (Python 3.12)         served as static     │
│        │                                    │
│  SQLite + Fernet                            │
│  encrypted config                           │
└──────────┬──────────────────────────────────┘
           │  outbound HTTPS/HTTP
    ┌──────┴─────────────────────────┐
    │  Collectors                    │
    ├────────────────────────────────┤
    │  UniFi    HTTPS + API key      │
    │  Docker   HTTP TCP API         │
    │  Proxmox  HTTPS + ticket/token │
    │  Ping     ICMP subprocess      │
    │  Ports    async TCP connect    │
    └────────────────────────────────┘
```

---

## Development

```bash
# Backend
cd him
pip install -r backend/requirements.txt
HIM_DATA_DIR=/tmp/him_dev uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # proxies /api/* to :8000
```

---

## Troubleshooting

**UniFi returns 0 clients/VLANs**
Run the debug endpoint: `curl http://your-host:8080/api/settings/test/unifi/debug | python3 -m json.tool`
Check `csrf_captured` and the status codes on each endpoint. Most common cause is using username/password auth on UniFi OS — switch to API key.

**Proxmox returns 401 on /nodes**
Check `docker logs him` for the auth log lines. Most common causes:
- Wrong realm — try `root@pam` not `root@pve`
- API token privilege separation enabled — uncheck it when creating the token
- Password contains special characters — use API token instead

**Docker containers not appearing**
Check that `CONTAINERS=1` is set in the socket proxy. Run `docker logs him` and look for `[Docker:...]` lines. If `/networks` returns 403, add `NETWORKS=1` to the proxy — this is needed for network driver metadata but containers will still appear without it.

**VMs showing without IPs**
For QEMU VMs: the QEMU guest agent must be installed and running inside the VM. For LXC containers: HIM reads IPs from the `/interfaces` endpoint (running containers) or the `net0` config field (static IPs). DHCP LXCs must be running at scan time.

**Ping not working**
The container requires `cap_add: NET_RAW`. Check this is present in `docker-compose.yml`.

---

## Credits

- Built with Claude (Anthropic) — [claude.ai](https://claude.ai)
- UI fonts: [Share Tech Mono](https://fonts.google.com/specimen/Share+Tech+Mono), [Exo 2](https://fonts.google.com/specimen/Exo+2)
- Socket proxy: [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
