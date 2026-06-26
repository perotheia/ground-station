import React, { useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

function HealthBadge({ health }) {
  if (!health) return <span className="text-muted text-xs">—</span>
  const map = {
    OK: 'bg-emerald-500/15 text-emerald-400',
    DEGRADED: 'bg-amber-500/15 text-amber-400',
    FAILED: 'bg-red-500/15 text-red-400',
  }
  return <span className={`badge ${map[health] || 'bg-slate-500/15 text-slate-300'}`}>{health}</span>
}

function short(id) {
  return id ? String(id).slice(0, 8) : '—'
}

export function Devices() {
  const { data, error, loading, refresh } = usePoll(() => api.devices(), [], 8000)
  const [sel, setSel] = useState(null)

  const devices = data?.devices || []
  const fleets = data?.fleets || {}

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <h2 className="text-base font-semibold">Fleet</h2>
        <span className="text-sm text-muted">{data ? `${data.count} device(s)` : ''}</span>
        <div className="flex gap-2">
          {Object.entries(fleets).map(([f, n]) => (
            <span key={f} className="badge bg-accent/10 text-accent">
              {f} · {n}
            </span>
          ))}
        </div>
        <button onClick={refresh} className="btn-ghost ml-auto">Refresh</button>
      </div>

      {error && <div className="card border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">{error}</div>}

      <div className="card overflow-hidden">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink/40">
            <tr>
              <th className="th">Device</th>
              <th className="th">Fleet</th>
              <th className="th">Group</th>
              <th className="th">Artifact</th>
              <th className="th">Health</th>
              <th className="th">SM state</th>
              <th className="th">UCM</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/60">
            {loading && (
              <tr><td className="cell text-muted" colSpan={7}>loading…</td></tr>
            )}
            {!loading && devices.length === 0 && (
              <tr><td className="cell text-muted" colSpan={7}>no devices enrolled</td></tr>
            )}
            {devices.map((d) => (
              <tr key={d.id} onClick={() => setSel(d)}
                  className="cursor-pointer hover:bg-ink/30">
                <td className="cell font-mono text-accent">{short(d.id)}</td>
                <td className="cell">{d.fleet || '—'}</td>
                <td className="cell">{d.group || <span className="text-muted">ungrouped</span>}</td>
                <td className="cell">
                  <span className="badge bg-slate-500/15 text-slate-300">{d.artifact || 'unknown'}</span>
                </td>
                <td className="cell"><HealthBadge health={d.health} /></td>
                <td className="cell">{d.sm_state || <span className="text-muted">—</span>}</td>
                <td className="cell">{d.ucm_version || <span className="text-muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sel && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Device <span className="font-mono text-accent">{short(sel.id)}</span></h3>
            <button onClick={() => setSel(null)} className="btn-ghost">Close</button>
          </div>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-edge/40">
              {Object.entries(sel.attributes || {}).map(([k, v]) => (
                <tr key={k}>
                  <td className="cell text-muted w-1/3">{k}</td>
                  <td className="cell font-mono break-all">{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
