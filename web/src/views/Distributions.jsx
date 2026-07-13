import React, { useState, useMemo, useEffect } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Distributions — PREPARE the deployable bundle (the strict ER entity). Pick an
// app (its arity + role names come from the app's manifest), then per role pick a
// runtime build + app build matching that role's ABI. Stored in S3. The Deployment
// panel deploys a prepared Distribution (Targets | Distributions).

// derive the abi suffix from a runtime/app build key (…-bookworm-arm64 etc.)
// LSB-codenamed abis FIRST (most specific), bare arch LAST — abiOf takes the
// first substring match, so 'noble-amd64' must precede 'amd64' or a noble
// key falls through to bare amd64 (the label lost its LSB release).
const ABIS = ['bookworm-arm64', 'focal-arm64', 'jammy-arm64', 'noble-amd64', 'ubuntu24', 'arm64', 'amd64']
const abiOf = (key) => ABIS.find((x) => (key || '').includes(x)) || ''

function NewDistDialog({ apps, runtimes, swpBuilds, onClose, onDone }) {
  const [name, setName] = useState('')
  const [appSel, setAppSel] = useState('')        // "fleet/app/version"
  const [roleRt, setRoleRt] = useState({})        // roleName -> runtime_build key
  const [busy, setBusy] = useState(false); const [err, setErr] = useState(null)

  const app = apps.find((a) => `${a.fleet}/${a.app}/${a.version}` === appSel)
  const roles = app?.roles?.length ? app.roles : (app ? ['default'] : [])
  // The Distribution INHERITS the app's version + abi. The SWP build key IS the
  // base app for EVERY role (a per-role app is an oxymoron — one base app defines
  // the distribution). abi is encoded in the runtime/app label (…-amd64), so no
  // separate ABI selector; the runtime list is filtered to the app's abi.
  const abi = app ? abiOf(`${app.app}-${app.version}`) : ''
  const swpBuild = app ? `${app.app}-${app.version}` : ''
  const version = app?.version || ''               // inherited, not entered
  const rtOptions = runtimes.filter((k) => !abi || abiOf(k) === abi)

  const save = async () => {
    if (!name.trim() || !app) { setErr('name + a Software Package required'); return }
    const rolesPayload = roles.map((r) => ({
      role: r, abi,
      runtime_build: roleRt[r] || '',
      swp_build: swpBuild, app_build: swpBuild,     // the ONE base app for every role
    }))
    if (rolesPayload.some((r) => !r.runtime_build)) { setErr('pick a runtime build for every role'); return }
    setBusy(true); setErr(null)
    try { await api.createDistribution({ name: name.trim(), version, roles: rolesPayload }); onDone() }
    catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[40rem] p-5 max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-medium flex-1" style={{ color: '#4A90E2' }}>Prepare a Distribution</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>✕</button>
        </div>
        <div className="flex gap-2 mb-3 items-center">
          <input className="input flex-1 text-sm" placeholder="distribution name (e.g. vehicle)" value={name} onChange={(e) => setName(e.target.value)} />
          {app && <span className="text-xs text-muted whitespace-nowrap">v{version} · {abi || 'any'}</span>}
        </div>
        <label className="block text-xs text-muted mb-1">Software Package (defines arity, roles, version + abi)</label>
        <select className="input w-full text-sm mb-3" value={appSel} onChange={(e) => { setAppSel(e.target.value); setRoleRt({}) }}>
          <option value="">— pick a Software Package —</option>
          {apps.map((a) => <option key={`${a.fleet}/${a.app}/${a.version}`} value={`${a.fleet}/${a.app}/${a.version}`}>
            {a.app} {a.version} /{a.arity || (a.roles?.length || 1)} [{(a.roles || []).join(', ') || 'single'}]
          </option>)}
        </select>
        {app && (
          <div className="space-y-3">
            <div className="text-xs text-muted">Per-role runtime (arity {app.arity || roles.length}) — the base app <span className="font-mono text-slate-300">{swpBuild}</span> deploys to every role:</div>
            {roles.map((r) => (
              <div key={r} className="rounded border border-edge bg-ink/40 p-2 flex gap-2 items-center text-xs">
                <span className="w-20 text-sm font-semibold text-slate-200">role: {r}</span>
                <span className="text-muted">runtime</span>
                <select className="input flex-1 text-xs font-mono" value={roleRt[r] || ''}
                        onChange={(e) => setRoleRt({ ...roleRt, [r]: e.target.value })}>
                  <option value="">— runtime build —</option>
                  {rtOptions.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </div>
            ))}
          </div>
        )}
        {err && <div className="text-xs text-red-400 mt-2">{err}</div>}
        <button className="btn w-full mt-4" disabled={busy} onClick={save}>{busy ? '…' : '💾 Save Distribution'}</button>
      </div>
    </div>
  )
}

export function Distributions() {
  const { data, refresh } = usePoll(() => api.distributions(), [], 10000)
  const { data: appData } = usePoll(() => api.appsPlane(), [], 15000)
  const { data: rtData } = usePoll(() => api.runtimePlane(), [], 15000)
  const [showNew, setShowNew] = useState(false)
  const [sel, setSel] = useState(null)                 // "name/version" → detail panel
  const dists = (data?.distributions || []).filter((d) => !d._error)
  const selDist = dists.find((d) => `${d.name}/${d.version}` === sel)

  const apps = useMemo(() => {
    const out = []
    for (const [fleet, byApp] of Object.entries(appData?.tree || {}))
      for (const [app, vers] of Object.entries(byApp))
        for (const v of vers) out.push({ fleet, app, version: v.version, arity: v.arity, roles: v.roles || [] })
    return out
  }, [appData])
  const runtimes = (rtData?.releases || []).filter((r) => !r._error).map((r) => r.key || r.version)
  const swpBuilds = apps.map((a) => a.app + '-' + a.version)   // SWP build keys (abi from name when present)

  const del = async (d) => { try { await api.deleteDistribution(d.name, d.version); refresh() } catch (e) { alert(e.message) } }

  return (
    <div className="pane h-full">
      <div className="pane-head">
        Distributions
        <span className="text-muted font-normal text-xs ml-2">{dists.length}</span>
        <button className="btn ml-auto" onClick={() => setShowNew(true)}>＋ Prepare Distribution</button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr><th className="th">Name</th><th className="th">Version</th><th className="th">Arity</th><th className="th">Roles (abi · runtime · app)</th><th className="th text-right">ACT</th></tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {dists.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no distributions — prepare one from a runtime + app</td></tr>}
            {dists.map((d) => {
              const k = `${d.name}/${d.version}`
              return (
              <tr key={k}
                  onClick={() => setSel(sel === k ? null : k)}
                  className={`cursor-pointer hover:bg-edge/20 ${sel === k ? 'row-sel' : ''}`}>
                <td className="cell text-sm">{d.name}</td>
                <td className="cell font-mono text-xs">{d.version}</td>
                <td className="cell text-xs"><span className="badge bg-amber-500/15 text-amber-300">/{d.arity || (d.roles?.length || 1)}</span></td>
                <td className="cell text-[11px] font-mono">
                  {(d.roles || []).map((r) => (
                    <div key={r.role}><span className="text-slate-200">{r.role}</span>
                      <span className="text-muted"> · {r.abi} · {r.runtime_build}{(r.swp_build || r.app_build) ? ` · ${r.swp_build || r.app_build}` : ''}</span></div>
                  ))}
                </td>
                <td className="cell text-right" onClick={(e) => e.stopPropagation()}>
                  <button className="icon-btn" title="delete" style={{ color: '#E57373' }} onClick={() => del(d)}>🗑</button>
                </td>
              </tr>
            )})}
          </tbody>
        </table>
      </div>
      {/* Detail panel — full breakdown of the selected distribution (role
          assignments, per-role base runtime + app build, abi). Select a row to
          open; click it again to close. */}
      <div className="border-t border-edge bg-sidebar/30 p-3 text-xs">
        {!selDist
          ? <div className="text-muted">Select a distribution to see its role assignments.</div>
          : <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-100 text-sm">{selDist.name}</span>
                <span className="font-mono text-slate-300">{selDist.version}</span>
                <span className="badge bg-amber-500/15 text-amber-300">arity {selDist.arity || (selDist.roles?.length || 1)}</span>
              </div>
              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <div className="text-muted uppercase tracking-wide col-span-2 mt-1">Role assignments</div>
                {(selDist.roles || []).map((r) => (
                  <React.Fragment key={r.role}>
                    <div className="font-semibold text-slate-200">{r.role}
                      <span className="text-muted font-normal"> · {r.abi || 'any'}</span></div>
                    <div className="font-mono text-[11px]">
                      <span className="text-violet-300" title="base runtime build">{r.runtime_build || '—'}</span>
                      <span className="text-muted"> + </span>
                      <span className="text-cyan-300" title="app (SWP) build">{r.swp_build || r.app_build || '— (base-only)'}</span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div className="text-[11px] text-muted pt-1">
                A machine is deploy-compatible with a role iff its probed abi == the role abi.
                Deploy this distribution from the Deployment tab.
              </div>
            </div>}
      </div>
      {showNew && <NewDistDialog apps={apps} runtimes={runtimes} swpBuilds={swpBuilds}
        onClose={() => setShowNew(false)} onDone={() => { setShowNew(false); refresh() }} />}
    </div>
  )
}
