import { useState, useEffect, useCallback } from 'react'
import s from './Settings.module.css'

// ── Generic field components ─────────────────────────────────────────────────

function Field({ label, hint, children }) {
  return (
    <div className={s.field}>
      <label className={s.label}>{label}</label>
      {children}
      {hint && <div className={s.hint}>{hint}</div>}
    </div>
  )
}

function Input({ value, onChange, type = 'text', placeholder = '', mono = false }) {
  return (
    <input
      className={`${s.input} ${mono ? s.mono : ''}`}
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
    />
  )
}

function Toggle({ value, onChange, label }) {
  return (
    <div className={s.toggleRow} onClick={() => onChange(!value)}>
      <div className={`${s.toggle} ${value ? s.toggleOn : ''}`}>
        <div className={s.toggleThumb} />
      </div>
      <span className={s.toggleLabel}>{label}</span>
    </div>
  )
}

function StatusBadge({ status }) {
  if (!status) return null
  const cls = status.ok ? s.statusOk : s.statusErr
  return <div className={cls}>{status.ok ? `✓ ${status.msg}` : `✗ ${status.msg}`}</div>
}

// ── Test button ──────────────────────────────────────────────────────────────

function TestBtn({ label, onTest }) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const run = async () => {
    setLoading(true)
    setResult(null)
    try {
      const r = await onTest()
      setResult({ ok: true, msg: r })
    } catch (e) {
      setResult({ ok: false, msg: e.message || 'Connection failed' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={s.testRow}>
      <button className={s.testBtn} onClick={run} disabled={loading}>
        {loading ? '◌ TESTING…' : `⟳ TEST ${label}`}
      </button>
      {result && <StatusBadge status={result} />}
    </div>
  )
}

// ── UniFi section ────────────────────────────────────────────────────────────

function UnifiSection({ initial, onSave }) {
  const [cfg, setCfg] = useState(initial)
  const [saved, setSaved] = useState(false)
  const [authMode, setAuthMode] = useState(initial?.api_key ? 'apikey' : 'password')

  useEffect(() => {
    setCfg(initial)
    setAuthMode(initial?.api_key ? 'apikey' : 'password')
  }, [initial])

  const save = async () => {
    await fetch('/api/settings/unifi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    onSave()
  }

  const test = async () => {
    await fetch('/api/settings/unifi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    const r = await fetch('/api/settings/test/unifi', { method: 'POST' })
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
    const d = await r.json()
    return `${d.clients} clients, ${d.vlans} VLANs found`
  }

  const set = k => v => setCfg(c => ({ ...c, [k]: v }))

  return (
    <div className={s.section}>
      <div className={s.sectionHeader}>
        <span className={s.sectionIcon}>◉</span>
        <div>
          <div className={s.sectionTitle}>UniFi Network</div>
          <div className={s.sectionSub}>UniFi Network Application or UDM/UDM-Pro/UDM-SE</div>
        </div>
      </div>

      <div className={s.grid2}>
        <Field label="HOST / IP" hint="IP or hostname of your UniFi controller">
          <Input value={cfg.host} onChange={set('host')} placeholder="10.200.0.1" mono />
        </Field>
        <Field label="PORT">
          <Input value={cfg.port} onChange={set('port')} placeholder="443" mono />
        </Field>
        <Field label="SITE">
          <Input value={cfg.site} onChange={set('site')} placeholder="default" mono />
        </Field>
        <Field label="OPTIONS">
          <Toggle value={cfg.verify_ssl} onChange={set('verify_ssl')} label="Verify SSL certificate" />
        </Field>
      </div>

      <div className={s.authToggle}>
        <button className={`${s.authTab} ${authMode === 'apikey' ? s.authTabActive : ''}`} onClick={() => setAuthMode('apikey')}>API KEY (recommended)</button>
        <button className={`${s.authTab} ${authMode === 'password' ? s.authTabActive : ''}`} onClick={() => setAuthMode('password')}>USERNAME / PASSWORD</button>
      </div>

      {authMode === 'apikey' ? (
        <div>
          <Field
            label="API KEY"
            hint="Generate at: UniFi OS → Settings → Control Plane → API Keys. Requires UniFi OS 3.x+"
          >
            <Input value={cfg.api_key} onChange={set('api_key')} type="password" placeholder="••••••••••••••••••••••••••••••••" mono />
          </Field>
        </div>
      ) : (
        <div className={s.grid2}>
          <Field label="USERNAME" hint="Create a read-only local user in Settings → Admins">
            <Input value={cfg.username} onChange={set('username')} placeholder="him_readonly" mono />
          </Field>
          <Field label="PASSWORD">
            <Input value={cfg.password} onChange={set('password')} type="password" placeholder="••••••••" mono />
          </Field>
        </div>
      )}

      <div className={s.actions}>
        <button className={s.saveBtn} onClick={save}>
          {saved ? '✓ SAVED' : '↳ SAVE UNIFI'}
        </button>
        <TestBtn label="CONNECTION" onTest={test} />
      </div>
    </div>
  )
}

// ── Docker host form ─────────────────────────────────────────────────────────

function DockerHostForm({ host, onSave, onCancel }) {
  const blank = { name: '', host: '', port: 2375, tls: false, ca: '', cert: '', key: '', enabled: true }
  const [cfg, setCfg] = useState(host || blank)
  const set = k => v => setCfg(c => ({ ...c, [k]: v }))

  return (
    <div className={s.hostForm}>
      <div className={s.grid2}>
        <Field label="NAME">
          <Input value={cfg.name} onChange={set('name')} placeholder="docker-host-1" mono />
        </Field>
        <Field label="HOST / IP">
          <Input value={cfg.host} onChange={set('host')} placeholder="10.200.0.50" mono />
        </Field>
        <Field label="PORT" hint="2375 plain, 2376 TLS">
          <Input value={cfg.port} onChange={v => set('port')(parseInt(v) || 2375)} placeholder="2375" mono />
        </Field>
        <Field label="OPTIONS">
          <Toggle value={cfg.tls} onChange={set('tls')} label="Use TLS" />
          <Toggle value={cfg.enabled} onChange={set('enabled')} label="Enabled" />
        </Field>
      </div>
      {cfg.tls && (
        <div className={s.tlsFields}>
          <div className={s.tlsNote}>TLS certificate paths (inside container, use volumes to mount)</div>
          <div className={s.grid3}>
            <Field label="CA CERT PATH">
              <Input value={cfg.ca} onChange={set('ca')} placeholder="/certs/ca.pem" mono />
            </Field>
            <Field label="CLIENT CERT PATH">
              <Input value={cfg.cert} onChange={set('cert')} placeholder="/certs/cert.pem" mono />
            </Field>
            <Field label="CLIENT KEY PATH">
              <Input value={cfg.key} onChange={set('key')} placeholder="/certs/key.pem" mono />
            </Field>
          </div>
        </div>
      )}
      <div className={s.formActions}>
        <button className={s.saveBtn} onClick={() => onSave(cfg)}>↳ {cfg.id ? 'UPDATE' : 'ADD'}</button>
        <button className={s.cancelBtn} onClick={onCancel}>CANCEL</button>
      </div>
    </div>
  )
}

// ── Docker section ───────────────────────────────────────────────────────────

function DockerSection({ initial, onSave }) {
  const [hosts, setHosts] = useState(initial)
  const [editing, setEditing] = useState(null) // null | 'new' | {host obj}

  useEffect(() => { setHosts(initial) }, [initial])

  const save = async (data) => {
    const r = await fetch('/api/settings/docker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    const updated = await r.json()
    setHosts(h => {
      const idx = h.findIndex(x => x.id === updated.id)
      if (idx >= 0) { const n = [...h]; n[idx] = updated; return n }
      return [...h, updated]
    })
    setEditing(null)
    onSave()
  }

  const del = async (hid) => {
    if (!confirm('Remove this Docker host?')) return
    await fetch(`/api/settings/docker/${hid}`, { method: 'DELETE' })
    setHosts(h => h.filter(x => x.id !== hid))
    onSave()
  }

  const test = (hid) => async () => {
    const r = await fetch(`/api/settings/test/docker/${hid}`, { method: 'POST' })
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
    const d = await r.json()
    return `${d.containers} containers found`
  }

  return (
    <div className={s.section}>
      <div className={s.sectionHeader}>
        <span className={s.sectionIcon}>◈</span>
        <div>
          <div className={s.sectionTitle}>Docker Hosts</div>
          <div className={s.sectionSub}>Hosts with Docker TCP API enabled (port 2375/2376)</div>
        </div>
        <button className={s.addBtn} onClick={() => setEditing('new')}>+ ADD HOST</button>
      </div>

      {hosts.length === 0 && editing === null && (
        <div className={s.empty}>No Docker hosts configured. Click ADD HOST to get started.</div>
      )}

      {hosts.map(h => (
        <div key={h.id} className={s.hostCard}>
          <div className={s.hostCardMain}>
            <div className={s.hostCardInfo}>
              <span className={`${s.hostDot} ${h.enabled ? s.hostDotOn : s.hostDotOff}`} />
              <span className={s.hostName}>{h.name}</span>
              <span className={s.hostAddr}>{h.host}:{h.port}</span>
              {h.tls && <span className={s.tag}>TLS</span>}
            </div>
            <div className={s.hostCardActions}>
              <TestBtn label="" onTest={test(h.id)} />
              <button className={s.editBtn} onClick={() => setEditing(h)}>EDIT</button>
              <button className={s.delBtn} onClick={() => del(h.id)}>✕</button>
            </div>
          </div>
          {editing && (editing === h || editing?.id === h.id) && (
            <DockerHostForm host={h} onSave={save} onCancel={() => setEditing(null)} />
          )}
        </div>
      ))}

      {editing === 'new' && (
        <DockerHostForm onSave={save} onCancel={() => setEditing(null)} />
      )}

      <div className={s.setupNote}>
        <strong>Option 1 — TCP API (direct)</strong><br />
        Add to <code>/etc/docker/daemon.json</code> on the target host:<br />
        <code className={s.codeBlock}>{'{"hosts": ["tcp://0.0.0.0:2375", "unix:///var/run/docker.sock"]}'}</code>
        Then: <code>sudo systemctl restart docker</code><br /><br />
        <strong>Option 2 — Socket proxy (recommended)</strong><br />
        Add this service to a compose stack on the target host. Set the port to match what you enter above.<br />
        <code className={s.codeBlock}>{`services:
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
      - POST=0
      - NETWORKS=1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock`}</code>
        The socket proxy exposes only the endpoints HIM needs (read-only). <code>POST=0</code> prevents any write operations.
      </div>
    </div>
  )
}

// ── Proxmox host form ────────────────────────────────────────────────────────

function ProxmoxHostForm({ host, onSave, onCancel }) {
  const blank = { name: '', host: '', port: 8006, user: 'root@pam', password: '', token_id: '', token_secret: '', verify_ssl: false, enabled: true }
  const [cfg, setCfg] = useState(host || blank)
  const [authMode, setAuthMode] = useState(host?.token_id ? 'token' : 'password')
  const set = k => v => setCfg(c => ({ ...c, [k]: v }))

  return (
    <div className={s.hostForm}>
      <div className={s.grid2}>
        <Field label="NAME">
          <Input value={cfg.name} onChange={set('name')} placeholder="pve1" mono />
        </Field>
        <Field label="HOST / IP">
          <Input value={cfg.host} onChange={set('host')} placeholder="10.200.0.60" mono />
        </Field>
        <Field label="PORT">
          <Input value={cfg.port} onChange={v => set('port')(parseInt(v) || 8006)} placeholder="8006" mono />
        </Field>
        <Field label="OPTIONS">
          <Toggle value={cfg.verify_ssl} onChange={set('verify_ssl')} label="Verify SSL certificate" />
          <Toggle value={cfg.enabled} onChange={set('enabled')} label="Enabled" />
        </Field>
      </div>

      <div className={s.authToggle}>
        <button className={`${s.authTab} ${authMode === 'password' ? s.authTabActive : ''}`} onClick={() => setAuthMode('password')}>PASSWORD AUTH</button>
        <button className={`${s.authTab} ${authMode === 'token' ? s.authTabActive : ''}`} onClick={() => setAuthMode('token')}>API TOKEN</button>
      </div>

      {authMode === 'password' ? (
        <div className={s.grid2}>
          <Field label="USER" hint="e.g. root@pam">
            <Input value={cfg.user} onChange={set('user')} placeholder="root@pam" mono />
          </Field>
          <Field label="PASSWORD">
            <Input value={cfg.password} onChange={set('password')} type="password" placeholder="••••••••" mono />
          </Field>
        </div>
      ) : (
        <div className={s.grid2}>
          <Field label="TOKEN ID" hint="e.g. him@pam!himtoken">
            <Input value={cfg.token_id} onChange={set('token_id')} placeholder="him@pam!himtoken" mono />
          </Field>
          <Field label="TOKEN SECRET">
            <Input value={cfg.token_secret} onChange={set('token_secret')} type="password" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" mono />
          </Field>
        </div>
      )}

      <div className={s.formActions}>
        <button className={s.saveBtn} onClick={() => onSave(cfg)}>↳ {cfg.id ? 'UPDATE' : 'ADD'}</button>
        <button className={s.cancelBtn} onClick={onCancel}>CANCEL</button>
      </div>
    </div>
  )
}

// ── Proxmox section ──────────────────────────────────────────────────────────

function ProxmoxSection({ initial, onSave }) {
  const [hosts, setHosts] = useState(initial)
  const [editing, setEditing] = useState(null)

  useEffect(() => { setHosts(initial) }, [initial])

  const save = async (data) => {
    const r = await fetch('/api/settings/proxmox', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    const updated = await r.json()
    setHosts(h => {
      const idx = h.findIndex(x => x.id === updated.id)
      if (idx >= 0) { const n = [...h]; n[idx] = updated; return n }
      return [...h, updated]
    })
    setEditing(null)
    onSave()
  }

  const del = async (hid) => {
    if (!confirm('Remove this Proxmox host?')) return
    await fetch(`/api/settings/proxmox/${hid}`, { method: 'DELETE' })
    setHosts(h => h.filter(x => x.id !== hid))
    onSave()
  }

  const test = (hid) => async () => {
    const r = await fetch(`/api/settings/test/proxmox/${hid}`, { method: 'POST' })
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
    const d = await r.json()
    return `${d.vms_lxcs} VMs/LXCs found`
  }

  return (
    <div className={s.section}>
      <div className={s.sectionHeader}>
        <span className={s.sectionIcon}>▤</span>
        <div>
          <div className={s.sectionTitle}>Proxmox VE</div>
          <div className={s.sectionSub}>Proxmox VE nodes — VMs and LXC containers</div>
        </div>
        <button className={s.addBtn} onClick={() => setEditing('new')}>+ ADD HOST</button>
      </div>

      {hosts.length === 0 && editing === null && (
        <div className={s.empty}>No Proxmox hosts configured.</div>
      )}

      {hosts.map(h => (
        <div key={h.id} className={s.hostCard}>
          <div className={s.hostCardMain}>
            <div className={s.hostCardInfo}>
              <span className={`${s.hostDot} ${h.enabled ? s.hostDotOn : s.hostDotOff}`} />
              <span className={s.hostName}>{h.name}</span>
              <span className={s.hostAddr}>{h.host}:{h.port}</span>
              {h.token_id && <span className={s.tag}>API TOKEN</span>}
              {!h.verify_ssl && <span className={s.tagGray}>NO SSL VERIFY</span>}
            </div>
            <div className={s.hostCardActions}>
              <TestBtn label="" onTest={test(h.id)} />
              <button className={s.editBtn} onClick={() => setEditing(h)}>EDIT</button>
              <button className={s.delBtn} onClick={() => del(h.id)}>✕</button>
            </div>
          </div>
          {editing && (editing === h || editing?.id === h.id) && (
            <ProxmoxHostForm host={h} onSave={save} onCancel={() => setEditing(null)} />
          )}
        </div>
      ))}

      {editing === 'new' && (
        <ProxmoxHostForm onSave={save} onCancel={() => setEditing(null)} />
      )}
    </div>
  )
}

// ── General section ──────────────────────────────────────────────────────────

function GeneralSection({ initial, onSave }) {
  const [cfg, setCfg] = useState(initial)
  const [saved, setSaved] = useState(false)

  useEffect(() => { setCfg(initial) }, [initial])

  const save = async () => {
    await fetch('/api/settings/general', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_interval: parseInt(cfg.refresh_interval) }),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    onSave()
  }

  return (
    <div className={s.section}>
      <div className={s.sectionHeader}>
        <span className={s.sectionIcon}>⚙</span>
        <div>
          <div className={s.sectionTitle}>General</div>
          <div className={s.sectionSub}>Scan intervals and behaviour</div>
        </div>
      </div>
      <div className={s.grid2}>
        <Field label="SCAN INTERVAL (SECONDS)" hint="How often to auto-collect data. Default: 300 (5 minutes)">
          <Input value={cfg.refresh_interval} onChange={v => setCfg(c => ({ ...c, refresh_interval: v }))} placeholder="300" mono />
        </Field>
      </div>
      <div className={s.actions}>
        <button className={s.saveBtn} onClick={save}>
          {saved ? '✓ SAVED' : '↳ SAVE GENERAL'}
        </button>
      </div>

      <div className={s.securityNote}>
        <div className={s.secTitle}>🔒 ENCRYPTION</div>
        <div className={s.secBody}>
          All passwords and API tokens are encrypted at rest using Fernet symmetric encryption (AES-128-CBC).
          The encryption key is stored at <code>/data/secret.key</code> inside the container — back this up
          along with <code>/data/him.db</code> to preserve your configuration.
        </div>
      </div>
    </div>
  )
}

// ── Main Settings component ──────────────────────────────────────────────────

const TABS = [
  { id: 'unifi',   label: 'UNIFI',   icon: '◉' },
  { id: 'docker',  label: 'DOCKER',  icon: '◈' },
  { id: 'proxmox', label: 'PROXMOX', icon: '▤' },
  { id: 'general', label: 'GENERAL', icon: '⚙' },
]

export default function Settings({ onClose }) {
  const [tab, setTab] = useState('unifi')
  const [config, setConfig] = useState(null)

  const load = useCallback(async () => {
    const r = await fetch('/api/settings')
    setConfig(await r.json())
  }, [])

  useEffect(() => { load() }, [load])

  if (!config) return (
    <div className={s.overlay}>
      <div className={s.modal}>
        <div className={s.loading}>LOADING CONFIGURATION<span className="blink">_</span></div>
      </div>
    </div>
  )

  return (
    <div className={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={s.modal}>
        <div className={s.modalHeader}>
          <div className={s.modalTitle}>
            <span className={s.modalTitleIcon}>⚙</span>
            CONFIGURATION
          </div>
          <button className={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        <div className={s.tabs}>
          {TABS.map(t => (
            <button
              key={t.id}
              className={`${s.tab} ${tab === t.id ? s.tabActive : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        <div className={s.body}>
          {tab === 'unifi'   && <UnifiSection   initial={config.unifi}         onSave={load} />}
          {tab === 'docker'  && <DockerSection  initial={config.docker_hosts}  onSave={load} />}
          {tab === 'proxmox' && <ProxmoxSection initial={config.proxmox_hosts} onSave={load} />}
          {tab === 'general' && <GeneralSection initial={config.general}       onSave={load} />}
        </div>
      </div>
    </div>
  )
}
