"""
SQLite-backed config store with Fernet encryption for sensitive fields.

Schema:
  - config_kv: key/value store for simple settings (unifi host, intervals, etc.)
  - docker_hosts: one row per Docker host
  - proxmox_hosts: one row per Proxmox host

Encryption:
  - A 32-byte Fernet key is generated on first run and stored at DATA_DIR/secret.key
  - Sensitive fields (passwords, tokens) are encrypted before storage and
    decrypted transparently on read. Non-sensitive fields are stored as plain text.
"""

import os
import json
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, Column, Integer, String, Boolean, text
from sqlalchemy.orm import DeclarativeBase, Session

DATA_DIR = Path(os.getenv("HIM_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "him.db"
KEY_PATH = DATA_DIR / "secret.key"

# ── Fernet key management ────────────────────────────────────────────────────

def _load_or_create_key() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(0o600)
    return key

_fernet: Fernet | None = None

def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet

def encrypt(value: str) -> str:
    if not value:
        return ""
    return get_fernet().encrypt(value.encode()).decode()

def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value  # Already plain (migration / blank)

# ── SQLAlchemy models ────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass

class ConfigKV(Base):
    __tablename__ = "config_kv"
    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False, default="")

class DockerHost(Base):
    __tablename__ = "docker_hosts"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String, nullable=False)
    host        = Column(String, nullable=False)
    port        = Column(Integer, nullable=False, default=2375)
    tls         = Column(Boolean, nullable=False, default=False)
    ca_path     = Column(String, default="")
    cert_path   = Column(String, default="")
    key_path    = Column(String, default="")
    enabled     = Column(Boolean, nullable=False, default=True)

class ProxmoxHost(Base):
    __tablename__ = "proxmox_hosts"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    name          = Column(String, nullable=False)
    host          = Column(String, nullable=False)
    port          = Column(Integer, nullable=False, default=8006)
    user          = Column(String, default="root@pam")
    password_enc  = Column(String, default="")   # encrypted
    token_id      = Column(String, default="")
    token_secret_enc = Column(String, default="") # encrypted
    verify_ssl    = Column(Boolean, nullable=False, default=False)
    enabled       = Column(Boolean, nullable=False, default=True)

# ── Engine / session ─────────────────────────────────────────────────────────

def get_engine():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    _seed_defaults(engine)
    return engine

def _seed_defaults(engine):
    """Insert default config_kv rows if missing."""
    defaults = {
        "unifi_host":       "",
        "unifi_port":       "443",
        "unifi_username":   "",
        "unifi_password":   "",
        "unifi_api_key":    "",
        "unifi_site":       "default",
        "unifi_verify_ssl": "false",
        "refresh_interval": "300",
    }
    with Session(engine) as s:
        for k, v in defaults.items():
            if not s.get(ConfigKV, k):
                s.add(ConfigKV(key=k, value=v))
        s.commit()

# ── High-level config accessors ──────────────────────────────────────────────

ENCRYPTED_KV_KEYS = {"unifi_password", "unifi_api_key"}

class ConfigStore:
    def __init__(self, engine):
        self.engine = engine

    def _session(self):
        return Session(self.engine)

    # ── KV ──
    def get(self, key: str, default: str = "") -> str:
        with self._session() as s:
            row = s.get(ConfigKV, key)
            if row is None:
                return default
            val = row.value
            if key in ENCRYPTED_KV_KEYS:
                val = decrypt(val)
            return val

    def set(self, key: str, value: str):
        with self._session() as s:
            row = s.get(ConfigKV, key)
            stored = encrypt(value) if key in ENCRYPTED_KV_KEYS else value
            if row:
                row.value = stored
            else:
                s.add(ConfigKV(key=key, value=stored))
            s.commit()

    def get_all_kv(self) -> dict:
        """Return all KV config, decrypting sensitive fields."""
        with self._session() as s:
            rows = s.query(ConfigKV).all()
            result = {}
            for r in rows:
                val = r.value
                if r.key in ENCRYPTED_KV_KEYS:
                    val = "••••••••" if val else ""  # Mask for UI, use get() for actual value
                result[r.key] = val
            return result

    def get_unifi_config(self) -> dict:
        return {
            "host":       self.get("unifi_host"),
            "port":       int(self.get("unifi_port", "443")),
            "username":   self.get("unifi_username"),
            "password":   self.get("unifi_password"),
            "api_key":    self.get("unifi_api_key"),
            "site":       self.get("unifi_site", "default"),
            "verify_ssl": self.get("unifi_verify_ssl", "false").lower() == "true",
        }

    # ── Docker hosts ──
    def get_docker_hosts(self) -> list[dict]:
        with self._session() as s:
            rows = s.query(DockerHost).filter(DockerHost.enabled == True).all()
            return [self._docker_to_dict(r) for r in rows]

    def get_all_docker_hosts(self) -> list[dict]:
        with self._session() as s:
            rows = s.query(DockerHost).all()
            return [self._docker_to_dict(r) for r in rows]

    def _docker_to_dict(self, r: DockerHost) -> dict:
        return {
            "id": r.id, "name": r.name, "host": r.host, "port": r.port,
            "tls": r.tls, "ca": r.ca_path, "cert": r.cert_path, "key": r.key_path,
            "enabled": r.enabled,
        }

    def upsert_docker_host(self, data: dict) -> dict:
        with self._session() as s:
            hid = data.get("id")
            if hid:
                row = s.get(DockerHost, hid)
            else:
                row = None
            if row is None:
                row = DockerHost()
                s.add(row)
            row.name      = data["name"]
            row.host      = data["host"]
            row.port      = int(data.get("port", 2375))
            row.tls       = bool(data.get("tls", False))
            row.ca_path   = data.get("ca", "")
            row.cert_path = data.get("cert", "")
            row.key_path  = data.get("key", "")
            row.enabled   = bool(data.get("enabled", True))
            s.commit()
            s.refresh(row)
            return self._docker_to_dict(row)

    def delete_docker_host(self, hid: int):
        with self._session() as s:
            row = s.get(DockerHost, hid)
            if row:
                s.delete(row)
                s.commit()

    # ── Proxmox hosts ──
    def get_proxmox_hosts(self) -> list[dict]:
        with self._session() as s:
            rows = s.query(ProxmoxHost).filter(ProxmoxHost.enabled == True).all()
            return [self._proxmox_to_collector_dict(r) for r in rows]

    def get_all_proxmox_hosts(self) -> list[dict]:
        with self._session() as s:
            rows = s.query(ProxmoxHost).all()
            return [self._proxmox_to_ui_dict(r) for r in rows]

    def _proxmox_to_collector_dict(self, r: ProxmoxHost) -> dict:
        """Decrypted, for passing to the collector."""
        return {
            "id": r.id, "name": r.name, "host": r.host, "port": r.port,
            "user": r.user,
            "password": decrypt(r.password_enc),
            "token_id": r.token_id,
            "token_secret": decrypt(r.token_secret_enc),
            "verify_ssl": r.verify_ssl,
            "enabled": r.enabled,
        }

    def _proxmox_to_ui_dict(self, r: ProxmoxHost) -> dict:
        """Masked sensitive fields for the UI."""
        return {
            "id": r.id, "name": r.name, "host": r.host, "port": r.port,
            "user": r.user,
            "password": "••••••••" if r.password_enc else "",
            "token_id": r.token_id,
            "token_secret": "••••••••" if r.token_secret_enc else "",
            "verify_ssl": r.verify_ssl,
            "enabled": r.enabled,
        }

    def upsert_proxmox_host(self, data: dict) -> dict:
        with self._session() as s:
            hid = data.get("id")
            row = s.get(ProxmoxHost, hid) if hid else None
            if row is None:
                row = ProxmoxHost()
                s.add(row)
            row.name       = data["name"]
            row.host       = data["host"]
            row.port       = int(data.get("port", 8006))
            row.user       = data.get("user", "root@pam")
            row.token_id   = data.get("token_id", "")
            row.verify_ssl = bool(data.get("verify_ssl", False))
            row.enabled    = bool(data.get("enabled", True))
            # Only update secrets if a real value (not placeholder) is provided
            pw = data.get("password", "")
            if pw and pw != "••••••••":
                row.password_enc = encrypt(pw)
            ts = data.get("token_secret", "")
            if ts and ts != "••••••••":
                row.token_secret_enc = encrypt(ts)
            s.commit()
            s.refresh(row)
            return self._proxmox_to_ui_dict(row)

    def delete_proxmox_host(self, hid: int):
        with self._session() as s:
            row = s.get(ProxmoxHost, hid)
            if row:
                s.delete(row)
                s.commit()
