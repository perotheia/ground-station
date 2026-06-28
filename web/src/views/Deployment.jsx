import React, { useState, useMemo } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

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
function Targets({ sel, setSel, onAssigned }) {
  const { data, loading, refresh } = usePoll(() => api.devices(), [], 6000)
  const devices = data?.devices || []
  const selDev = devices.find((d) => d.id === sel)
  const [confirm, setConfirm] = useState(null)   // device id awaiting cleanup confirm
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)

  // zero-arity Cleanup: keep the device enrolled, remove its software
  // (= colony cleanup <rig>). The rig is the device's machine tag (central/…).
  const cleanup = async (d) => {
    const rig = d.attributes?.machine || d.name
    setBusy(d.id); setConfirm(null); setNote(null)
    try {
      const r = await api.deployBase(rig, 'cleanup')
      setNote(`cleanup ${rig}: ${r.ok ? 'ok' : 'failed'}`)
      refresh()
    } catch (e) { setNote(`cleanup error: ${e.message}`) }
    setBusy(null)
  }

  return (
    <div className="pane min-h-0">
      <div className="pane-head">
        Targets
        <span className="ml-auto flex gap-1 text-muted">
          <span className="icon-btn" title="search">⌕</span>
          <span className="icon-btn" title="connect">＋</span>
          <span className="icon-btn" title="filter">▾</span>
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Base</th><th className="th">App</th><th className="th">St</th><th className="th"></th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && <tr><td className="cell text-muted" colSpan={5}>loading…</td></tr>}
            {devices.map((d) => (
              <tr key={d.id} onClick={() => setSel(d.id)}
                  className={`cursor-pointer hover:bg-edge/20 ${sel === d.id ? 'row-sel' : ''}`}>
                <td className="cell font-mono text-xs">{d.name || d.id.slice(0, 10)}</td>
                <td className="cell text-xs text-muted">{d.base_version || '—'}</td>
                <td className="cell text-xs text-muted">{d.artifact || '—'}</td>
                <td className="cell"><StatusDot s={d.connected} /></td>
                <td className="cell text-right whitespace-nowrap">
                  {confirm === d.id
                    ? <RowConfirm label="cleanup" onYes={() => cleanup(d)} onNo={() => setConfirm(null)} />
                    : <button title="cleanup (keep enrolled, remove software)"
                              className="icon-btn"
                              disabled={busy === d.id}
                              onClick={(e) => { e.stopPropagation(); setConfirm(d.id) }}>
                        {busy === d.id ? '…' : '🧹'}
                      </button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <div className="px-3 py-1 text-[11px] text-slate-300 border-t border-edge">{note}</div>}
      {/* Target details (bottom pane) — tabbed Master-Detail (Details | Assigned) */}
      <TargetDetails dev={selDev} onAssigned={() => { refresh(); onAssigned && onAssigned() }} />
      <div className="px-3 py-1.5 border-t border-edge text-[11px] text-muted">
        Total Targets: {devices.length}
      </div>
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

// The bottom Target-Details pane: a tab bar (Details | Assigned) — the in-context
// Master-Detail arity-1 workflow. The Assigned tab applies a distribution without
// a modal, with the runtime-compat gate enforced inline.
function TargetDetails({ dev, onAssigned }) {
  const [tab, setTab] = useState('details')
  if (!dev) return (
    <div className="border-t border-edge bg-sidebar/30 p-3 text-xs text-muted">Select a target to see details.</div>
  )
  return (
    <div className="border-t border-edge bg-sidebar/30 text-xs">
      <div className="flex items-center gap-1 px-3 pt-2">
        <span className="font-semibold text-slate-100 mr-2">{dev.name || dev.id.slice(0, 12)}</span>
        <span className={`tab ${tab === 'details' ? 'tab-active' : ''}`} onClick={() => setTab('details')}>Details</span>
        <span className={`tab ${tab === 'assigned' ? 'tab-active' : ''}`} onClick={() => setTab('assigned')}>Assigned</span>
      </div>
      <div className="p-3">
        {tab === 'details' ? (
          <div className="space-y-1">
            <Kv k="Controller Id" v={dev.id} mono />
            <Kv k="Fleet (type)" v={dev.fleet} />
            <Kv k="Base runtime" v={dev.base_version || '— (no colony deploy yet)'} />
            <Kv k="App" v={dev.artifact || '—'} />
            <Kv k="Connected" v={dev.connected} />
          </div>
        ) : <AssignedTab dev={dev} onAssigned={onAssigned} />}
      </div>
    </div>
  )
}

// The Assigned tab — shows what's on the device + an inline compat-aware picker.
function AssignedTab({ dev, onAssigned }) {
  const { data } = usePoll(() => api.appsPlane(), [], 8000)
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  // app distributions for THIS device's fleet, each tagged compatible/blocked vs
  // the device's installed base_version (the runtime-compat gate, surfaced inline).
  const choices = useMemo(() => {
    const out = []
    const byApp = (data?.tree || {})[dev.fleet] || {}
    for (const [app, vers] of Object.entries(byApp)) {
      for (const v of vers) {
        const need = v.requires_runtime || ''
        const compatible = !need || need === dev.base_version
        out.push({ app, version: v.version, need, compatible,
          label: `${app} ${v.version}`, value: `${app}:${v.version}` })
      }
    }
    return out
  }, [data, dev])

  const assign = async () => {
    const c = choices.find((x) => x.value === pick)
    if (!c) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.publishApp(dev.fleet, c.app, c.version, true)
      setMsg(r.deployment ? `assigned ${r.artifact_name} → ${r.deployment.devices} device(s)` : (r.detail || r.upload || 'done'))
      onAssigned && onAssigned()
    } catch (e) { setMsg(`blocked: ${e.message}`) }
    setBusy(false)
  }

  const picked = choices.find((x) => x.value === pick)
  return (
    <div className="space-y-2">
      <div className="text-muted">Currently: <span className="text-slate-300">{dev.artifact || 'no app'}</span> on base <span className="font-mono text-slate-300">{dev.base_version || '—'}</span></div>
      <div className="flex items-center gap-2">
        <select value={pick} onChange={(e) => setPick(e.target.value)}
                className="bg-ink border border-edge rounded px-2 py-1 text-xs flex-1">
          <option value="">Select a distribution to apply…</option>
          {choices.map((c) => (
            <option key={c.value} value={c.value} disabled={!c.compatible}>
              {c.label}{c.compatible ? '' : ` — needs ${c.need}`}
            </option>
          ))}
        </select>
        <button className="btn" disabled={busy || !picked || !picked.compatible} onClick={assign}>
          {busy ? '…' : 'Assign'}
        </button>
      </div>
      {picked && !picked.compatible && (
        <div className="text-danger text-[11px]">Incompatible: needs base {picked.need}, device runs {dev.base_version || '—'}. Update the base first.</div>
      )}
      {msg && <div className="text-[11px] text-slate-300">{msg}</div>}
    </div>
  )
}

// ── Column 2: Releases (Distributions) ──────────────────────────────────────
function Releases({ selRel, setSelRel }) {
  const { data } = usePoll(() => api.appsPlane(), [], 8000)
  const { data: rt } = usePoll(() => api.runtimePlane(), [], 8000)
  // flatten app plane tree → rows; tag each with its requires_runtime
  const apps = useMemo(() => {
    const out = []
    const tree = data?.tree || {}
    for (const [fleet, byApp] of Object.entries(tree)) {
      for (const [app, vers] of Object.entries(byApp)) {
        for (const v of vers) out.push({ kind: 'app', fleet, app, version: v.version,
          requires: v.requires_runtime || v.requires || '', key: `app:${fleet}/${app}/${v.version}` })
      }
    }
    return out
  }, [data])
  const runtimes = (rt?.releases || []).filter((r) => !r._error)
    .map((r) => ({ kind: 'base', key: `base:${r.key}`, version: r.key || r.version, app: 'runtime+services' }))
  const rows = [...runtimes, ...apps]
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
            <tr><th className="th"></th><th className="th">Name</th><th className="th">Version</th><th className="th">Needs</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {rows.map((r) => (
              <tr key={r.key} onClick={() => setSelRel(r)}
                  className={`cursor-pointer hover:bg-edge/20 ${selRel?.key === r.key ? 'row-sel' : ''}`}>
                <td className="cell">
                  <span className={`badge ${r.kind === 'base' ? 'bg-violet-500/15 text-violet-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{r.kind}</span>
                </td>
                <td className="cell text-xs">{r.kind === 'base' ? 'runtime+services' : `${r.app}`}</td>
                <td className="cell text-xs font-mono text-slate-300">{r.version}</td>
                <td className="cell text-[11px] text-muted">{r.kind === 'app' ? (r.requires || '—') : ''}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td className="cell text-muted" colSpan={4}>no releases</td></tr>}
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

  // the runtime-compat gate: app.requires_runtime must == device.base_version
  const compat = useMemo(() => {
    if (!selRel || selRel.kind !== 'app' || !target) return { ok: true }
    const need = selRel.requires
    const have = target.base_version
    if (!need) return { ok: true, note: 'app unpinned (arch-only)' }
    return { ok: need === have, need, have }
  }, [selRel, target])

  const deploy = async () => {
    if (!selRel || (selRel.kind === 'app' && !target)) return
    setBusy(true); setMsg(null)
    try {
      if (selRel.kind === 'base') {
        const r = await api.deployBase('central', 'orchestrate')  // rig resolution: P4 wiring
        setMsg(`base deploy ${r.ok ? 'OK' : 'failed'} (mirrored=${r.mirrored})`)
      } else {
        if (!compat.ok) { setMsg(`blocked: device runs ${compat.have || '—'}, ${selRel.app} needs ${compat.need}`); setBusy(false); return }
        const r = await api.publishApp(selRel.fleet, selRel.app, selRel.version, true)
        setMsg(r.deployment ? `deployed ${r.artifact_name} → ${r.deployment.devices} device(s)` : (r.upload || 'published'))
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
        {selRel?.kind === 'app' && target && !compat.ok && (
          <span className="badge bg-danger/15 text-danger">incompatible: needs {compat.need}</span>
        )}
        {selRel?.kind === 'app' && target && compat.ok && (
          <span className="badge bg-ok/15 text-ok">compatible</span>
        )}
        <button className="btn ml-auto" disabled={busy || !selRel} onClick={deploy}>
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
