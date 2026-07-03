import React, { useState, useMemo } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { Rollout } from './Rollout'

// Rollouts — phased-by-group APP upgrades/downgrades (UF "Rollout View", trimmed
// for a lab fleet: NO percent thresholds / auto-halt). A rollout is a NAMED,
// STATEFUL entity (persisted on S3 → theia-rollouts): it moves an app group FROM
// its installed SWP version TO a new one, in N SEQUENTIAL sub-groups the operator
// gates (Advance) and can Abort. Rollouts are APP-PLANE ONLY — base/runtime is
// re-provisioned by colony, never rolled.

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

// abi encoded in an artifact/runtime label (bookworm-arm64, amd64, ...).
const ABIS = ['bookworm-arm64', 'focal-arm64', 'ubuntu24', 'amd64']
const abiOf = (key) => ABIS.find((x) => (key || '').includes(x)) || ''
// derive a device's abi from its Mender inventory (matches Deployment._devAbi).
function _devAbi(d) {
  const os = (d.attributes?.os || '').toLowerCase(), k = (d.attributes?.kernel || '').toLowerCase()
  const arch = /aarch64|arm64/.test(k + os) ? 'arm64' : /x86_64|amd64/.test(k) ? 'amd64' : ''
  const distro = /focal|20\.04/.test(os) ? 'focal' : /bookworm|trixie|debian gnu\/linux 1[23]/.test(os) ? 'bookworm'
    : /ubuntu.*24/.test(os) ? 'ubuntu24' : ''
  return [distro, arch].filter(Boolean).join('-')
}

// semver core (abi suffix stripped) → numeric tuple, for from/to compare.
const _semver = (v) => (v || '').split('-')[0].split('.').map((x) => parseInt(x, 10) || 0)
function _direction(frm, to) {
  const a = _semver(frm), b = _semver(to)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0, y = b[i] || 0
    if (y > x) return 'upgrade'
    if (y < x) return 'downgrade'
  }
  return 'same'
}

// New Rollout dialog — NAME the rollout, pick an APP + target version + group.
// The rollout is app-plane only; from_version is inferred from the group's
// installed build; direction (upgrade|downgrade) is derived from from→to.
function NewRolloutDialog({ onClose, onCreated }) {
  const { data: appData } = usePoll(() => api.appsPlane(), [], 60000)
  const { data: gdata } = usePoll(() => api.groups(), [], 60000)
  const groups = gdata?.groups || []
  // the flat published-SWP catalog: {app, version, abi, artifact, ...}
  const swp = useMemo(() => (appData?.swp || []).filter((r) => !r._error && r.app), [appData])

  const [name, setName] = useState('')
  const [app, setApp] = useState('')
  const [toVersion, setToVersion] = useState('')
  const [group, setGroup] = useState('')
  const [phases, setPhases] = useState(2)
  const [when, setWhen] = useState('now')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  // The selected group's accepted devices → their abi set → COMPATIBLE apps only.
  const { data: gdevs } = usePoll(() => group ? api.devices(group, 'accepted') : Promise.resolve({ devices: [] }), [group], 30000)
  const gDevices = gdevs?.devices || []
  const groupAbis = useMemo(() => new Set(gDevices.map(_devAbi).filter(Boolean)), [gdevs])

  // apps whose abi an accepted device in the group runs (no group → all apps).
  const apps = useMemo(() => {
    const compat = (r) => !group || groupAbis.size === 0 || !r.abi || groupAbis.has(r.abi)
    return [...new Set(swp.filter(compat).map((r) => r.app))].sort()
  }, [swp, group, groupAbis])

  // versions published for the chosen app (compat-filtered), newest first.
  const versions = useMemo(() => {
    const compat = (r) => !group || groupAbis.size === 0 || !r.abi || groupAbis.has(r.abi)
    return [...new Set(swp.filter((r) => r.app === app && compat(r)).map((r) => r.version))]
      .sort((a, b) => (_direction(a, b) === 'upgrade' ? 1 : -1))
  }, [swp, app, group, groupAbis])

  // from_version = the version the group's devices currently report installed (if
  // discernible from Mender inventory), for the upgrade/downgrade direction.
  const fromVersion = useMemo(() => {
    const vs = gDevices.map((d) => d.attributes?.artifact_name || d.attributes?.rootfs_image_version || '')
      .map((s) => (s.match(/(\d+\.\d+(\.\d+)?)/) || [])[1]).filter(Boolean)
    return vs[0] || ''
  }, [gDevices])
  const direction = toVersion ? _direction(fromVersion, toVersion) : ''

  const create = async () => {
    if (!name.trim()) { setErr('name the rollout'); return }
    if (!app || !toVersion || !group) { setErr('pick an app, target version, and group'); return }
    setBusy(true); setErr(null)
    try {
      const r = await api.createRollout({
        name: name.trim(), app, to_version: toVersion,
        from_version: fromVersion || undefined, direction: direction || undefined,
        group, phases: Number(phases), when,
      })
      onCreated(r)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[32rem] p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-semibold">New Rollout</h3>
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>

        <label className="block text-xs text-muted mb-1">Name (rollout identity)</label>
        <input className="input w-full mb-3" placeholder="e.g. counter-canary" value={name}
               onChange={(e) => setName(e.target.value)} />

        <label className="block text-xs text-muted mb-1">Group</label>
        <select className="input w-full mb-3" value={group}
                onChange={(e) => { setGroup(e.target.value); setApp(''); setToVersion('') }}>
          <option value="">— pick —</option>
          {groups.map((g) => <option key={g.name} value={g.name}>{g.name} ({g.count})</option>)}
        </select>

        <div className="flex gap-3 mb-3">
          <div className="flex-1">
            <label className="block text-xs text-muted mb-1">App</label>
            <select className="input w-full" value={app}
                    onChange={(e) => { setApp(e.target.value); setToVersion('') }}>
              <option value="">— pick —</option>
              {apps.map((a) => <option key={a} value={a}>{a}</option>)}
              {group && apps.length === 0 && <option value="" disabled>-- no app matches this group's abi --</option>}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs text-muted mb-1">Target version</label>
            <select className="input w-full" value={toVersion} disabled={!app}
                    onChange={(e) => setToVersion(e.target.value)}>
              <option value="">— pick —</option>
              {versions.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
        </div>

        {toVersion && (
          <p className="text-[11px] mb-3">
            <span className="text-muted">{fromVersion || 'installed'}</span>
            <span className="mx-1">{direction === 'downgrade' ? '↓' : '→'}</span>
            <span className="text-accent">{toVersion}</span>
            <span className={`ml-2 badge ${direction === 'downgrade'
              ? 'bg-amber-500/15 text-amber-300' : 'bg-emerald-500/15 text-emerald-300'}`}>{direction || 'upgrade'}</span>
          </p>
        )}

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
          subsequent phase. The rollout is saved by name — it survives a reload.
        </p>
        {err && <div className="text-xs text-red-400 mb-2">{err}</div>}
        <button className="btn w-full" disabled={busy} onClick={create}>
          {busy ? 'creating…' : 'Create Rollout'}
        </button>
      </div>
    </div>
  )
}

// Phase-plan tracker — the active NAMED rollout's sub-groups + Advance/Abort.
// State lives on the server (S3) keyed by name; Advance re-reads it there.
function PhasePlan({ rollout, onClose }) {
  const [doc, setDoc] = useState(rollout)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const phases = doc.phases || []
  const next = phases.find((p) => p.status === 'queued')
  const advance = async () => {
    if (!next) return
    setBusy(true); setErr(null)
    try {
      await api.advanceRollout(doc.name)
      const fresh = await api.getRollout(doc.name)
      setDoc(fresh)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const abort = async () => {
    setBusy(true); setErr(null)
    try {
      await api.abortRollout(doc.name)
      const fresh = await api.getRollout(doc.name)
      setDoc(fresh)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const tgt = doc.target?.group || doc.target?.fleet || ''
  return (
    <div className="card p-3 mb-3 border border-accent/40">
      <div className="flex items-center mb-2">
        <span className="font-semibold text-sm">{doc.name}</span>
        <span className="text-xs text-muted ml-2">
          {doc.app} {doc.from_version || '·'} {doc.direction === 'downgrade' ? '↓' : '→'} {doc.to_version} · {tgt}
        </span>
        <span className={`ml-2 badge ${doc.status === 'aborted' ? 'bg-red-500/15 text-red-300'
          : doc.status === 'completed' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-sky-500/15 text-sky-300'}`}>
          {doc.status}
        </span>
        <button className="btn-ghost ml-auto text-xs" onClick={onClose}>Dismiss</button>
      </div>
      <div className="flex gap-2 flex-wrap mb-2">
        {phases.map((p) => (
          <div key={p.phase} className={`px-2 py-1 rounded text-xs border ${
            p.status === 'queued' ? 'border-edge text-muted'
            : p.status === 'aborted' ? 'border-red-500/40 text-red-300 bg-red-500/10'
            : 'border-accent/50 text-accent bg-accent/10'}`}>
            phase {p.phase} · {p.count} dev · {p.status}
          </div>
        ))}
      </div>

      {/* SW patch-compare: each targeted board's installed version vs the
          rollout's to_version — which are at target (green) vs behind (amber). */}
      {(doc.sw || []).length > 0 && (
        <div className="mb-2">
          <div className="text-xs text-muted mb-1">SW per machine (installed → target {doc.to_version})</div>
          <div className="flex flex-col gap-0.5">
            {doc.sw.map((r) => (
              <div key={r.machine} className="flex items-center gap-2 text-xs">
                <span className="font-medium w-24">{r.machine}</span>
                <span className="text-muted">{r.current || '—'}</span>
                <span className="mx-1">→</span>
                <span>{r.target || '—'}</span>
                <span className={`badge ${r.at_target
                  ? 'bg-emerald-500/15 text-emerald-300' : 'bg-amber-500/15 text-amber-300'}`}>
                  {r.at_target ? 'at target' : 'behind'}
                </span>
                {r.state && <span className="text-muted">{r.state}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
      {err && <div className="text-xs text-red-400 mb-1">{err}</div>}
      <div className="flex gap-2">
        {next && doc.status !== 'aborted'
          ? <button className="btn" disabled={busy} onClick={advance}>
              {busy ? '…' : `Advance → phase ${next.phase} (${next.count} device${next.count > 1 ? 's' : ''})`}
            </button>
          : <span className="text-xs text-ok">{doc.status === 'aborted' ? 'aborted' : 'all phases launched'}</span>}
        {doc.status !== 'aborted' && doc.status !== 'completed' &&
          <button className="btn-ghost text-xs" style={{ color: '#E57373' }} disabled={busy} onClick={abort}>Abort</button>}
      </div>
    </div>
  )
}

export function Rollouts() {
  const { data, loading, refresh } = usePoll(() => api.deployments(), [], 6000)
  // the persisted named rollouts (S3) — the durable entities, restored on reload.
  const { data: rdata, refresh: refreshRollouts } = usePoll(() => api.listRollouts(), [], 8000)
  const [sel, setSel] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const [active, setActive] = useState(null)   // the in-progress phase plan
  const [busyAbort, setBusyAbort] = useState(null)
  const deps = (data?.deployments || []).filter((d) => d.authority !== 'base')  // app rollouts
  const rollouts = (rdata?.rollouts || []).filter((r) => !r._error)

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
        {active && <PhasePlan rollout={active} onClose={() => { setActive(null); refreshRollouts() }} />}

        {/* Named, persisted rollouts (durable across reload) */}
        {rollouts.length > 0 && (
          <div className="mb-4">
            <div className="text-xs text-muted mb-1">Named rollouts</div>
            <div className="flex flex-col gap-1">
              {rollouts.map((r) => (
                <div key={r.name} className="flex items-center gap-2 text-sm card px-3 py-1.5">
                  <span className="font-medium">{r.name}</span>
                  <span className="text-xs text-muted">
                    {r.app} {r.from_version || '·'} {r.direction === 'downgrade' ? '↓' : '→'} {r.to_version}
                  </span>
                  <span className={`badge ${r.status === 'aborted' ? 'bg-red-500/15 text-red-300'
                    : r.status === 'completed' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-sky-500/15 text-sky-300'}`}>
                    {r.status}
                  </span>
                  <button className="btn-ghost text-xs ml-auto" onClick={() => setActive(r)}>Manage</button>
                </div>
              ))}
            </div>
          </div>
        )}

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
        onCreated={(r) => { setShowNew(false); setActive(r); refresh(); refreshRollouts() }} />}
    </div>
  )
}
