import React, { useState, useMemo, useEffect } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { CreateTargetModal } from '../components/CreateTargetModal'

// The Deployment board — Update Factory's 3-column heart, retargeted to our two
// authorities. Targets | Releases | Action History. Select a target + a release,
// Deploy → routed by release TYPE (base→colony, app→Mender) with the
// runtime-compat gate (an app only lands where base_version == requires_runtime).

function StatusDot({ s }) {
  // UF status colors: synced/pending/error/registered + our base/app coupling
  const map = {
    'mender+com': ['#4CAF50', 'synchronized'],
    'mender-only': ['#E57373', 'no observability'],
    pending: ['#FFC107', 'pending'],
    registered: ['#1E88E5', 'registered'],
  }
  const [c, t] = map[s] || ['#90A4AE', s || 'unknown']
  return <span title={t} className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: c }} />
}

// Inline confirm bubble for a row-level destructive action (no modal).
function RowConfirm({ label, onYes, onNo }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px]">
      <span className="text-muted">{label}?</span>
      <button className="text-ok hover:underline" onClick={(e) => { e.stopPropagation(); onYes() }}>Yes</button>
      <button className="text-danger hover:underline" onClick={(e) => { e.stopPropagation(); onNo() }}>No</button>
    </span>
  )
}

// ── Column 1: Targets (devices) ─────────────────────────────────────────────
// ── Create New Target — SSH-probe enrolment modal ────────────────────────────
// Operator types a Host IP → reload → colony-api SSHes it → prefill Controller ID
// (MAC, the stable Mender identity) + Name (hostname). Type from Mender (stateless).
function Targets({ sel, setSel, onAssigned }) {
  const { data, loading, error, refresh } = usePoll(() => api.devices(), [], 6000)
  const devices = data?.devices || []
  const selDev = devices.find((d) => d.id === sel)
  const [confirm, setConfirm] = useState(null)   // device id awaiting cleanup confirm
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  const act = async (d, fn, label) => {
    setBusy(d.id); setConfirm(null); setNote(null)
    try { await fn(); refresh() }
    catch (e) { setNote(`${label} error: ${e.message}`) }
    setBusy(null)
  }
  // zero-arity Cleanup: keep enrolled, remove software (= colony cleanup <rig>).
  const cleanup = (d) => act(d, async () => {
    const rig = d.attributes?.machine || d.name
    const r = await api.deployBase(rig, 'cleanup')
    setNote(`cleanup ${rig}: ${r.ok ? 'ok' : 'failed'} — progress in Action History`)
  }, 'cleanup')
  const pin = (d) => act(d, () => api.pinDevice(d.id, !d.pinned), 'pin')
  const del = (d) => act(d, () => api.decommission(d.id), 'delete')

  return (
    <div className="pane min-h-0">
      <div className="pane-head">
        Targets
        <span className="ml-auto flex gap-1 text-muted">
          <span className="icon-btn" title="search">⌕</span>
          <span className="icon-btn cursor-pointer" title="Create new Target" onClick={() => setShowCreate(true)}>＋</span>
          <span className="icon-btn" title="filter">▾</span>
        </span>
      </div>
      {error && (
        <div className="bg-red-500/15 border-b border-red-500/40 text-red-300 text-[11px] px-3 py-2">
          ⚠ Mender unreachable — fleet not read (devices NOT deleted). {error}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Base</th><th className="th">App</th><th className="th">St</th><th className="th text-right">ACT</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && !data && <tr><td className="cell text-muted" colSpan={5}>loading…</td></tr>}
            {error && <tr><td className="cell text-red-300" colSpan={5}>fleet unavailable — see banner</td></tr>}
            {!loading && !error && devices.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no devices</td></tr>}
            {devices.map((d) => (
              <tr key={d.id} onClick={() => setSel(d.id)}
                  className={`cursor-pointer hover:bg-edge/20 ${sel === d.id ? 'row-sel' : ''}`}>
                <td className="cell font-mono text-xs">{d.name || d.id.slice(0, 10)}</td>
                <td className="cell text-xs text-muted">{d.base_version || '—'}</td>
                <td className="cell text-xs text-muted">{d.artifact || '—'}</td>
                <td className="cell"><StatusDot s={d.connected} /></td>
                <td className="cell text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  {confirm === `clean:${d.id}`
                    ? <RowConfirm label="cleanup" onYes={() => cleanup(d)} onNo={() => setConfirm(null)} />
                    : confirm === `del:${d.id}`
                    ? <RowConfirm label="delete" onYes={() => del(d)} onNo={() => setConfirm(null)} />
                    : busy === d.id ? <span className="text-muted text-xs">…</span>
                    : <span className="inline-flex gap-0.5">
                        <button className="icon-btn" title={d.pinned ? 'unpin' : 'pin (guard from delete)'}
                                onClick={() => pin(d)}>{d.pinned ? '📌' : '📍'}</button>
                        <button className="icon-btn" title="cleanup (keep enrolled, remove software)"
                                onClick={() => setConfirm(`clean:${d.id}`)}>🧹</button>
                        <button className="icon-btn" title={d.pinned ? 'unpin before delete' : 'delete (decommission)'}
                                disabled={d.pinned}
                                style={{ color: d.pinned ? '#5a6b7d' : '#E57373' }}
                                onClick={() => !d.pinned && setConfirm(`del:${d.id}`)}>🗑</button>
                      </span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <div className="px-3 py-1 text-[11px] text-slate-300 border-t border-edge">{note}</div>}
      <TargetDetails dev={selDev} />
      <div className="px-3 py-1.5 border-t border-edge text-[11px] text-muted">
        Total Targets: {devices.length}
      </div>
      {showCreate && <CreateTargetModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); refresh() }} />}
    </div>
  )
}

function Kv({ k, v, mono }) {
  return (
    <div className="flex gap-2">
      <span className="text-muted w-28 shrink-0">{k}</span>
      <span className={mono ? 'font-mono text-slate-300 break-all' : 'text-slate-300'}>{v}</span>
    </div>
  )
}

// The bottom Target-Details pane — read-only details (the deploy happens via the
// top select-target + select-release + [Deploy →] bar; cleaner than an inline
// assign). Base is the LIVE supervisor-reported release (stateless).
function TargetDetails({ dev }) {
  if (!dev) return (
    <div className="border-t border-edge bg-sidebar/30 p-3 text-xs text-muted">Select a target to see details.</div>
  )
  return (
    <div className="border-t border-edge bg-sidebar/30 p-3 text-xs">
      <div className="font-semibold text-slate-100 mb-1">{dev.name || dev.id.slice(0, 12)}</div>
      <div className="space-y-1">
        <Kv k="Controller Id" v={dev.id} mono />
        <Kv k="Fleet (type)" v={dev.fleet} />
        <Kv k="Base runtime" v={dev.base_version
          ? `${dev.base_version}${dev.base_source === 'live' ? ' (live)' : ''}`
          : '— (no runtime reported)'} />
        <Kv k="App" v={dev.artifact || '—'} />
        <Kv k="Connected" v={dev.connected} />
      </div>
    </div>
  )
}

// ── Column 2: Releases (Distributions) ──────────────────────────────────────
function Releases({ selRel, setSelRel }) {
  const { data, refresh } = usePoll(() => api.appsPlane(), [], 8000)
  const { data: rt, refresh: rtRefresh } = usePoll(() => api.runtimePlane(), [], 8000)
  const [confirm, setConfirm] = useState(null)   // key pending delete-confirm
  const [busy, setBusy] = useState(null)
  // flatten app plane tree → rows; tag each with its requires_runtime + pin/lock
  const apps = useMemo(() => {
    const out = []
    const tree = data?.tree || {}
    for (const [fleet, byApp] of Object.entries(tree)) {
      for (const [app, vers] of Object.entries(byApp)) {
        for (const v of vers) out.push({ kind: 'app', fleet, app, version: v.version,
          requires: v.requires_runtime || v.requires || '', key: `app:${fleet}/${app}/${v.version}`,
          pinned: !!v.pinned, locked: !!v.locked })
      }
    }
    return out
  }, [data])
  const runtimes = (rt?.releases || []).filter((r) => !r._error)
    .map((r) => ({ kind: 'base', key: `base:${r.key}`, rtKey: r.key || r.version,
      version: r.key || r.version, app: 'runtime+services',
      pinned: !!r.pinned, locked: !!r.locked }))
  const rows = [...runtimes, ...apps]

  // ACT — pin (📍/📌) + delete (🗑), same icons + guards as the Targets column.
  // Routed by release kind: base → runtime plane, app → app plane.
  const act = async (r, fn, label) => {
    setBusy(r.key); setConfirm(null)
    try { await fn(); r.kind === 'base' ? rtRefresh() : refresh() }
    catch (e) { alert(`${label}: ${e.message}`) }
    setBusy(null)
  }
  const pin = (r) => act(r, () => r.kind === 'base'
    ? api.pinRuntime(r.rtKey, !r.pinned)
    : api.pinApp(r.fleet, r.app, r.version, !r.pinned), 'pin')
  const del = (r) => act(r, () => r.kind === 'base'
    ? api.deleteRuntime(r.rtKey)
    : api.deleteApp(r.fleet, r.app, r.version), 'delete')

  return (
    <div className="pane min-h-0">
      <div className="pane-head">
        Releases
        <span className="ml-auto flex gap-1 text-muted">
          <span className="icon-btn" title="search">⌕</span>
          <span className="icon-btn" title="filter">▾</span>
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th"></th><th className="th">Name</th><th className="th">Version</th><th className="th">Needs</th><th className="th text-right">ACT</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {rows.map((r) => (
              <tr key={r.key} onClick={() => setSelRel(r)}
                  className={`cursor-pointer hover:bg-edge/20 ${selRel?.key === r.key ? 'row-sel' : ''}`}>
                <td className="cell">
                  <span className={`badge ${r.kind === 'base' ? 'bg-violet-500/15 text-violet-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{r.kind}</span>
                </td>
                <td className="cell text-xs">{r.kind === 'base' ? 'runtime+services' : `${r.app}`}</td>
                <td className="cell text-xs font-mono text-slate-300">{r.version}{r.locked && <span title="locked: deployed, immutable" className="ml-1">🔒</span>}</td>
                <td className="cell text-[11px] text-muted">{r.kind === 'app' ? (r.requires || '—') : ''}</td>
                <td className="cell text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  {confirm === r.key
                    ? <RowConfirm label="delete" onYes={() => del(r)} onNo={() => setConfirm(null)} />
                    : busy === r.key ? <span className="text-muted text-xs">…</span>
                    : <span className="inline-flex gap-0.5">
                        <button className="icon-btn" title={r.pinned ? 'unpin' : 'pin (guard from delete)'}
                                onClick={() => pin(r)}>{r.pinned ? '📌' : '📍'}</button>
                        <button className="icon-btn"
                                title={r.locked ? 'locked: deployed, immutable' : r.pinned ? 'unpin before delete' : 'delete from plane'}
                                disabled={r.pinned || r.locked}
                                style={{ color: (r.pinned || r.locked) ? '#5a6b7d' : '#E57373' }}
                                onClick={() => !r.pinned && !r.locked && setConfirm(r.key)}>🗑</button>
                      </span>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no releases</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="border-t border-edge bg-sidebar/30 p-3 text-xs text-muted">
        {selRel
          ? <div className="space-y-1">
              <div className="font-semibold text-slate-100">{selRel.kind === 'base' ? 'runtime+services' : selRel.app} <span className="font-mono">{selRel.version}</span></div>
              {selRel.kind === 'app' && <Kv k="Requires runtime" v={selRel.requires || '— (unpinned)'} />}
              {selRel.kind === 'app' && <Kv k="Fleet" v={selRel.fleet} />}
              {selRel.kind === 'base' && <div className="text-muted">A platform release. Deploy via colony (base).</div>}
            </div>
          : 'Select a release to see details.'}
      </div>
    </div>
  )
}
// ── Column 3: Action History (deployments) ──────────────────────────────────
function ActionHistory({ targetName }) {
  const { data } = usePoll(() => api.deployments(), [], 6000)
  const rows = (data?.deployments || []).slice(0, 40)
  return (
    <div className="pane min-h-0">
      <div className="pane-head">Action History {targetName ? <span className="text-muted font-normal">: {targetName}</span> : ''}</div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Plane</th><th className="th">Distribution</th><th className="th">Date</th><th className="th">Status</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {rows.map((d) => {
              const s = d.statistics?.status || {}
              const ok = (s.success || 0) > 0 && !(s.failure > 0)
              return (
                <tr key={d.id} className="hover:bg-edge/20">
                  <td className="cell"><span className={`badge ${d.authority === 'base' ? 'bg-violet-500/15 text-violet-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{d.authority || 'app'}</span></td>
                  <td className="cell text-xs">{d.artifact_name || d.name}</td>
                  <td className="cell text-[11px] text-muted">{fmtTs(d.created || d.created_ts)}</td>
                  <td className="cell">{d.status === 'finished'
                    ? <span style={{ color: ok ? '#4CAF50' : '#E57373' }}>{ok ? '✓' : '✕'}</span>
                    : <span className="text-sky-400 text-xs">{d.status}</span>}</td>
                </tr>
              )
            })}
            {rows.length === 0 && <tr><td className="cell text-muted" colSpan={4}>no actions yet</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function fmtTs(c) {
  if (c == null) return ''
  if (typeof c === 'number') return new Date(c * 1000).toISOString().slice(5, 16).replace('T', ' ')
  return String(c).slice(5, 16).replace('T', ' ')
}

export function Deployment() {
  const [selTarget, setSelTarget] = useState(null)
  const [selRel, setSelRel] = useState(null)
  const { data: devData } = usePoll(() => api.devices(), [], 8000)
  const target = (devData?.devices || []).find((d) => d.id === selTarget)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  // The deploy gate. An APP needs a target whose base_version == requires_runtime
  // (no backward compat). A BASE deploy needs a target (the rig to (re)orchestrate).
  // `ready` drives the Deploy button — it's only enabled when the action is valid.
  const gate = useMemo(() => {
    if (!selRel) return { ready: false, why: 'select a release' }
    if (!target) return { ready: false, why: 'select a target' }
    if (selRel.kind === 'base') return { ready: true, app: false }
    // app: fleet must match + runtime must match
    if (selRel.fleet && target.fleet && !String(target.fleet).includes(selRel.fleet))
      return { ready: false, app: true, why: `target is not in fleet ${selRel.fleet}` }
    const need = selRel.requires, have = target.base_version
    if (!need) return { ready: true, app: true, note: 'app unpinned (arch-only)' }
    if (need !== have)
      return { ready: false, app: true, incompatible: true, need, have,
               why: `device runs ${have || '—'}, needs ${need}` }
    return { ready: true, app: true, compatible: true }
  }, [selRel, target])

  const deploy = async () => {
    if (!gate.ready) return
    setBusy(true); setMsg(null)
    try {
      if (selRel.kind === 'base') {
        const rig = target.attributes?.machine || target.name
        // The deploy target IP is the device's reachable_ip (remote_ip||local_ip).
        // A preauthorized device has none → prompt for it (then it's recorded as
        // local_ip + used as the colony host). We DON'T assume later reachability.
        let ip = target.reachable_ip || null
        if (!ip) {
          ip = window.prompt(`No IP on record for ${rig}. Enter the target IP to deploy to:`)
          if (!ip) { setMsg('deploy cancelled — a target IP is required'); setBusy(false); return }
        }
        const r = await api.deployBase(rig, 'orchestrate', ip, target.id)
        setMsg(`base deploy ${rig} @ ${ip}: ${r.ok ? 'OK' : 'started'} — progress in Action History`)
      } else {
        const r = await api.publishApp(selRel.fleet, selRel.app, selRel.version, true)
        setMsg(r.deployment
          ? `deployed ${r.artifact_name} → ${r.deployment.devices} device(s) — see status in Mender UI`
          : (r.detail || r.upload || 'published'))
      }
    } catch (e) { setMsg(`error: ${e.message}`) }
    setBusy(false)
  }

  return (
    <div className="h-full flex flex-col gap-2">
      {/* deploy action bar */}
      <div className="flex items-center gap-3 text-sm">
        <span className="text-muted">Target:</span>
        <span className="font-mono text-slate-200">{target?.name || '— select —'}</span>
        <span className="text-muted">Release:</span>
        <span className="font-mono text-slate-200">{selRel ? `${selRel.kind === 'base' ? 'runtime' : selRel.app} ${selRel.version}` : '— select —'}</span>
        {gate.incompatible && (
          <span className="badge bg-danger/15 text-danger" title={gate.why}>incompatible: needs {gate.need}</span>
        )}
        {gate.compatible && (
          <span className="badge bg-ok/15 text-ok">compatible</span>
        )}
        <button className="btn ml-auto" disabled={busy || !gate.ready} title={gate.why || ''} onClick={deploy}>
          {busy ? 'deploying…' : 'Deploy →'}
        </button>
      </div>
      {msg && <div className="card px-3 py-1.5 text-xs text-slate-300">{msg}</div>}

      {/* 3-column board */}
      <div className="flex-1 grid grid-cols-3 grid-rows-1 gap-2 min-h-0">
        <Targets sel={selTarget} setSel={setSelTarget} />
        <Releases selRel={selRel} setSelRel={setSelRel} />
        <ActionHistory targetName={target?.name} />
      </div>
    </div>
  )
}
