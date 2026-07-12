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

// Cleanup scope dialog: pick which layers to remove. app = the SWP overlay
// (keep the runtime); runtime = the whole /opt/theia base; mender-state = the
// device mender key/identity (forces re-enrol). Default app+runtime.
function CleanupDialog({ device, onClose, onRun }) {
  const [app, setApp] = useState(true)
  const [runtime, setRuntime] = useState(true)
  const [mender, setMender] = useState(false)
  const Row = ({ on, set, name, desc }) => (
    <label className="flex items-start gap-2 text-sm py-1 cursor-pointer">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} className="mt-1" />
      <span><span className="text-slate-200">{name}</span>
        <span className="block text-[11px] text-muted">{desc}</span></span>
    </label>
  )
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[28rem] p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-medium flex-1" style={{ color: '#E57373' }}>Cleanup {device.name || device.id?.slice(0, 12)}</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>✕</button>
        </div>
        <div className="text-xs text-muted mb-2">Remove the selected layers (the device stays enrolled):</div>
        <Row on={app} set={setApp} name="App (Software Package)" desc="the SWP overlay — app FCs + executor nodes; keeps the runtime" />
        <Row on={runtime} set={setRuntime} name="Runtime (base)" desc="the whole /opt/theia — supervisor + services + app" />
        <Row on={mender} set={setMender} name="Mender state" desc="the device mender key + identity — forces RE-ENROL. Leave off normally." />
        <button className="btn w-full mt-3" disabled={!app && !runtime && !mender}
                onClick={() => onRun({ app, runtime, mender })}>Cleanup →</button>
      </div>
    </div>
  )
}

function Targets({ sel, setSel, onAssigned }) {
  const { data, loading, error, refresh } = usePoll(() => api.devices(), [], 6000)
  const devices = data?.devices || []
  const selDev = devices.find((d) => d.id === sel)
  const [confirm, setConfirm] = useState(null)   // device id awaiting del confirm
  const [cleanDlg, setCleanDlg] = useState(null)  // device awaiting cleanup-scope dialog
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
  const cleanup = (d, scope) => act(d, async () => {
    const rig = d.attributes?.machine || d.name
    const r = await api.deployBase(rig, 'cleanup', undefined, d.id, scope)
    const layers = Object.entries(scope).filter(([, v]) => v).map(([k]) => k).join('+')
    setNote(`cleanup ${rig} [${layers}]: ${r.ok ? 'ok' : 'failed'} — progress in Action History`)
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
            <tr><th className="th">Name</th><th className="th">Base</th><th className="th">SWP</th><th className="th">St</th><th className="th text-right">ACT</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && !data && <tr><td className="cell text-muted" colSpan={5}>loading…</td></tr>}
            {error && <tr><td className="cell text-red-300" colSpan={5}>fleet unavailable — see banner</td></tr>}
            {!loading && !error && devices.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no devices</td></tr>}
            {devices.map((d) => (
              <tr key={d.id} onClick={() => setSel(sel === d.id ? null : d.id)}
                  className={`cursor-pointer hover:bg-edge/20 ${sel === d.id ? 'row-sel' : ''}`}
                  title={sel === d.id ? 'click again to clear the filter' : 'filter Action History to this target'}>
                <td className="cell font-mono text-xs">{d.name || d.id.slice(0, 10)}</td>
                <td className="cell text-xs text-muted">{d.base_version || '—'}</td>
                <td className="cell text-xs text-muted">{d.artifact || '—'}</td>
                <td className="cell"><StatusDot s={d.connected} /></td>
                <td className="cell text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  {confirm === `del:${d.id}`
                    ? <RowConfirm label="delete" onYes={() => del(d)} onNo={() => setConfirm(null)} />
                    : busy === d.id ? <span className="text-muted text-xs">…</span>
                    : <span className="inline-flex gap-0.5">
                        <button className="icon-btn" title={d.pinned ? 'unpin' : 'pin (guard from delete)'}
                                onClick={() => pin(d)}>{d.pinned ? '📌' : '📍'}</button>
                        <button className="icon-btn" title="cleanup (keep enrolled, remove software)"
                                onClick={(e) => { e.stopPropagation(); setCleanDlg(d) }}>🧹</button>
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
      {cleanDlg && <CleanupDialog device={cleanDlg}
        onClose={() => setCleanDlg(null)}
        onRun={(scope) => { const d = cleanDlg; setCleanDlg(null); cleanup(d, scope) }} />}
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

// One Action-History entry per colony/Mender action. Each colony KIND
// (provision / orchestrate / cleanup) and the app (Mender) are SEPARATE rows, so
// the operator follows progress per layer: pending → installing → OK/NOK.
const ACTION_LABEL = {
  provision:   ['Provision', 'etcd / network / mender'],
  orchestrate: ['Orchestrate', 'runtime + services (base)'],
  cleanup:     ['Cleanup', 'remove software'],
}
// lifecycle → {text, color}. colony: pending/inprogress/finished(+stats);
// Mender: pending/inprogress/finished/failure/already-installed/aborted.
const _OK = { text: 'OK', color: '#4CAF50' }
const _NOK = { text: 'NOK', color: '#E57373' }
const _CANCELLED = { text: 'cancelled', color: '#E57373' }
function lifecycle(d) {
  const st = d.status
  // base (colony) carries a shell return code: rc!=0 is a FAILED orchestrate even
  // though the job "finished". Trust rc when present.
  if (d.authority === 'base' && d.rc != null && d.rc !== 0) return _NOK
  if (st === 'aborted') return _CANCELLED
  if (st === 'finished' || st === 'finished/success' || st === 'success') {
    const s = d.statistics?.status || {}
    // A Mender deployment that was cancelled reports finished with aborted>0 (and
    // often noartifact>0) — NOT a success. Count aborted/failure/noartifact as bad.
    if ((s.aborted || 0) > 0) return _CANCELLED
    if ((s.failure || 0) > 0 || (s.noartifact || 0) > 0) return _NOK
    return _OK
  }
  if (st === 'failure') return _NOK
  if (st === 'inprogress' || st === 'installing' || st === 'downloading')
    return { text: 'installing…', color: '#FFB300' }
  if (st === 'already-installed') return _OK
  return { text: st || 'pending', color: '#64B5F6' }   // pending / scheduled / …
}
function actionLabel(d) {
  if (d.authority === 'base') {
    const [name, sub] = ACTION_LABEL[d.kind] || [d.kind || 'base', '']
    return { name, sub: `${d.rig || ''}${sub ? ' · ' + sub : ''}`.trim() }
  }
  // app (Mender): the artifact is the SWP; the deployment name carries the role.
  return { name: 'App', sub: d.artifact_name || d.name || '' }
}

const _FINISHED = ['finished', 'finished/success', 'success', 'failure', 'aborted', 'already-installed']
const isLive = (d) => !_FINISHED.includes(d.status)
const epochOf = (d) => {
  const c = d.created || d.created_ts
  if (typeof c === 'number') return c
  const t = Date.parse(c); return isNaN(t) ? 0 : t / 1000
}

// A CancelBtn — the ACT [x] on any live action or a deployment group. Disabled
// (dimmed ×) once the action is finished; spins while the cancel is in flight.
function CancelBtn({ live, onCancel, title }) {
  const [busy, setBusy] = useState(false)
  if (!live) return <span className="text-muted/40 text-xs" title="finished">×</span>
  return (
    <button className="text-danger hover:text-red-300 text-sm leading-none px-1 disabled:opacity-50"
      disabled={busy} title={title || 'cancel'}
      onClick={async (e) => { e.stopPropagation(); setBusy(true); try { await onCancel() } finally { setBusy(false) } }}>
      {busy ? '…' : '×'}
    </button>
  )
}

// Group the flat action rows into DEPLOYMENTS. A Distribution deploy fans out
// per role into base + app rows created together; correlate them by the deploy
// name prefix (app rows: "<dist>-<role>-<swp>") + a coarse timestamp bucket, so
// one operator click shows as ONE Deployment parent with its child actions.
// A Distribution deploy fans out per role into base (Orchestrate) + app (App)
// rows created in the SAME instant. Correlate the WHOLE fan-out — base AND app —
// into one Deployment, keyed by the distribution name + a coarse time bucket.
// The app row's name is "<dist>-<role>-<swp>"; the base row carries no dist name,
// so we recover the <dist> from the app rows in the same time bucket and fold the
// base rows (matched by that bucket) under it. A base row with no app sibling in
// its bucket (a bare colony action — cleanup/provision) stays its own group.
function groupDeployments(rows) {
  const bucket = (d) => Math.round(epochOf(d) / 60)    // 1-min bucket
  const distOf = (name) => {
    const m = String(name || '').match(/^(.*?)-[a-z0-9]+-[^-]+-[\d.]+/i)
    return m ? m[1] : null
  }
  // 1) collect the distribution name present in each time bucket (from app rows).
  const bucketDist = new Map()
  for (const d of rows) {
    if (d.authority === 'app') {
      const dist = distOf(d.name)
      if (dist) bucketDist.set(bucket(d), dist)
    }
  }
  const key = (d) => {
    const b = bucket(d)
    const dist = bucketDist.get(b)   // a distribution deploy happened this bucket
    if (dist) return `${dist}@${b}`  // fold BOTH base + app of that fan-out together
    // otherwise: a standalone action (bare orchestrate/cleanup, or an app with no
    // recognizable dist name) — its own group.
    return d.authority === 'app'
      ? `${distOf(d.name) || d.artifact_name || d.name}@${b}`
      : `${d.rig || 'base'}:${d.kind || ''}@${b}`
  }
  const groups = new Map()
  for (const d of rows) {
    const k = key(d)
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k).push(d)
  }
  // Sort children within a group: base (Orchestrate) first, then app — reads as
  // "provision the runtime, then overlay the app".
  const ord = (d) => (d.authority === 'base' ? 0 : 1)
  return [...groups.entries()].map(([k, items]) => ({
    k, items: items.sort((a, b) => ord(a) - ord(b) || epochOf(a) - epochOf(b)),
  })).sort((a, b) => Math.max(...b.items.map(epochOf)) - Math.max(...a.items.map(epochOf)))
}

function ActionHistory({ targetName }) {
  const { data, refresh } = usePoll(() => api.deployments(), [], 4000)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState({})           // group key → expanded?
  // GLOBAL cleared-before epoch (not per-target — one shared surface; a deleted
  // target must not orphan its history). Hides app (Mender) rows older than the
  // last Clear; base rows are pruned server-side.
  const clearedBefore = Number(localStorage.getItem('gs.clearedBefore') || 0)

  const doClear = async () => {
    setBusy(true)
    const before = Date.now() / 1000
    try {
      await api.clearActions(null, before)       // rig=null → GLOBAL prune
      localStorage.setItem('gs.clearedBefore', String(before))
      refresh()
    } catch (e) { /* surfaced by the next poll */ }
    setBusy(false)
  }
  const cancel = async (d) => { try { await api.cancelAction(d.id, d.authority || 'app') } finally { refresh() } }

  let rows = data?.deployments || []
  // TOGGLE filter: a selected target scopes the view; deselect → global.
  if (targetName) {
    rows = rows.filter((d) => (d.authority === 'base' && d.rig === targetName)
      || (d.authority === 'app' && String(d.name || '').includes(targetName)))
  }
  if (clearedBefore) rows = rows.filter((d) => d.authority !== 'app' || epochOf(d) > clearedBefore)
  const groups = groupDeployments(rows).slice(0, 40)

  return (
    <div className="pane min-h-0">
      <div className="pane-head flex items-center">Action History
        {targetName && <span className="text-muted font-normal">: {targetName}</span>}
        <button className="btn-ghost text-[11px] ml-auto" disabled={busy}
                title="prune finished base actions everywhere + hide older app actions (global)"
                onClick={doClear}>{busy ? '…' : 'Clear all'}</button></div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Plane</th><th className="th">Action</th><th className="th">Date</th><th className="th">Status</th><th className="th text-right">Act</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {groups.map(({ k, items }) => {
              const anyLive = items.some(isLive)
              const texts = items.map((d) => lifecycle(d).text)
              const worst = texts.includes('NOK') ? 'NOK'
                : texts.includes('cancelled') ? 'cancelled'
                : anyLive ? 'in progress' : 'OK'
              const worstColor = worst === 'OK' ? '#4CAF50'
                : worst === 'in progress' ? '#FFB300' : '#E57373'
              const single = items.length === 1
              const expanded = single || open[k]
              // Parent (Deployment) row — cancels ALL its live children at once.
              const parent = !single && (
                <tr key={k} className="hover:bg-edge/20 bg-edge/10 cursor-pointer"
                    onClick={() => setOpen((o) => ({ ...o, [k]: !o[k] }))}>
                  <td className="cell"><span className="badge bg-slate-500/20 text-slate-300">deploy</span></td>
                  <td className="cell text-xs"><span className="text-slate-100">{open[k] ? '▾' : '▸'} Deployment</span>
                    <span className="block text-[10px] text-muted font-mono">{k.split('@')[0]} · {items.length} action(s)</span></td>
                  <td className="cell text-[11px] text-muted">{fmtTs(Math.max(...items.map(epochOf)))}</td>
                  <td className="cell text-xs" style={{ color: worstColor }}>{worst}</td>
                  <td className="cell text-right">
                    <CancelBtn live={anyLive} title="cancel all live actions in this deployment"
                      onCancel={async () => { for (const d of items) if (isLive(d)) await cancel(d) }} />
                  </td>
                </tr>
              )
              const childRows = (expanded ? items : []).map((d) => {
                const lc = lifecycle(d); const a = actionLabel(d); const live = isLive(d)
                return (
                  <tr key={d.id} className={`hover:bg-edge/20 ${single ? '' : 'bg-black/10'}`}>
                    <td className={`cell ${single ? '' : 'pl-5'}`}><span className={`badge ${d.authority === 'base' ? 'bg-violet-500/15 text-violet-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{d.authority || 'app'}</span></td>
                    <td className="cell text-xs"><span className="text-slate-200">{a.name}</span>
                      {a.sub && <span className="block text-[10px] text-muted font-mono">{a.sub}</span>}</td>
                    <td className="cell text-[11px] text-muted">{fmtTs(d.created || d.created_ts)}</td>
                    <td className="cell text-xs" style={{ color: lc.color }}>
                      {live && <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 animate-pulse" style={{ background: lc.color }} />}
                      {lc.text}
                    </td>
                    <td className="cell text-right"><CancelBtn live={live} onCancel={() => cancel(d)} /></td>
                  </tr>
                )
              })
              return <React.Fragment key={k}>{parent}{childRows}</React.Fragment>
            })}
            {groups.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no actions yet</td></tr>}
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

// ── Column 2 (P4): Distributions — pick a PREPARED distribution to deploy ────
function DistributionsColumn({ sel, setSel }) {
  const { data } = usePoll(() => api.distributions(), [], 10000)
  const dists = (data?.distributions || []).filter((d) => !d._error)
  return (
    <div className="pane min-h-0">
      <div className="pane-head">Distributions
        <span className="text-muted font-normal text-xs ml-2">{dists.length}</span></div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Version</th><th className="th">Arity</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {dists.length === 0 && <tr><td className="cell text-muted" colSpan={3}>no distributions — prepare one in Distributions</td></tr>}
            {dists.map((d) => {
              const k = `${d.name}/${d.version}`
              return (
                <tr key={k} onClick={() => setSel(d)}
                    className={`cursor-pointer hover:bg-edge/20 ${sel && `${sel.name}/${sel.version}` === k ? 'row-sel' : ''}`}>
                  <td className="cell text-sm">{d.name}</td>
                  <td className="cell font-mono text-xs">{d.version}</td>
                  <td className="cell text-xs"><span className="badge bg-amber-500/15 text-amber-300">/{d.arity || (d.roles?.length || 1)}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="border-t border-edge bg-sidebar/30 p-3 text-xs text-muted">
        {sel
          ? <div className="space-y-1">
              <div className="font-semibold text-slate-100">{sel.name} <span className="font-mono">{sel.version}</span> · arity {sel.arity || sel.roles?.length}</div>
              {(sel.roles || []).map((r) => (
                <div key={r.role} className="font-mono text-[11px]"><span className="text-slate-200">{r.role}</span>
                  <span className="text-muted"> · {r.abi} · {r.runtime_build}{(r.swp_build || r.app_build) ? ` · ${r.swp_build || r.app_build}` : ''}</span></div>
              ))}
            </div>
          : 'Select a distribution to deploy.'}
      </div>
    </div>
  )
}

// Role → compatible-machine assignment dialog (deploy a distribution). Only
// machines whose probed abi matches a role's abi are offered for that role.
// A device's abi (mirrors the backend _rig_abi heuristic): os+kernel →
// bookworm-arm64 / focal-arm64 / amd64 / …
function _devAbi(d) {
  const os = (d.attributes?.os || '').toLowerCase(), k = (d.attributes?.kernel || '').toLowerCase()
  const arch = /aarch64|arm64/.test(k + os) ? 'arm64' : /x86_64|amd64/.test(k) ? 'amd64' : ''
  // LSB codenames — must match the target registry abi_key (targets.bzl):
  // noble (24.04), jammy (22.04), focal (20.04), bookworm (Debian 12/trixie).
  const distro = /focal|20\.04/.test(os) ? 'focal'
    : /jammy|22\.04/.test(os) ? 'jammy'
    : /noble|24\.04/.test(os) ? 'noble'
    : /bookworm|trixie|debian gnu\/linux 1[23]/.test(os) ? 'bookworm' : ''
  return [distro, arch].filter(Boolean).join('-')
}

function DeployDistDialog({ dist, devices, onClose, onDone }) {
  const [assign, setAssign] = useState({})   // role -> device_id
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState(null)
  const roles = dist.roles || []
  const devAbi = _devAbi
  const deploy = async () => {
    const assignments = roles.map((r) => ({ role: r.role, device_id: assign[r.role] }))
    if (assignments.some((a) => !a.device_id)) { setMsg('assign a machine to every role'); return }
    setBusy(true); setMsg(null)
    try {
      const res = await api.deployDistribution({ name: dist.name, version: dist.version, assignments })
      onDone(res)
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[34rem] p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-medium flex-1" style={{ color: '#4A90E2' }}>Deploy {dist.name}:{dist.version}</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>✕</button>
        </div>
        <div className="text-xs text-muted mb-3">Assign each role a compatible machine (abi must match):</div>
        <div className="space-y-3">
          {roles.map((r) => {
            const compatible = devices.filter((d) => !r.abi || devAbi(d) === r.abi)
            return (
              <div key={r.role} className="flex items-center gap-2 text-sm">
                <span className="w-40 text-right text-xs"><span className="font-semibold text-slate-200">{r.role}</span>
                  <span className="text-muted"> · {r.abi}</span></span>
                <select className="input flex-1 text-sm" value={assign[r.role] || ''}
                        onChange={(e) => setAssign({ ...assign, [r.role]: e.target.value })}>
                  <option value="">— pick a {r.abi} machine —</option>
                  {compatible.map((d) => <option key={d.id} value={d.id}>{d.name || d.id.slice(0, 12)} ({devAbi(d) || '?'})</option>)}
                </select>
              </div>
            )
          })}
        </div>
        {msg && <div className="text-xs text-red-400 mt-2">{msg}</div>}
        <button className="btn w-full mt-4" disabled={busy} onClick={deploy}>{busy ? 'deploying…' : 'Deploy →'}</button>
      </div>
    </div>
  )
}

export function Deployment() {
  const [selTarget, setSelTarget] = useState(null)
  const [selDist, setSelDist] = useState(null)
  const { data: devData } = usePoll(() => api.devices(null, 'accepted'), [], 8000)
  const devices = devData?.devices || []
  const target = devices.find((d) => d.id === selTarget)
  const [showDeploy, setShowDeploy] = useState(false)
  const [msg, setMsg] = useState(null)

  // Deploy click: an ARITY-1 dist + a selected target whose abi matches the
  // single role → deploy DIRECTLY (no dialog). Otherwise open the role-assign
  // dialog (arity-2+, or no target picked, or abi mismatch).
  const onDeployClick = async () => {
    const roles = selDist.roles || []
    const arity = selDist.arity || roles.length || 1
    if (target && arity === 1 && roles[0] &&
        (!roles[0].abi || _devAbi(target) === roles[0].abi)) {
      try {
        const res = await api.deployDistribution({ name: selDist.name, version: selDist.version,
          assignments: [{ role: roles[0].role, device_id: target.id }] })
        setMsg(`deployed ${selDist.name}:${selDist.version} → ${target.name} — progress in Action History`)
      } catch (e) { setMsg(`deploy: ${e.message}`) }
      return
    }
    setShowDeploy(true)
  }

  return (
    <div className="h-full flex flex-col gap-2">
      {/* deploy action bar — Distribution-driven */}
      <div className="flex items-center gap-3 text-sm">
        <span className="text-muted">Distribution:</span>
        <span className="font-mono text-slate-200">{selDist ? `${selDist.name} ${selDist.version} /${selDist.arity || selDist.roles?.length}` : '— select —'}</span>
        {target && <span className="text-muted">→ <span className="text-slate-200">{target.name}</span></span>}
        <button className="btn ml-auto" disabled={!selDist} title={!selDist ? 'select a distribution' : ''}
                onClick={onDeployClick}>Deploy →</button>
      </div>
      {msg && <div className="card px-3 py-1.5 text-xs text-slate-300">{msg}</div>}
      {/* 3-column board: Targets | Distributions | Action History */}
      <div className="flex-1 grid grid-cols-3 grid-rows-1 gap-2 min-h-0">
        <Targets sel={selTarget} setSel={setSelTarget} />
        <DistributionsColumn sel={selDist} setSel={setSelDist} />
        <ActionHistory targetName={target?.name} />
      </div>
      {showDeploy && selDist && <DeployDistDialog dist={selDist} devices={devices}
        onClose={() => setShowDeploy(false)}
        onDone={(res) => { setShowDeploy(false); setMsg(`deployed ${selDist.name}:${selDist.version} — ${(res.steps || []).length} role(s); progress in Action History`) }} />}
    </div>
  )
}
