import React, { useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Fleet — the device inventory (UF "Targets"), with the connected status dots,
// the base/app version pair, and Connect Device (the GS funnel).

const DOT = {
  'mender+com': ['#4CAF50', 'synchronized'],
  'mender-only': ['#E57373', 'no observability'],
}
function Dot({ s }) {
  const [c, t] = DOT[s] || ['#90A4AE', s || '?']
  return <span title={t} className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: c }} />
}

function ConnectDialog({ onClose }) {
  const { data, refresh } = usePoll(() => api.pending(), [], 5000)
  const pend = data?.pending || []
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const connect = async (mac) => {
    setBusy(mac); setMsg(null)
    try {
      const fleet = mac.startsWith('dc:a6') ? 'theia-gateway' : 'theia-rig'
      const r = await api.connect(mac, fleet)
      setMsg(`${mac} → ${r.mender?.newly_accepted ? 'accepted' : 'already accepted'}; cluster ${r.observability?.present_in_cluster ? 'present' : 'absent'}`)
      refresh()
    } catch (e) { setMsg(`error: ${e.message}`) }
    setBusy(null)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[28rem] p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-semibold">Connect Device</h3>
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-muted mb-3">
          Boards Mender sees but hasn't accepted. Connect = accept in Mender + verify
          Observability presence. The GS funnel — both, or nothing.
        </p>
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

export function Fleet() {
  const { data, loading, refresh } = usePoll(() => api.devices(), [], 6000)
  const [showConnect, setShowConnect] = useState(false)
  const devices = data?.devices || []
  return (
    <div className="pane h-full">
      <div className="pane-head">
        Fleet
        <span className="text-muted font-normal text-xs ml-2">{devices.length} device(s)</span>
        <span className="ml-auto flex gap-1">
          <button className="btn" onClick={() => setShowConnect(true)}>Connect Device</button>
          <button className="btn-ghost" onClick={refresh}>Refresh</button>
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr>
              <th className="th">Device</th><th className="th">Fleet</th><th className="th">Group</th>
              <th className="th">Base runtime</th><th className="th">App</th>
              <th className="th">Health</th><th className="th">Connected</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && <tr><td className="cell text-muted" colSpan={7}>loading…</td></tr>}
            {devices.map((d) => (
              <tr key={d.id} className="hover:bg-edge/20">
                <td className="cell font-mono text-xs">{d.name || d.id.slice(0, 12)}</td>
                <td className="cell text-xs">{d.fleet || <span className="text-muted">—</span>}</td>
                <td className="cell text-xs text-muted">{d.group || 'ungrouped'}</td>
                <td className="cell text-xs font-mono">{d.base_version || <span className="text-muted">—</span>}</td>
                <td className="cell text-xs">{d.artifact || <span className="text-muted">—</span>}</td>
                <td className="cell text-xs">{d.health || <span className="text-muted">—</span>}</td>
                <td className="cell flex items-center gap-2 text-xs">
                  <Dot s={d.connected} />
                  <span className="text-muted">{d.connected}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {showConnect && <ConnectDialog onClose={() => { setShowConnect(false); refresh() }} />}
    </div>
  )
}
