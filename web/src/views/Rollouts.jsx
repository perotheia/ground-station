import React, { useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { Rollout } from './Rollout'

// Rollouts — phased-by-group deployments (UF "Rollout View", trimmed: no percent
// thresholds for a lab fleet). For now this lists active/recent deployments with
// the per-deployment two-plane rollout detail (transport + UCM ladder) on select.

function bar(stats) {
  if (!stats) return null
  const total = Object.values(stats).reduce((a, b) => a + b, 0) || 1
  const seg = [['success', '#4CAF50'], ['downloading', '#1E88E5'], ['installing', '#42A5F5'],
               ['pending', '#FFC107'], ['failure', '#E57373']]
  return (
    <div className="flex h-2 w-40 overflow-hidden rounded bg-ink">
      {seg.map(([k, c]) => {
        const v = stats[k] || 0
        if (!v) return null
        return <div key={k} style={{ width: `${(v / total) * 100}%`, background: c }} title={`${k}: ${v}`} />
      })}
    </div>
  )
}

export function Rollouts() {
  const { data, loading, refresh } = usePoll(() => api.deployments(), [], 6000)
  const [sel, setSel] = useState(null)
  const deps = (data?.deployments || []).filter((d) => d.authority !== 'base')  // app rollouts
  return (
    <div className="pane h-full">
      <div className="pane-head">
        Rollouts
        <span className="text-muted font-normal text-xs ml-2">{deps.length}</span>
        <button className="btn-ghost ml-auto" onClick={refresh}>Refresh</button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Artifact</th><th className="th">Status</th><th className="th">Progress</th><th className="th"></th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && <tr><td className="cell text-muted" colSpan={5}>loading…</td></tr>}
            {deps.map((d) => (
              <tr key={d.id} className="hover:bg-edge/20">
                <td className="cell text-sm">{d.name}</td>
                <td className="cell"><span className="badge bg-slate-500/15 text-slate-300">{d.artifact_name}</span></td>
                <td className="cell text-xs">{d.status}</td>
                <td className="cell">{bar(d.statistics?.status)}</td>
                <td className="cell"><button className="btn-ghost" onClick={() => setSel(d.id)}>Detail</button></td>
              </tr>
            ))}
            {deps.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no rollouts</td></tr>}
          </tbody>
        </table>
      </div>
      {sel && <Rollout depId={sel} onClose={() => setSel(null)} />}
    </div>
  )
}
