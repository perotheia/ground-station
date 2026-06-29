import React, { useState, useMemo, useEffect } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Distributions — PREPARE the deployable bundle (the strict ER entity). Pick an
// app (its arity + role names come from the app's manifest), then per role pick a
// runtime build + app build matching that role's ABI. Stored in S3. The Deployment
// panel deploys a prepared Distribution (Targets | Distributions).

// derive the abi suffix from a runtime/app build key (…-bookworm-arm64 etc.)
const ABIS = ['bookworm-arm64', 'focal-arm64', 'ubuntu24', 'amd64']
const abiOf = (key) => ABIS.find((x) => (key || '').includes(x)) || ''

function NewDistDialog({ apps, runtimes, appBuilds, onClose, onDone }) {
  const [name, setName] = useState('')
  const [version, setVersion] = useState('1.0')
  const [appSel, setAppSel] = useState('')        // "fleet/app/version"
  const [roleAbi, setRoleAbi] = useState({})      // roleName -> chosen abi
  const [roleRt, setRoleRt] = useState({})        // roleName -> runtime_build key
  const [roleApp, setRoleApp] = useState({})      // roleName -> app_build key
  const [busy, setBusy] = useState(false); const [err, setErr] = useState(null)

  const app = apps.find((a) => `${a.fleet}/${a.app}/${a.version}` === appSel)
  const roles = app?.roles?.length ? app.roles : (app ? ['default'] : [])

  const save = async () => {
    if (!name.trim() || !app) { setErr('name + an app required'); return }
    const rolesPayload = roles.map((r) => ({
      role: r, abi: roleAbi[r] || '',
      runtime_build: roleRt[r] || '', app_build: roleApp[r] || '',
    }))
    if (rolesPayload.some((r) => !r.runtime_build)) { setErr('pick a runtime build for every role'); return }
    setBusy(true); setErr(null)
    try { await api.createDistribution({ name: name.trim(), version: version.trim(), roles: rolesPayload }); onDone() }
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
        <div className="flex gap-2 mb-3">
          <input className="input flex-1 text-sm" placeholder="distribution name (e.g. vehicle)" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input w-24 text-sm" placeholder="version" value={version} onChange={(e) => setVersion(e.target.value)} />
        </div>
        <label className="block text-xs text-muted mb-1">App (defines arity + roles)</label>
        <select className="input w-full text-sm mb-3" value={appSel} onChange={(e) => setAppSel(e.target.value)}>
          <option value="">— pick an app —</option>
          {apps.map((a) => <option key={`${a.fleet}/${a.app}/${a.version}`} value={`${a.fleet}/${a.app}/${a.version}`}>
            {a.app} {a.version} /{a.arity || (a.roles?.length || 1)} [{(a.roles || []).join(', ') || 'single'}]
          </option>)}
        </select>
        {app && (
          <div className="space-y-3">
            <div className="text-xs text-muted">Per-role builds (arity {app.arity || roles.length}):</div>
            {roles.map((r) => {
              const abi = roleAbi[r] || ''
              const rts = runtimes.filter((k) => !abi || abiOf(k) === abi)
              const aps = appBuilds.filter((k) => !abi || abiOf(k) === abi)
              return (
                <div key={r} className="rounded border border-edge bg-ink/40 p-2 space-y-2">
                  <div className="text-sm font-semibold text-slate-200">role: {r}</div>
                  <div className="flex gap-2 items-center text-xs">
                    <span className="w-16 text-right text-muted">ABI</span>
                    <select className="input flex-1 text-xs" value={abi}
                            onChange={(e) => setRoleAbi({ ...roleAbi, [r]: e.target.value })}>
                      <option value="">(any)</option>
                      {ABIS.map((x) => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </div>
                  <div className="flex gap-2 items-center text-xs">
                    <span className="w-16 text-right text-muted">runtime</span>
                    <select className="input flex-1 text-xs font-mono" value={roleRt[r] || ''}
                            onChange={(e) => setRoleRt({ ...roleRt, [r]: e.target.value })}>
                      <option value="">— runtime build —</option>
                      {rts.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                  </div>
                  <div className="flex gap-2 items-center text-xs">
                    <span className="w-16 text-right text-muted">app</span>
                    <select className="input flex-1 text-xs font-mono" value={roleApp[r] || ''}
                            onChange={(e) => setRoleApp({ ...roleApp, [r]: e.target.value })}>
                      <option value="">(base only)</option>
                      {aps.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                  </div>
                </div>
              )
            })}
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
  const dists = (data?.distributions || []).filter((d) => !d._error)

  const apps = useMemo(() => {
    const out = []
    for (const [fleet, byApp] of Object.entries(appData?.tree || {}))
      for (const [app, vers] of Object.entries(byApp))
        for (const v of vers) out.push({ fleet, app, version: v.version, arity: v.arity, roles: v.roles || [] })
    return out
  }, [appData])
  const runtimes = (rtData?.releases || []).filter((r) => !r._error).map((r) => r.key || r.version)
  const appBuilds = apps.map((a) => a.app + '-' + a.version)   // app build keys (abi from name when present)

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
            {dists.map((d) => (
              <tr key={`${d.name}/${d.version}`} className="hover:bg-edge/20">
                <td className="cell text-sm">{d.name}</td>
                <td className="cell font-mono text-xs">{d.version}</td>
                <td className="cell text-xs"><span className="badge bg-amber-500/15 text-amber-300">/{d.arity || (d.roles?.length || 1)}</span></td>
                <td className="cell text-[11px] font-mono">
                  {(d.roles || []).map((r) => (
                    <div key={r.role}><span className="text-slate-200">{r.role}</span>
                      <span className="text-muted"> · {r.abi} · {r.runtime_build}{r.app_build ? ` · ${r.app_build}` : ''}</span></div>
                  ))}
                </td>
                <td className="cell text-right">
                  <button className="icon-btn" title="delete" style={{ color: '#E57373' }} onClick={() => del(d)}>🗑</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {showNew && <NewDistDialog apps={apps} runtimes={runtimes} appBuilds={appBuilds}
        onClose={() => setShowNew(false)} onDone={() => { setShowNew(false); refresh() }} />}
    </div>
  )
}
