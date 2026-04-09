import { useState, useEffect, useCallback } from 'react'
import s from './SubnetMap.module.css'

const STATUS_COLORS = {
  online:   { bg: 'rgba(61,220,132,0.25)',  border: '#3ddc84', text: '#3ddc84'  },
  offline:  { bg: 'rgba(255,77,77,0.2)',    border: '#ff4d4d', text: '#ff4d4d'  },
  gateway:  { bg: 'rgba(77,166,255,0.25)',  border: '#4da6ff', text: '#4da6ff'  },
  dhcp:     { bg: 'rgba(255,179,71,0.1)',   border: '#4a3010', text: '#6a5a40'  },
  reserved: { bg: 'rgba(106,106,106,0.15)', border: '#333',    text: '#444'     },
  free:     { bg: 'transparent',            border: '#1e3028', text: '#2a4a38'  },
}

function IPCell({ entry, onClick }) {
  const c = STATUS_COLORS[entry.status] || STATUS_COLORS.free
  const lastOctet = entry.ip.split('.').pop()

  return (
    <div
      className={s.cell}
      style={{ background: c.bg, borderColor: c.border }}
      onClick={() => entry.status !== 'free' && entry.status !== 'reserved' && onClick(entry)}
      title={[
        entry.ip,
        entry.hostname || '',
        entry.status,
        entry.in_dhcp ? 'DHCP range' : '',
        entry.is_gateway ? 'gateway' : '',
      ].filter(Boolean).join(' · ')}
    >
      <span className={s.octet} style={{ color: c.text }}>{lastOctet}</span>
      {entry.status === 'online' && <span className={s.dot} />}
      {entry.status === 'offline' && <span className={`${s.dot} ${s.dotOff}`} />}
    </div>
  )
}

function Legend() {
  const items = [
    { status: 'online',   label: 'Online'   },
    { status: 'offline',  label: 'Offline'  },
    { status: 'gateway',  label: 'Gateway'  },
    { status: 'dhcp',     label: 'DHCP range (free)' },
    { status: 'reserved', label: 'Reserved (net/broadcast)' },
    { status: 'free',     label: 'Free' },
  ]
  return (
    <div className={s.legend}>
      {items.map(({ status, label }) => {
        const c = STATUS_COLORS[status]
        return (
          <div key={status} className={s.legendItem}>
            <span className={s.legendSwatch} style={{ background: c.bg, borderColor: c.border }} />
            <span className={s.legendLabel}>{label}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function SubnetMap({ vlan, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('all') // all | used | free

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`/api/subnet-map/${encodeURIComponent(vlan.uid || vlan.id)}`)
      if (!r.ok) {
        const d = await r.json()
        throw new Error(d.detail || `HTTP ${r.status}`)
      }
      setData(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [vlan.uid || vlan.id])

  useEffect(() => { load() }, [load])

  const entries = data?.entries || []
  const visible = entries.filter(e => {
    if (filter === 'used') return ['online','offline','gateway'].includes(e.status)
    if (filter === 'free') return e.status === 'free' || e.status === 'dhcp'
    return true
  })

  // Group into rows of 16 for display
  const rows = []
  for (let i = 0; i < visible.length; i += 16) {
    rows.push(visible.slice(i, i + 16))
  }

  return (
    <div className={s.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={s.modal}>

        {/* Header */}
        <div className={s.header}>
          <div className={s.headerLeft}>
            <div className={s.title}>
              <span className={s.titleIcon}>⬡</span>
              SUBNET MAP
            </div>
            <div className={s.subtitle}>
              {vlan.id ? `VLAN ${vlan.id} · ` : ""}{vlan.name} · {vlan.subnet}
              {vlan.dhcp_enabled && vlan.dhcp_start && (
                <span className={s.dhcpRange}>
                  · DHCP {vlan.dhcp_start} – {vlan.dhcp_stop}
                </span>
              )}
            </div>
          </div>
          <button className={s.closeBtn} onClick={onClose}>✕</button>
        </div>

        {/* Stats bar */}
        {data && (
          <div className={s.stats}>
            <div className={s.stat}>
              <span className={s.statVal}>{data.total_ips}</span>
              <span className={s.statLabel}>TOTAL</span>
            </div>
            <div className={s.stat}>
              <span className={s.statVal} style={{ color: '#3ddc84' }}>{data.online}</span>
              <span className={s.statLabel}>ONLINE</span>
            </div>
            <div className={s.stat}>
              <span className={s.statVal} style={{ color: '#ff4d4d' }}>{data.offline}</span>
              <span className={s.statLabel}>OFFLINE</span>
            </div>
            <div className={s.stat}>
              <span className={s.statVal} style={{ color: 'var(--text-secondary)' }}>{data.free}</span>
              <span className={s.statLabel}>FREE</span>
            </div>
            <div className={s.stat}>
              <span className={s.statVal} style={{ color: '#ffb347' }}>
                {data.total_ips > 0 ? Math.round((data.used / data.total_ips) * 100) : 0}%
              </span>
              <span className={s.statLabel}>USED</span>
            </div>
          </div>
        )}

        {/* Filter tabs */}
        <div className={s.filterBar}>
          {['all', 'used', 'free'].map(f => (
            <button
              key={f}
              className={`${s.filterTab} ${filter === f ? s.filterTabActive : ''}`}
              onClick={() => setFilter(f)}
            >
              {f.toUpperCase()}
            </button>
          ))}
          <button className={s.refreshBtn} onClick={load} disabled={loading}>
            {loading ? '◌' : '⟳'}
          </button>
          <Legend />
        </div>

        {/* Content */}
        <div className={s.body}>
          {loading && (
            <div className={s.loading}>GENERATING MAP<span className="blink">_</span></div>
          )}
          {error && (
            <div className={s.errorMsg}>✗ {error}</div>
          )}
          {!loading && !error && (
            <div className={s.grid}>
              {rows.map((row, ri) => (
                <div key={ri} className={s.row}>
                  <span className={s.rowLabel}>
                    {/* Show the /16 prefix of first cell in row */}
                    {row[0]?.ip.split('.').slice(0, 3).join('.')}
                  </span>
                  {row.map(entry => (
                    <IPCell
                      key={entry.ip}
                      entry={entry}
                      onClick={setSelected}
                    />
                  ))}
                </div>
              ))}
              {visible.length === 0 && (
                <div className={s.empty}>No entries to display</div>
              )}
            </div>
          )}
        </div>

        {/* Selected IP detail panel */}
        {selected && (
          <div className={s.detail}>
            <div className={s.detailHeader}>
              <span
                className={s.detailIp}
                style={{ color: STATUS_COLORS[selected.status]?.text || 'var(--green-bright)' }}
              >
                {selected.ip}
              </span>
              {selected.hostname && (
                <span className={s.detailHostname}>{selected.hostname}</span>
              )}
              <span className={s.detailStatus}>{selected.status.toUpperCase()}</span>
              <button className={s.detailClose} onClick={() => setSelected(null)}>✕</button>
            </div>
            <div className={s.detailBody}>
              {selected.type && (
                <span className={s.detailChip}>TYPE: {selected.type}</span>
              )}
              {selected.mac && (
                <span className={s.detailChip}>MAC: {selected.mac}</span>
              )}
              {selected.ip_assignment && (
                <span className={s.detailChip}>
                  ASSIGNMENT: {selected.ip_assignment.toUpperCase()}
                </span>
              )}
              {selected.is_gateway && (
                <span className={s.detailChip} style={{ color: '#4da6ff' }}>GATEWAY</span>
              )}
              {selected.in_dhcp && (
                <span className={s.detailChip} style={{ color: '#ffb347' }}>IN DHCP RANGE</span>
              )}
              {(selected.sources || []).map(src => (
                <span key={src} className={s.detailChip}>{src}</span>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
