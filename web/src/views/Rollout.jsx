import React from 'react'
import { api } from '../api'

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

// ONE serial poll loop, every 5 s: the per-device outcome (Mender) each tick, the ECU plane (a com
// gRPC call per device, can take seconds) every second tick, awaited one after the other, so two
// requests are never in flight. It stops once the outcome is final. Resets on a new deployment.
const POLL_MS = 5000
function useRolloutPoll(depId) {
  const [outcome, setOutcome] = React.useState(null)
  const [ecu, setEcu] = React.useState(null)
  const [error, setError] = React.useState(null)
  React.useEffect(() => {
    let live = true, timer = null, n = 0
    setOutcome(null); setEcu(null); setError(null)
    const tick = async () => {
      let final = false
      try {
        const o = await api.deploymentOutcome(depId)
        if (!live) return
        setOutcome(o); setError(null); final = !!o?.final
      } catch (e) { if (live) setError(e.message) }
      if (live && (n % 2 === 0 || final)) {
        try { const r = await api.rollout(depId); if (live) setEcu(r) } catch { /* ECU plane is best-effort */ }
      }
      n += 1
      if (live && !final) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => { live = false; clearTimeout(timer) }
  }, [depId])
  return { outcome, ecu, error }
}

const OUTCOME_STYLE = {
  success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  rolled_back: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  failure: 'border-red-500/40 bg-red-500/10 text-red-300',
  aborted: 'border-red-500/40 bg-red-500/10 text-red-300',
  in_progress: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
}

// one device: success | rolled back to X | failure | aborted | its live Mender status
export function deviceOutcomeText(d) {
  if (d.outcome === 'rolled_back') return `rolled back to ${d.rolled_back_to}`
  if (d.outcome === 'in_progress') return d.status
  return d.outcome
}

function overallText(o) {
  if (!o) return 'loading…'
  if (o.outcome === 'rolled_back') {
    const to = [...new Set((o.devices || []).map((d) => d.rolled_back_to).filter(Boolean))]
    return `rolled back to ${to.join(', ')}`
  }
  if (o.outcome === 'success') return 'success: committed on every device'
  if (o.outcome === 'in_progress') return 'in progress'
  return o.outcome
}

function DeviceOutcomes({ outcome }) {
  const devs = outcome?.devices || []
  return (
    <div data-testid="rollout-outcome" data-outcome={outcome?.outcome || 'loading'}>
      <div className="text-xs uppercase tracking-wide text-muted mb-2">Devices · progress and outcome</div>
      <div className={`rounded border px-3 py-2 text-sm mb-2 ${OUTCOME_STYLE[outcome?.outcome] || 'border-edge text-muted'}`}
           data-testid="rollout-outcome-text">
        {overallText(outcome)}
      </div>
      <div className="space-y-2">
        {devs.map((d) => (
          <div key={d.id} className="rounded border border-edge/60 p-2" data-testid={`ro-device-${d.name}`}
               data-status={d.status} data-outcome={d.outcome}>
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium">{d.name}</span>
              <span className="badge bg-slate-500/15 text-slate-300" data-testid="device-status">{d.status}</span>
              <span className={`ml-auto badge ${OUTCOME_STYLE[d.outcome] || ''}`} data-testid="device-outcome">
                {deviceOutcomeText(d)}</span>
            </div>
            {(d.reason || []).length > 0 && (
              <pre className="mt-1 text-[11px] text-slate-300 whitespace-pre-wrap break-all bg-ink/60 rounded p-2"
                   data-testid="device-reason">{d.reason.join('\n')}</pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export function Rollout({ depId, onClose }) {
  const { outcome, ecu: data, error } = useRolloutPoll(depId)
  const loading = !data && !error           // first ECU answer not in yet: not "empty"
  const [aborting, setAborting] = React.useState(false)
  const [msg, setMsg] = React.useState(null)

  const dep = { ...(data?.transport?.deployment || {}), ...(outcome ? {
    name: outcome.name, artifact_name: outcome.artifact_name, status: outcome.status } : {}) }
  const stats = data?.transport?.statistics?.status || data?.transport?.statistics || outcome?.statistics || {}
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
    <div className="card p-4 space-y-4" data-testid="rollout-detail">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold">{dep.name || depId.slice(0, 12)}</h3>
          <div className="text-xs text-muted">
            {!outcome && loading ? 'loading…'
              : <>artifact <span className="text-slate-300">{dep.artifact_name}</span> · {dep.status}</>}
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

      <DeviceOutcomes outcome={outcome} />

      {/* TRANSPORT plane */}
      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-2">Transport plane · Mender</div>
        <div className="flex gap-3 text-sm">
          {Object.entries(stats).filter(([, v]) => v > 0).map(([k, v]) => (
            <span key={k} className="badge bg-slate-500/15 text-slate-300">{k}: {v}</span>
          ))}
          {loading && <span className="text-muted text-sm">loading…</span>}
          {!loading && Object.keys(stats).length === 0 && <span className="text-muted text-sm">no per-status data</span>}
        </div>
      </div>

      {/* ECU plane */}
      <div>
        <div className="text-xs uppercase tracking-wide text-muted mb-2">
          ECU plane · ara::ucm + ara::sm (over com)
        </div>
        <div className="space-y-3">
          {loading && <div className="text-muted text-sm">loading… (reads each device over com)</div>}
          {!loading && ecu.length === 0 && <div className="text-muted text-sm">no target devices resolved</div>}
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
