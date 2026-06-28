import React, { useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { Rollout } from './Rollout'

const STATUS_COLOR = {
  finished: 'bg-emerald-500/15 text-emerald-400',
  inprogress: 'bg-sky-500/15 text-sky-400',
  pending: 'bg-amber-500/15 text-amber-400',
  failed: 'bg-red-500/15 text-red-400',
  aborted: 'bg-slate-500/15 text-slate-400',
}

function StatusBadge({ status }) {
  return <span className={`badge ${STATUS_COLOR[status] || 'bg-slate-500/15 text-slate-300'}`}>{status}</span>
}

// Mender 'created' is an ISO string; colony's is a unix ts (number). Render both.
function fmtCreated(c) {
  if (c == null) return ''
  if (typeof c === 'number') return new Date(c * 1000).toISOString().slice(0, 19).replace('T', ' ')
  return String(c).slice(0, 19).replace('T', ' ')
}

// authority chip — base (colony) vs app (Mender). The one surface, two authorities.
function PlaneBadge({ authority }) {
  const a = authority || 'app'
  const cls = a === 'base'
    ? 'bg-violet-500/15 text-violet-300'   // base = colony
    : 'bg-cyan-500/15 text-cyan-300'        // app = Mender
  return <span className={`badge ${cls}`}>{a}</span>
}

// a compact rollout bar from Mender's per-status statistics
function RolloutBar({ stats }) {
  if (!stats) return null
  const total = Object.values(stats).reduce((a, b) => a + b, 0) || 1
  const seg = [
    ['success', 'bg-emerald-500'],
    ['downloading', 'bg-sky-500'],
    ['installing', 'bg-sky-400'],
    ['rebooting', 'bg-sky-300'],
    ['pending', 'bg-amber-500'],
    ['failure', 'bg-red-500'],
    ['noartifact', 'bg-slate-600'],
  ]
  return (
    <div className="flex h-2 w-40 overflow-hidden rounded bg-ink">
      {seg.map(([k, c]) => {
        const v = stats[k] || 0
        if (!v) return null
        return <div key={k} className={c} style={{ width: `${(v / total) * 100}%` }} title={`${k}: ${v}`} />
      })}
    </div>
  )
}

export function Deployments() {
  const { data, error, loading, refresh } = usePoll(() => api.deployments(), [], 6000)
  const [rolloutId, setRolloutId] = useState(null)

  const deps = data?.deployments || []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <h2 className="text-base font-semibold">Deployments</h2>
        <span className="text-sm text-muted">{deps.length} rollout(s)</span>
        <button onClick={refresh} className="btn-ghost ml-auto">Refresh</button>
      </div>

      {error && <div className="card border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">{error}</div>}

      <div className="card overflow-hidden">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink/40">
            <tr>
              <th className="th">Name</th>
              <th className="th">Plane</th>
              <th className="th">Artifact</th>
              <th className="th">Status</th>
              <th className="th">Rollout</th>
              <th className="th">Created</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/60">
            {loading && <tr><td className="cell text-muted" colSpan={7}>loading…</td></tr>}
            {!loading && deps.length === 0 && (
              <tr><td className="cell text-muted" colSpan={7}>no deployments yet</td></tr>
            )}
            {deps.map((d) => (
              <tr key={d.id} className="hover:bg-ink/30">
                <td className="cell font-medium">{d.name}</td>
                <td className="cell"><PlaneBadge authority={d.authority} /></td>
                <td className="cell"><span className="badge bg-slate-500/15 text-slate-300">{d.artifact_name}</span></td>
                <td className="cell"><StatusBadge status={d.status} /></td>
                <td className="cell"><RolloutBar stats={d.statistics?.status || d.stats} /></td>
                <td className="cell text-muted text-xs">{fmtCreated(d.created || d.created_ts)}</td>
                <td className="cell"><button className="btn-ghost" onClick={() => setRolloutId(d.id)}>Rollout</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rolloutId && <Rollout depId={rolloutId} onClose={() => setRolloutId(null)} />}
    </div>
  )
}
