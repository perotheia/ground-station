import React from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// The two-plane rollout view — the Theia OTA superpower over a vanilla Mender UI:
//   TRANSPORT plane (Mender): the deployment landing bits on each device.
//   ECU plane (UCM/SM over com): the AUTOSAR install lifecycle running on the rig.
// The operator sees the bytes arriving AND the install executing, side by side.

const UCM_LADDER = ['IDLE', 'DOWNLOADED', 'VALIDATED', 'STAGED', 'INSTALLING',
                    'RESTARTING', 'VERIFYING', 'ACTIVE']

function UcmLadder({ progress }) {
  const cur = progress?.state ?? 0
  const rollback = progress?.state_name === 'ROLLBACK'
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {UCM_LADDER.map((s, i) => {
        const done = !rollback && i < cur
        const active = !rollback && i === cur
        return (
          <React.Fragment key={s}>
            {i > 0 && <span className={`h-px w-3 ${done ? 'bg-accent' : 'bg-edge'}`} />}
            <span className={`badge ${
              active ? 'bg-accent text-ink' :
              done ? 'bg-accent/20 text-accent' :
              'bg-ink text-muted'
            }`}>{s}</span>
          </React.Fragment>
        )
      })}
      {rollback && <span className="badge bg-red-500/20 text-red-400 ml-2">ROLLBACK</span>}
    </div>
  )
}

function SmBadge({ progress }) {
  if (!progress?.sm_ok) return <span className="text-muted text-xs">SM: —</span>
  const s = progress.sm_state_name
  const color = s === 'UPDATE' ? 'bg-amber-500/20 text-amber-400'
    : s === 'RUNNING' ? 'bg-emerald-500/20 text-emerald-400'
    : s === 'DEGRADED' ? 'bg-red-500/20 text-red-400'
    : 'bg-slate-500/20 text-slate-300'
  return <span className={`badge ${color}`}>SM: {s}</span>
}

export function Rollout({ depId, onClose }) {
  const { data, error } = usePoll(() => api.rollout(depId), [depId], 3000)
  const [aborting, setAborting] = React.useState(false)
  const [msg, setMsg] = React.useState(null)

  const dep = data?.transport?.deployment || {}
  const stats = data?.transport?.statistics?.status || data?.transport?.statistics || {}
  const ecu = data?.ecu || []
  const inflight = ['inprogress', 'pending'].includes(dep.status)

  const doAbort = async () => {
    setAborting(true); setMsg(null)
    try {
      await api.abort(depId)
      setMsg({ ok: true, text: 'deployment aborted — devices roll back' })
    } catch (e) {
      setMsg({ ok: false, text: e.message })
    } finally {
      setAborting(false)
    }
  }

  return (
    <div className="card p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold">{dep.name || depId.slice(0, 12)}</h3>
          <div className="text-xs text-muted">
            artifact <span className="text-slate-300">{dep.artifact_name}</span> · {dep.status}
          </div>
        </div>
        <div className="flex gap-2">
          {inflight && (
            <button className="btn-ghost border-red-500/40 text-red-300" disabled={aborting}
                    onClick={doAbort}>{aborting ? 'Aborting…' : 'Abort'}</button>
          )}
          <button className="btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>

      {error && <div className="text-red-300 text-sm">{error}</div>}
      {msg && <div className={`text-sm ${msg.ok ? 'text-emerald-300' : 'text-red-300'}`}>{msg.text}</div>}

      {/* TRANSPORT plane */}
      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-2">Transport plane · Mender</div>
        <div className="flex gap-3 text-sm">
          {Object.entries(stats).filter(([, v]) => v > 0).map(([k, v]) => (
            <span key={k} className="badge bg-slate-500/15 text-slate-300">{k}: {v}</span>
          ))}
          {Object.keys(stats).length === 0 && <span className="text-muted text-sm">no per-status data</span>}
        </div>
      </div>

      {/* ECU plane */}
      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-2">
          ECU plane · ara::ucm + ara::sm (over com)
        </div>
        <div className="space-y-3">
          {ecu.length === 0 && <div className="text-muted text-sm">no target devices resolved</div>}
          {ecu.map((d) => (
            <div key={d.device} className="rounded border border-edge/60 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-accent text-sm">{d.device.slice(0, 12)}</span>
                <SmBadge progress={d.progress} />
              </div>
              {d.error ? (
                <span className="text-amber-400 text-xs">com unreachable: {d.error}</span>
              ) : d.progress?.ok || d.progress?.state_name ? (
                <div className="space-y-1">
                  <UcmLadder progress={d.progress} />
                  {d.progress?.detail && (
                    <div className="text-xs text-muted">{d.progress.detail}</div>
                  )}
                </div>
              ) : (
                <span className="text-muted text-xs">UCM idle / no install in flight</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
