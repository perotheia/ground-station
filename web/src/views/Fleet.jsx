import React, { useState, useEffect } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Fleet — the device inventory (UF "Targets"): status dots, the base/app version
// pair, Theia state (Health · SM · UCM), group assignment, Connect (the GS
// funnel), and a per-device MERGED TIMELINE side-panel (colony base + Mender app
// + state, chronological).

// UF status-dot color model. A board is `synced` only when BOTH planes agree
// (Mender accepted AND present in the Observability cluster); otherwise it's
// degraded/registered. base_source=mirror (no live supervisor read) shows amber.
const DOT = {
  'mender+com': ['#4CAF50', 'synchronized (Mender + Observability)'],
  'mender-only': ['#E57373', 'registered, no observability'],
  'com-only': ['#FFB300', 'observed, not enrolled'],
}
function Dot({ s }) {
  const [c, t] = DOT[s] || ['#90A4AE', s || 'unknown']
  return <span title={t} className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: c }} />
}

// A small state pill (Health / SM / UCM). Greys out when the rig hasn't reported.
function Pill({ v }) {
  if (!v) return <span className="text-muted">—</span>
  return <span className="badge bg-edge/40 text-slate-200 font-mono text-[11px]">{v}</span>
}

function ConnectDialog({ onClose, groups }) {
  const { data, refresh } = usePoll(() => api.pending(), [], 5000)
  const pend = data?.pending || []
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const [group, setGroup] = useState('')
  const connect = async (mac) => {
    setBusy(mac); setMsg(null)
    try {
      const fleet = mac.startsWith('dc:a6') ? 'theia-gateway' : 'theia-rig'
      const r = await api.connect(mac, fleet, group || undefined)
      setMsg(`${mac} → ${r.mender?.newly_accepted ? 'accepted' : 'already accepted'}; cluster ${r.observability?.present_in_cluster ? 'present' : 'absent'}`)
      refresh()
    } catch (e) { setMsg(`error: ${e.message}`) }
    setBusy(null)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[30rem] p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-semibold">Connect Device</h3>
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-muted mb-3">
          Boards Mender sees but hasn't accepted. Connect = accept in Mender + verify
          Observability presence. The GS funnel — both, or nothing.
        </p>
        <div className="flex items-center gap-2 mb-2 text-xs">
          <span className="text-muted">Assign to group:</span>
          <select className="input text-xs py-0.5" value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">(none)</option>
            {groups.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
          </select>
        </div>
        {pend.length === 0 && <div className="text-sm text-muted">no pending boards.</div>}
        {pend.map((p) => (
          <div key={p.mac} className="flex items-center gap-2 py-1.5 border-t border-edge/60">
            <span className="font-mono text-xs">{p.mac}</span>
            <span className="badge bg-accent/15 text-accent">pending</span>
            <button className="btn ml-auto" disabled={busy === p.mac} onClick={() => connect(p.mac)}>
              {busy === p.mac ? '…' : 'Connect'}
            </button>
          </div>
        ))}
        {msg && <div className="mt-3 text-xs text-slate-300">{msg}</div>}
      </div>
    </div>
  )
}

// Merged-timeline side panel: colony base events + Mender app deployments + Theia
// state, newest-first, authority-color-coded.
const AUTH_COLOR = { base: '#42A5F5', app: '#AB47BC', state: '#78909C' }
function Timeline({ device, onClose }) {
  const [tl, setTl] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let live = true
    api.deviceTimeline(device.id).then((d) => live && setTl(d)).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [device.id])
  const events = tl?.events || []
  return (
    <div className="fixed inset-0 bg-black/40 flex justify-end z-50" onClick={onClose}>
      <div className="card w-[34rem] h-full p-4 overflow-auto rounded-none" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-1">
          <h3 className="font-semibold">{device.name || device.id?.slice(0, 12)}</h3>
          <Dot s={device.connected} />
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <div className="text-xs text-muted mb-3 flex gap-3 flex-wrap">
          <span>fleet: <span className="text-slate-300">{device.fleet || '—'}</span></span>
          <span>group: <span className="text-slate-300">{device.group || 'ungrouped'}</span></span>
          <span>base: <span className="font-mono text-slate-300">{device.base_version || '—'}</span>
            {device.base_source && <span className="text-muted"> ({device.base_source})</span>}</span>
          <span>app: <span className="text-slate-300">{device.artifact || '—'}</span></span>
        </div>
        {err && <div className="text-xs text-red-400">timeline: {err}</div>}
        {!tl && !err && <div className="text-sm text-muted">loading timeline…</div>}
        {tl?.errors && <div className="text-[11px] text-amber-400 mb-2">partial: {Object.entries(tl.errors).map(([k, v]) => `${k}: ${v}`).join('; ')}</div>}
        <ol className="relative border-l border-edge/60 ml-2">
          {events.map((e, i) => (
            <li key={i} className="ml-4 mb-3">
              <span className="absolute -left-[5px] w-2.5 h-2.5 rounded-full"
                style={{ background: AUTH_COLOR[e.authority] || '#90A4AE' }} />
              <div className="flex items-baseline gap-2">
                <span className="badge text-[10px]" style={{ background: (AUTH_COLOR[e.authority] || '#90A4AE') + '22', color: AUTH_COLOR[e.authority] || '#90A4AE' }}>
                  {e.authority}
                </span>
                <span className="text-sm text-slate-200">{e.title}</span>
                {e.status && <span className="text-[11px] text-muted ml-auto">{e.status}</span>}
              </div>
              {e.detail && <div className="text-[11px] text-muted">{e.detail}</div>}
              {e.ts && <div className="text-[10px] text-muted/70">{String(e.ts)}</div>}
            </li>
          ))}
          {events.length === 0 && tl && <li className="ml-4 text-sm text-muted">no events.</li>}
        </ol>
      </div>
    </div>
  )
}

// Inline group editor — a select that PATCHes the device's group on change.
function GroupCell({ device, groups, onChanged }) {
  const [busy, setBusy] = useState(false)
  const change = async (g) => {
    if (!g || g === device.group) return
    setBusy(true)
    try { await api.assignGroup(device.id, g); onChanged() } catch (e) { alert(`group: ${e.message}`) }
    setBusy(false)
  }
  return (
    <select
      className="bg-transparent text-xs text-muted hover:text-slate-200 outline-none cursor-pointer"
      disabled={busy} value={device.group || ''} onClick={(e) => e.stopPropagation()}
      onChange={(e) => change(e.target.value)}>
      <option value="">{device.group || 'ungrouped'}</option>
      {groups.filter((g) => g.name !== device.group).map((g) => (
        <option key={g.name} value={g.name}>{g.name}</option>
      ))}
    </select>
  )
}

export function Fleet() {
  const { data, loading, error, refresh } = usePoll(() => api.devices(), [], 6000)
  const { data: gdata, refresh: grefresh } = usePoll(() => api.groups(), [], 30000)
  const [showConnect, setShowConnect] = useState(false)
  const [selected, setSelected] = useState(null)
  const devices = data?.devices || []
  const groups = gdata?.groups || []
  const reload = () => { refresh(); grefresh() }
  return (
    <div className="pane h-full">
      <div className="pane-head">
        Fleet
        <span className="text-muted font-normal text-xs ml-2">{devices.length} device(s) · {groups.length} group(s)</span>
        <span className="ml-auto flex gap-1">
          <button className="btn" onClick={() => setShowConnect(true)}>Connect Device</button>
          <button className="btn-ghost" onClick={reload}>Refresh</button>
        </span>
      </div>
      {error && (
        <div className="bg-red-500/15 border-b border-red-500/40 text-red-300 text-xs px-3 py-2">
          ⚠ Can't reach Mender inventory — the fleet can't be read (devices are NOT
          deleted; this is a connectivity/auth error). Detail: {error}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr>
              <th className="th">Device</th><th className="th">Fleet</th><th className="th">Group</th>
              <th className="th">Base runtime</th><th className="th">App</th>
              <th className="th">Health</th><th className="th">SM</th><th className="th">UCM</th>
              <th className="th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && !data && <tr><td className="cell text-muted" colSpan={9}>loading…</td></tr>}
            {error && <tr><td className="cell text-red-300" colSpan={9}>fleet unavailable (Mender error) — see banner above</td></tr>}
            {!loading && !error && devices.length === 0 && <tr><td className="cell text-muted" colSpan={9}>no devices enrolled</td></tr>}
            {devices.map((d) => (
              <tr key={d.id} className="hover:bg-edge/20 cursor-pointer" onClick={() => setSelected(d)}>
                <td className="cell font-mono text-xs">{d.name || d.id?.slice(0, 12)}</td>
                <td className="cell text-xs">{d.fleet || <span className="text-muted">—</span>}</td>
                <td className="cell text-xs"><GroupCell device={d} groups={groups} onChanged={reload} /></td>
                <td className="cell text-xs font-mono">
                  {d.base_version || <span className="text-muted">—</span>}
                  {d.base_source === 'mirror' && <span title="from Mender mirror tag, not a live supervisor read" className="ml-1 text-amber-400">◐</span>}
                </td>
                <td className="cell text-xs">{d.artifact || <span className="text-muted">—</span>}</td>
                <td className="cell text-xs"><Pill v={d.health} /></td>
                <td className="cell text-xs"><Pill v={d.sm_state} /></td>
                <td className="cell text-xs"><Pill v={d.ucm_version} /></td>
                <td className="cell flex items-center gap-2 text-xs">
                  <Dot s={d.connected} />
                  <span className="text-muted">{d.connected}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {showConnect && <ConnectDialog groups={groups} onClose={() => { setShowConnect(false); reload() }} />}
      {selected && <Timeline device={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
