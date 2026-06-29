import React, { useState, useMemo } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { Rollout } from './Rollout'

// Rollouts — phased-by-group deployments (UF "Rollout View", trimmed for a lab
// fleet: NO percent thresholds / auto-halt). Lists active/recent app deployments
// with a per-deployment two-plane detail (transport + UCM ladder), and a New
// Rollout flow that splits a group/fleet into N SEQUENTIAL sub-groups — the
// operator gates each phase (Advance) and can Abort.

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

// New Rollout dialog — pick an APP artifact + group + phase count + Now/Scheduled.
function NewRolloutDialog({ onClose, onCreated }) {
  const { data: appData } = usePoll(() => api.appsPlane(), [], 60000)
  const { data: gdata } = usePoll(() => api.groups(), [], 60000)
  const groups = gdata?.groups || []
  const artifacts = useMemo(() => {
    const out = []
    const tree = appData?.tree || {}
    for (const byApp of Object.values(tree))
      for (const vers of Object.values(byApp))
        for (const v of vers) if (v.artifact) out.push(v.artifact)
    return [...new Set(out)]
  }, [appData])

  const [artifact, setArtifact] = useState('')
  const [group, setGroup] = useState('')
  const [phases, setPhases] = useState(2)
  const [when, setWhen] = useState('now')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const create = async () => {
    if (!artifact || !group) { setErr('pick an artifact and a group'); return }
    setBusy(true); setErr(null)
    try {
      const r = await api.createRollout({ artifact_name: artifact, group, phases: Number(phases), when })
      onCreated(r)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[30rem] p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-semibold">New Rollout</h3>
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <label className="block text-xs text-muted mb-1">Release (app artifact)</label>
        <select className="input w-full mb-3" value={artifact} onChange={(e) => setArtifact(e.target.value)}>
          <option value="">— pick —</option>
          {artifacts.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="block text-xs text-muted mb-1">Group</label>
        <select className="input w-full mb-3" value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">— pick —</option>
          {groups.map((g) => <option key={g.name} value={g.name}>{g.name} ({g.count})</option>)}
        </select>
        <div className="flex gap-4 mb-3">
          <div>
            <label className="block text-xs text-muted mb-1">Phases (sub-groups)</label>
            <input type="number" min={1} max={8} className="input w-20" value={phases}
                   onChange={(e) => setPhases(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Mode</label>
            <select className="input" value={when} onChange={(e) => setWhen(e.target.value)}>
              <option value="now">Now (launch phase 1)</option>
              <option value="scheduled">Scheduled (plan only)</option>
            </select>
          </div>
        </div>
        <p className="text-[11px] text-muted mb-3">
          The group is split into {phases} sequential sub-groups. Phase 1 deploys
          {when === 'now' ? ' immediately' : ' on your first Advance'}; you gate each
          subsequent phase. No percent thresholds — manual Advance / Abort.
        </p>
        {err && <div className="text-xs text-red-400 mb-2">{err}</div>}
        <button className="btn w-full" disabled={busy} onClick={create}>
          {busy ? 'creating…' : 'Create Rollout'}
        </button>
      </div>
    </div>
  )
}

// Phase-plan tracker — the active rollout's sequential sub-groups + Advance.
function PhasePlan({ rollout, onClose }) {
  const [plan, setPlan] = useState(rollout.plan || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const next = plan.find((p) => p.status === 'queued')
  const advance = async () => {
    if (!next) return
    setBusy(true); setErr(null)
    try {
      const r = await api.advanceRollout(rollout.artifact_name, next.name, next.devices)
      setPlan(plan.map((p) => p.phase === next.phase
        ? { ...p, status: 'deploying', deployment_id: r.deployment_id } : p))
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="card p-3 mb-3 border border-accent/40">
      <div className="flex items-center mb-2">
        <span className="font-semibold text-sm">Rollout · {rollout.artifact_name} → {rollout.target}</span>
        <span className="text-xs text-muted ml-2">{rollout.total_devices} device(s), {rollout.phases} phase(s)</span>
        <button className="btn-ghost ml-auto text-xs" onClick={onClose}>Dismiss</button>
      </div>
      <div className="flex gap-2 flex-wrap mb-2">
        {plan.map((p) => (
          <div key={p.phase} className={`px-2 py-1 rounded text-xs border ${
            p.status === 'queued' ? 'border-edge text-muted'
            : 'border-accent/50 text-accent bg-accent/10'}`}>
            phase {p.phase} · {p.count} dev · {p.status}
          </div>
        ))}
      </div>
      {err && <div className="text-xs text-red-400 mb-1">{err}</div>}
      {next
        ? <button className="btn" disabled={busy} onClick={advance}>
            {busy ? '…' : `Advance → phase ${next.phase} (${next.count} device${next.count > 1 ? 's' : ''})`}
          </button>
        : <span className="text-xs text-ok">all phases launched</span>}
    </div>
  )
}

export function Rollouts() {
  const { data, loading, refresh } = usePoll(() => api.deployments(), [], 6000)
  const [sel, setSel] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const [active, setActive] = useState(null)   // the in-progress phase plan
  const [busyAbort, setBusyAbort] = useState(null)
  const deps = (data?.deployments || []).filter((d) => d.authority !== 'base')  // app rollouts

  const abort = async (id) => {
    setBusyAbort(id)
    try { await api.abort(id); refresh() } catch (e) { alert(`abort: ${e.message}`) }
    setBusyAbort(null)
  }
  return (
    <div className="pane h-full">
      <div className="pane-head">
        Rollouts
        <span className="text-muted font-normal text-xs ml-2">{deps.length}</span>
        <span className="ml-auto flex gap-1">
          <button className="btn" onClick={() => setShowNew(true)}>New Rollout</button>
        </span>
      </div>
      <div className="flex-1 overflow-auto p-3">
        {active && <PhasePlan rollout={active} onClose={() => setActive(null)} />}
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Artifact</th><th className="th">Status</th><th className="th">Progress</th><th className="th text-right">ACT</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && <tr><td className="cell text-muted" colSpan={5}>loading…</td></tr>}
            {deps.map((d) => {
              const done = ['finished', 'aborted'].includes(d.status)
              return (
                <tr key={d.id} className="hover:bg-edge/20">
                  <td className="cell text-sm">{d.name}</td>
                  <td className="cell"><span className="badge bg-slate-500/15 text-slate-300">{d.artifact_name}</span></td>
                  <td className="cell text-xs">{d.status}</td>
                  <td className="cell">{bar(d.statistics?.status || d.statistics)}</td>
                  <td className="cell text-right whitespace-nowrap">
                    <button className="btn-ghost text-xs" onClick={() => setSel(d.id)}>Detail</button>
                    <button className="icon-btn" title={done ? 'finished' : 'abort'}
                            disabled={done || busyAbort === d.id}
                            style={{ color: done ? '#5a6b7d' : '#E57373' }}
                            onClick={() => !done && abort(d.id)}>⊘</button>
                  </td>
                </tr>
              )
            })}
            {deps.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no rollouts</td></tr>}
          </tbody>
        </table>
      </div>
      {sel && <Rollout depId={sel} onClose={() => setSel(null)} />}
      {showNew && <NewRolloutDialog onClose={() => setShowNew(false)}
        onCreated={(r) => { setShowNew(false); setActive(r); refresh() }} />}
    </div>
  )
}
