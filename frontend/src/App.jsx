import { useState, useEffect, useCallback, useMemo } from 'react'
import styles from './App.module.css'
import Settings from './Settings.jsx'
import SubnetMap from './SubnetMap.jsx'

const TYPE_META = {
  'client':        { icon: '⬡', label: 'Client',        color: '#4da6ff' },
  'container':     { icon: '◈', label: 'Container',     color: '#c084fc' },
  'vm':            { icon: '▣', label: 'VM',            color: '#ffb347' },
  'lxc':           { icon: '◫', label: 'LXC',           color: '#ff9f7f' },
  'gateway':       { icon: '◉', label: 'Gateway',       color: '#3ddc84' },
  'access-point':  { icon: '◎', label: 'AP',            color: '#3ddc84' },
  'switch':        { icon: '⬢', label: 'Switch',        color: '#3ddc84' },
  'network-device':{ icon: '⬡', label: 'Network',       color: '#3ddc84' },
  'proxmox-node':  { icon: '▤', label: 'PVE Node',      color: '#ffb347' },
}

const SOURCE_COLORS = {
  'unifi-client':  '#4da6ff',
  'unifi-device':  '#3ddc84',
}

function sourceColor(src) {
  if (SOURCE_COLORS[src]) return SOURCE_COLORS[src]
  if (src.startsWith('docker:')) return '#c084fc'
  if (src.startsWith('proxmox:')) return '#ffb347'
  return '#6a9a7a'
}

function formatBytes(bytes) {
  if (!bytes) return '—'
  const gb = bytes / 1073741824
  return gb >= 1 ? `${gb.toFixed(1)}GB` : `${(bytes/1048576).toFixed(0)}MB`
}

function timeAgo(ts) {
  if (!ts) return 'never'
  const sec = Math.floor(Date.now()/1000 - ts)
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec/60)}m ago`
  return `${Math.floor(sec/3600)}h ago`
}

function ipToNum(ip) {
  const parts = ip.split('.')
  if (parts.length !== 4) return 0
  return parts.reduce((acc, o) => (acc * 256) + parseInt(o, 10), 0)
}

function ipInSubnet(ip, subnet) {
  // subnet may be "10.200.0.0/24" or "10.200.0.1/24" (host addr form from UniFi)
  try {
    const [base, bitsStr] = subnet.split('/')
    const bits = parseInt(bitsStr, 10)
    const mask = bits === 0 ? 0 : (0xFFFFFFFF << (32 - bits)) >>> 0
    const ipNum   = ipToNum(ip) >>> 0
    const baseNum = ipToNum(base) >>> 0
    return (ipNum & mask) === (baseNum & mask)
  } catch { return false }
}

function HostRow({ host, onPing, pingLoading }) {
  const [expanded, setExpanded] = useState(false)
  // Use ports already known from Docker inspect; fallback to scan results
  const [ports, setPorts] = useState(host.ports?.length ? host.ports : null)
  const [scanningPorts, setScanningPorts] = useState(false)
  const tm = TYPE_META[host.type] || { icon: '○', label: host.type, color: '#6a9a7a' }

  // Sync if host.ports changes (new scan data comes in from API refresh)
  useState(() => {
    if (host.ports?.length) setPorts(host.ports)
  })

  const onlineState = host.online === true ? 'online'
    : host.online === false ? 'offline'
    : 'unknown'

  const handlePortScan = async (e) => {
    e.stopPropagation()
    if (!host.ip || scanningPorts) return
    setScanningPorts(true)
    try {
      const r = await fetch(`/api/portscan/${host.ip}`)
      const d = await r.json()
      setPorts(d.ports)
    } finally {
      setScanningPorts(false)
    }
  }

  const assignmentBadge = host.ip_assignment && host.ip_assignment !== 'unknown'
    ? host.ip_assignment
    : null

  return (
    <>
      <tr
        className={`${styles.hostRow} ${styles[onlineState]}`}
        onClick={() => setExpanded(e => !e)}
      >
        <td className={styles.statusCell}>
          <span className={`${styles.dot} ${styles[`dot_${onlineState}`]}`} />
        </td>
        <td className={styles.ipCell}>
          <span className={styles.ipAddr}>{host.ip || '—'}</span>
          {assignmentBadge && (
            <span
              className={`${styles.assignBadge} ${styles[`assign_${assignmentBadge}`]}`}
              title={`IP assignment: ${assignmentBadge}`}
            >
              {assignmentBadge === 'static' ? 'S' : assignmentBadge === 'reserved' ? 'R' : 'D'}
            </span>
          )}
        </td>
        <td className={styles.hostnameCell}>{host.hostname || '—'}</td>
        <td className={styles.typeCell}>
          <span className={styles.typeTag} style={{ color: tm.color, borderColor: tm.color + '44' }}>
            {tm.icon} {tm.label}
          </span>
        </td>
        <td className={styles.networkCell}>{host.network || '—'}</td>
        <td className={styles.vlanCell}>{host.vlan != null ? `VLAN ${host.vlan}` : '—'}</td>
        <td className={styles.latencyCell}>
          {host.latency_ms != null
            ? <span className={styles.latency}>{host.latency_ms}ms</span>
            : onlineState === 'online' ? <span className={styles.latencyFast}>—</span> : '—'
          }
        </td>
        <td className={styles.sourcesCell}>
          {(host.sources || []).map(s => (
            <span key={s} className={styles.sourceTag} style={{ borderColor: sourceColor(s) + '66', color: sourceColor(s) }}>
              {s}
            </span>
          ))}
          {(host.sources || []).length > 1 && (
            <span className={styles.mergedTag} title="This host was discovered by multiple sources and merged">⊕</span>
          )}
        </td>
        <td className={styles.actionCell}>
          {host.online_authoritative ? (
            <span
              className={styles.sourceStatus}
              title="Online status reported by Docker/Proxmox — ICMP ping not used"
            >
              ✓
            </span>
          ) : (
            <button
              className={styles.pingBtn}
              onClick={e => { e.stopPropagation(); onPing(host.ip) }}
              disabled={pingLoading || !host.ip}
              title="Ping"
            >
              {pingLoading ? '…' : '⟳'}
            </button>
          )}
          {host.ip && (
            <button
              className={styles.pingBtn}
              onClick={handlePortScan}
              disabled={scanningPorts}
              title="Scan ports"
              style={{ marginLeft: 4 }}
            >
              {scanningPorts ? '…' : '⬡'}
            </button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className={styles.expandedRow}>
          <td colSpan={9}>
            <div className={styles.expandedContent}>
              {host.mac && <div><span className={styles.extraLabel}>MAC</span><span className={styles.extraMono}>{host.mac}</span></div>}
              {host.extra?.service_primary && (
                <div>
                  <span className={styles.extraLabel}>NETWORK VIA</span>
                  <span className={styles.extraMono} style={{ color: '#c084fc' }}>
                    service:{host.extra.service_primary}
                    {host.extra.primary_ip ? ` (${host.extra.primary_ip})` : ''}
                  </span>
                </div>
              )}
              {host.extra && Object.entries(host.extra)
                .filter(([k, v]) => v != null && v !== '' && v !== false
                  && k !== 'service_primary' && k !== 'primary_ip')
                .map(([k, v]) => (
                  <div key={k}>
                    <span className={styles.extraLabel}>{k.replace(/_/g, ' ')}</span>
                    <span className={styles.extraMono}>
                      {k === 'maxmem' ? formatBytes(v) : String(v)}
                    </span>
                  </div>
                ))
              }
            </div>
            {/* Ports section */}
            <div className={styles.portsSection}>
              <div className={styles.portsHeader}>
                <span className={styles.extraLabel}>
                  {host.type === 'container' && host.ports?.length ? 'PUBLISHED PORTS' : 'OPEN PORTS'}
                </span>
                {host.ip && host.type !== 'container' && (
                  <button
                    className={styles.portScanBtn}
                    onClick={handlePortScan}
                    disabled={scanningPorts}
                  >
                    {scanningPorts ? '◌ SCANNING…' : ports ? '⟳ RESCAN' : '⬡ SCAN PORTS'}
                  </button>
                )}
                {host.ip && host.type === 'container' && (
                  <button
                    className={styles.portScanBtn}
                    onClick={handlePortScan}
                    disabled={scanningPorts}
                  >
                    {scanningPorts ? '◌ SCANNING…' : '⬡ FULL SCAN'}
                  </button>
                )}
              </div>
              {scanningPorts && (
                <div className={styles.portsScanningMsg}>scanning {host.ip}…</div>
              )}
              {!scanningPorts && ports && ports.length === 0 && (
                <div className={styles.portsEmpty}>no open ports found</div>
              )}
              {!scanningPorts && ports && ports.length > 0 && (
                <div className={styles.portsList}>
                  {ports.map(p => (
                    <span key={p.port} className={styles.portChip}>
                      <span className={styles.portNum}>{p.port}</span>
                      {p.name && <span className={styles.portName}>{p.name}</span>}
                    </span>
                  ))}
                </div>
              )}
              {!ports && !scanningPorts && (
                <div className={styles.portsEmpty}>click scan to check ports</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function StatCard({ label, value, accent }) {
  return (
    <div className={styles.statCard} style={{ borderTopColor: accent }}>
      <div className={styles.statValue} style={{ color: accent }}>{value}</div>
      <div className={styles.statLabel}>{label}</div>
    </div>
  )
}

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showSettings, setShowSettings] = useState(false)
  const [subnetMapVlan, setSubnetMapVlan] = useState(null)
  const [mainTab, setMainTab] = useState('devices') // 'devices' | 'maps'
  const [search, setSearch] = useState('')
  const [filterType, setFilterType] = useState('all')
  const [filterOnline, setFilterOnline] = useState('all')
  const [filterSource, setFilterSource] = useState('all')
  const [filterSubnet, setFilterSubnet] = useState(null)  // subnet string or null
  const [sortBy, setSortBy] = useState('ip')
  const [sortDir, setSortDir] = useState('asc')
  const [pingLoading, setPingLoading] = useState({})
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefresh, setLastRefresh] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/hosts')
      const json = await res.json()
      setData(json)
      setLastRefresh(Date.now())
    } catch(e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetch('/api/refresh', { method: 'POST' })
    // Poll until scanning is done
    let attempts = 0
    const poll = setInterval(async () => {
      await fetchData()
      attempts++
      if (attempts > 60) { clearInterval(poll); setRefreshing(false) }
    }, 2000)
    setTimeout(() => { clearInterval(poll); setRefreshing(false) }, 120000)
  }

  const handlePing = async (ip) => {
    setPingLoading(p => ({ ...p, [ip]: true }))
    try {
      await fetch(`/api/ping/${ip}`)
      await fetchData()
    } finally {
      setPingLoading(p => ({ ...p, [ip]: false }))
    }
  }

  const allHosts = data?.hosts || []

  const allSources = useMemo(() => {
    const s = new Set()
    allHosts.forEach(h => (h.sources || []).forEach(src => s.add(src)))
    return [...s].sort()
  }, [allHosts])

  const allTypes = useMemo(() => {
    const t = new Set(allHosts.map(h => h.type).filter(Boolean))
    return [...t].sort()
  }, [allHosts])

  const anyFilterActive = search || filterOnline !== 'all' || filterType !== 'all' || filterSource !== 'all' || filterSubnet

  const clearAllFilters = () => {
    setSearch('')
    setFilterOnline('all')
    setFilterType('all')
    setFilterSource('all')
    setFilterSubnet(null)
  }

  const filtered = useMemo(() => {
    let hosts = allHosts

    if (filterOnline === 'online') hosts = hosts.filter(h => h.online === true)
    if (filterOnline === 'offline') hosts = hosts.filter(h => h.online === false)
    if (filterOnline === 'unknown') hosts = hosts.filter(h => h.online == null)
    if (filterType !== 'all') hosts = hosts.filter(h => h.type === filterType)
    if (filterSource !== 'all') hosts = hosts.filter(h => (h.sources || []).includes(filterSource))
    if (filterSubnet) hosts = hosts.filter(h => h.ip && ipInSubnet(h.ip, filterSubnet))

    if (search) {
      const q = search.toLowerCase()
      hosts = hosts.filter(h =>
        h.ip?.includes(q) ||
        h.hostname?.toLowerCase().includes(q) ||
        h.network?.toLowerCase().includes(q) ||
        h.mac?.toLowerCase().includes(q)
      )
    }

    return [...hosts].sort((a, b) => {
      let va, vb
      if (sortBy === 'ip') { va = ipToNum(a.ip || '0.0.0.0'); vb = ipToNum(b.ip || '0.0.0.0') }
      else if (sortBy === 'hostname') { va = (a.hostname || '').toLowerCase(); vb = (b.hostname || '').toLowerCase() }
      else if (sortBy === 'type') { va = a.type || ''; vb = b.type || '' }
      else if (sortBy === 'online') { va = a.online ? 0 : 1; vb = b.online ? 0 : 1 }
      else if (sortBy === 'latency') { va = a.latency_ms ?? 9999; vb = b.latency_ms ?? 9999 }
      else { va = a[sortBy] || ''; vb = b[sortBy] || '' }
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [allHosts, search, filterType, filterOnline, filterSource, filterSubnet, sortBy, sortDir])

  const toggleSort = (col) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortDir('asc') }
  }

  const SortIndicator = ({ col }) => sortBy === col
    ? <span className={styles.sortIndicator}>{sortDir === 'asc' ? '▲' : '▼'}</span>
    : null

  if (loading) {
    return (
      <div className={styles.loading}>
        <div className={styles.loadingInner}>
          <div className={styles.loadingLogo}>HIM</div>
          <div className={styles.loadingText}>INITIALISING HOMELAB IP MONITOR<span className="blink">_</span></div>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.app}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.logo}>
            <span className={styles.logoHim}>HIM</span>
            <span className={styles.logoSub}>HOMELAB IP MONITOR</span>
          </div>
        </div>
        <div className={styles.headerRight}>
          <div className={styles.headerMeta}>
            <span className={styles.metaItem}>
              <span className={styles.metaDot} style={{ background: '#3ddc84' }} />
              {data?.online ?? 0} ONLINE
            </span>
            <span className={styles.metaItem}>
              <span className={styles.metaDot} style={{ background: '#ff4d4d' }} />
              {data?.offline ?? 0} OFFLINE
            </span>
            <span className={styles.metaItem}>
              UPDATED {timeAgo(data?.last_updated)}
            </span>
          </div>
          <button
            className={styles.settingsBtn}
            onClick={() => setShowSettings(true)}
          >
            ⚙ CONFIG
          </button>
          <button
            className={`${styles.refreshBtn} ${refreshing || data?.scanning ? styles.refreshing : ''}`}
            onClick={handleRefresh}
            disabled={refreshing || data?.scanning}
          >
            {refreshing || data?.scanning ? '◌ SCANNING…' : '⟳ REFRESH'}
          </button>
        </div>
      </header>

      {/* Stats */}
      <div className={styles.stats}>
        <StatCard label="TOTAL HOSTS" value={data?.total ?? 0} accent="#3ddc84" />
        <StatCard label="ONLINE" value={data?.online ?? 0} accent="#3ddc84" />
        <StatCard label="OFFLINE" value={data?.offline ?? 0} accent="#ff4d4d" />
        <StatCard label="VLANS" value={data?.vlans?.length ?? 0} accent="#4da6ff" />
        <StatCard label="FILTERED" value={filtered.length} accent="#ffb347" />
      </div>

      {/* Main tabs */}
      <div className={styles.mainTabs}>
        <button
          className={`${styles.mainTab} ${mainTab === 'devices' ? styles.mainTabActive : ''}`}
          onClick={() => setMainTab('devices')}
        >
          ⬡ DEVICES
        </button>
        <button
          className={`${styles.mainTab} ${mainTab === 'maps' ? styles.mainTabActive : ''}`}
          onClick={() => setMainTab('maps')}
        >
          ◈ SUBNET MAPS
        </button>
      </div>

      {/* ── DEVICES TAB ─────────────────────────────────────────────────── */}
      {mainTab === 'devices' && (<>

        {/* VLANs strip — click filters the table */}
        {data?.vlans?.length > 0 && (
          <div className={styles.vlansStrip}>
            {data.vlans.filter(v => v.subnet).map(v => {
              const active = filterSubnet === v.subnet
              const vlanLabel = v.id ? `VLAN ${v.id}` : v.name
              return (
                <div
                  key={v.uid || `${v.id}-${v.subnet}`}
                  className={`${styles.vlanChip} ${active ? styles.vlanChipActive : ''}`}
                  onClick={() => setFilterSubnet(active ? null : v.subnet)}
                  title={`Filter to ${v.subnet}${v.purpose ? ` (${v.purpose})` : ''}`}
                >
                  <span className={styles.vlanId}>{vlanLabel}</span>
                  <span className={styles.vlanName}>{v.name}</span>
                  <span className={styles.vlanSubnet}>{v.subnet}</span>
                  {active && <span className={styles.vlanClear}>✕</span>}
                </div>
              )
            })}
          </div>
        )}

        {/* Controls */}
        <div className={styles.controls}>
          <div className={styles.searchWrap}>
            <span className={styles.searchIcon}>⌕</span>
            <input
              className={styles.searchInput}
              placeholder="search ip, hostname, mac, network…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            {search && (
              <button className={styles.clearBtn} onClick={() => setSearch('')}>✕</button>
            )}
          </div>

          <div className={styles.filters}>
            <select className={styles.filterSelect} value={filterOnline} onChange={e => setFilterOnline(e.target.value)}>
              <option value="all">ALL STATUS</option>
              <option value="online">ONLINE</option>
              <option value="offline">OFFLINE</option>
              <option value="unknown">UNKNOWN</option>
            </select>

            <select className={styles.filterSelect} value={filterType} onChange={e => setFilterType(e.target.value)}>
              <option value="all">ALL TYPES</option>
              {allTypes.map(t => (
                <option key={t} value={t}>{(TYPE_META[t]?.label || t).toUpperCase()}</option>
              ))}
            </select>

            <select className={styles.filterSelect} value={filterSource} onChange={e => setFilterSource(e.target.value)}>
              <option value="all">ALL SOURCES</option>
              {allSources.map(s => {
                const count = allHosts.filter(h => (h.sources || []).includes(s)).length
                return <option key={s} value={s}>{s.toUpperCase()} ({count})</option>
              })}
            </select>

            {anyFilterActive && (
              <button className={styles.clearFiltersBtn} onClick={clearAllFilters}>
                ✕ CLEAR FILTERS
              </button>
            )}
          </div>
        </div>

        {/* Table */}
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.thStatus}></th>
                <th className={`${styles.th} ${styles.sortable}`} onClick={() => toggleSort('ip')}>
                  IP ADDRESS <SortIndicator col="ip" />
                </th>
                <th className={`${styles.th} ${styles.sortable}`} onClick={() => toggleSort('hostname')}>
                  HOSTNAME <SortIndicator col="hostname" />
                </th>
                <th className={`${styles.th} ${styles.sortable}`} onClick={() => toggleSort('type')}>
                  TYPE <SortIndicator col="type" />
                </th>
                <th className={styles.th}>NETWORK</th>
                <th className={styles.th}>VLAN</th>
                <th className={`${styles.th} ${styles.sortable}`} onClick={() => toggleSort('latency')}>
                  LATENCY <SortIndicator col="latency" />
                </th>
                <th className={styles.th}>SOURCE</th>
                <th className={styles.th}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} className={styles.emptyRow}>
                    {allHosts.length === 0
                      ? 'NO HOSTS DISCOVERED — CHECK CONFIGURATION'
                      : 'NO HOSTS MATCH CURRENT FILTERS'
                    }
                  </td>
                </tr>
              ) : (
                filtered.map(h => {
                  const source = (h.sources || [''])[0]
                  const vmid = h.extra?.vmid ?? ''
                  const key = h.ip
                    ? `${h.ip}-${source}`
                    : `noip-${source}-${h.hostname}-${vmid}`
                  return (
                    <HostRow
                      key={key}
                      host={h}
                      onPing={handlePing}
                      pingLoading={!!pingLoading[h.ip]}
                    />
                  )
                })
              )}
            </tbody>
          </table>
        </div>

      </>)}

      {/* ── MAPS TAB ────────────────────────────────────────────────────── */}
      {mainTab === 'maps' && (
        <div className={styles.mapsTab}>
          {data?.vlans?.filter(v => v.subnet).length === 0 ? (
            <div className={styles.mapsEmpty}>
              No VLANs with subnets configured — check UniFi connection
            </div>
          ) : (
            <>
              <div className={styles.mapsHint}>
                Select a subnet to view its IP allocation map
              </div>
              <div className={styles.mapsGrid}>
                {(data?.vlans || []).filter(v => v.subnet).map(v => {
                  // Count hosts in this subnet
                  const inSubnet = allHosts.filter(h => h.ip && ipInSubnet(h.ip, v.subnet))
                  const online  = inSubnet.filter(h => h.online).length
                  const offline = inSubnet.filter(h => h.online === false).length
                  const bits    = parseInt(v.subnet.split('/')[1], 10)
                  const total   = Math.max(0, (1 << (32 - bits)) - 2)

                  return (
                    <div
                      key={v.uid || `${v.id}-${v.subnet}`}
                      className={styles.mapCard}
                      onClick={() => setSubnetMapVlan(v)}
                    >
                      <div className={styles.mapCardHeader}>
                        <span className={styles.mapVlanId}>{v.id ? `VLAN ${v.id}` : v.name}</span>
                        <span className={styles.mapVlanName}>{v.name}</span>
                      </div>
                      <div className={styles.mapCardSubnet}>{v.subnet}</div>
                      <div className={styles.mapCardStats}>
                        <span className={styles.mapStatOnline}>{online} online</span>
                        {offline > 0 && <span className={styles.mapStatOffline}>{offline} offline</span>}
                        <span className={styles.mapStatFree}>{total - inSubnet.length} free</span>
                      </div>
                      {v.dhcp_enabled && v.dhcp_start && (
                        <div className={styles.mapCardDhcp}>
                          DHCP {v.dhcp_start} – {v.dhcp_stop}
                        </div>
                      )}
                      <div className={styles.mapCardArrow}>→</div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}

      <footer className={styles.footer}>
        <span>HIM v{data?.version ?? '17'}</span>
        <span>{filtered.length} / {allHosts.length} hosts displayed</span>
        <span>AUTO-REFRESH 30s</span>
      </footer>

      {showSettings && (
        <Settings onClose={() => { setShowSettings(false); fetchData() }} />
      )}
      {subnetMapVlan && (
        <SubnetMap vlan={subnetMapVlan} onClose={() => setSubnetMapVlan(null)} />
      )}
    </div>
  )
}
