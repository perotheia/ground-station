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
                  <span className="text-muted"> · {r.abi} · {r.runtime_build}{r.app_build ? ` · ${r.app_build}` : ''}</span></div>
              ))}
            </div>
          : 'Select a distribution to deploy.'}
      </div>
    </div>
  )
}

// Role → compatible-machine assignment dialog (deploy a distribution). Only
// machines whose probed abi matches a role's abi are offered for that role.
function DeployDistDialog({ dist, devices, onClose, onDone }) {
  const [assign, setAssign] = useState({})   // role -> device_id
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState(null)
  const roles = dist.roles || []
  // a device's abi (mirror the backend _rig_abi heuristic, loosely)
  const devAbi = (d) => {
    const os = (d.attributes?.os || '').toLowerCase(), k = (d.attributes?.kernel || '').toLowerCase()
    const arch = /aarch64|arm64/.test(k + os) ? 'arm64' : /x86_64|amd64/.test(k) ? 'amd64' : ''
    const distro = /focal|20\.04/.test(os) ? 'focal' : /bookworm|trixie|debian gnu\/linux 1[23]/.test(os) ? 'bookworm'
      : /ubuntu.*24/.test(os) ? 'ubuntu24' : ''
    return [distro, arch].filter(Boolean).join('-')
  }
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

  return (
    <div className="h-full flex flex-col gap-2">
      {/* deploy action bar — Distribution-driven */}
      <div className="flex items-center gap-3 text-sm">
        <span className="text-muted">Distribution:</span>
        <span className="font-mono text-slate-200">{selDist ? `${selDist.name} ${selDist.version} /${selDist.arity || selDist.roles?.length}` : '— select —'}</span>
        <button className="btn ml-auto" disabled={!selDist} title={!selDist ? 'select a distribution' : ''}
                onClick={() => setShowDeploy(true)}>Deploy →</button>
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
