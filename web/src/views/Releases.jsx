import React, { useMemo, useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Releases (UF "Distributions" + "Upload") — the deployable-unit catalog across
// both planes, surfacing the runtime↔app DEPENDENCY (no backward compat: each app
// pins exactly one runtime). Runtime rows list the apps that depend on them.
// ACT column: pin / delete (unpin before delete).

function RowConfirm({ label, onYes, onNo }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px]">
      <span className="text-muted">{label}?</span>
      <button className="text-ok hover:underline" onClick={onYes}>Yes</button>
      <button className="text-danger hover:underline" onClick={onNo}>No</button>
    </span>
  )
}

// A small UF lock badge — a deployed release is immutable (re-iterate = new ver).
function Lock({ on }) {
  if (!on) return null
  return <span title="locked: deployed releases are immutable" className="badge bg-slate-500/20 text-slate-300 ml-1">🔒 locked</span>
}

export function Releases() {
  const { data: rtData, refresh: rtRefresh } = usePoll(() => api.runtimePlane(), [], 8000)
  const { data: appData, refresh } = usePoll(() => api.appsPlane(), [], 8000)
  const { data: roleData } = usePoll(() => api.rolesPlane(), [], 12000)
  const [confirm, setConfirm] = useState(null)
  const [rtConfirm, setRtConfirm] = useState(null)
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)

  const actKey = (a) => `${a.fleet}/${a.app}/${a.version}`
  const act = async (a, fn, label) => {
    setBusy(actKey(a)); setConfirm(null); setNote(null)
    try { await fn(); refresh() }
    catch (e) { setNote(`${label}: ${e.message}`) }
    setBusy(null)
  }
  const pin = (a) => act(a, () => api.pinApp(a.fleet, a.app, a.version, !a.pinned), 'pin')
  const del = (a) => act(a, () => api.deleteApp(a.fleet, a.app, a.version), 'delete')

  const apps = useMemo(() => {
    const out = []
    const tree = appData?.tree || {}
    for (const [fleet, byApp] of Object.entries(tree)) {
      for (const [app, vers] of Object.entries(byApp)) {
        for (const v of vers) {
          out.push({ fleet, app, version: v.version,
            requires: v.requires_runtime || v.requires || '',
            pinned: !!v.pinned, locked: !!v.locked })
        }
      }
    }
    return out
  }, [appData])

  const roles = useMemo(() => {
    const out = []
    const tree = roleData?.tree || {}
    for (const [fleet, byVer] of Object.entries(tree)) {
      for (const [version, rs] of Object.entries(byVer)) {
        for (const r of rs) out.push({ fleet, version, role: r.role, locked: !!r.locked })
      }
    }
    return out
  }, [roleData])

  const runtimes = (rtData?.releases || []).filter((r) => !r._error)
    .map((r) => ({ key: r.key || r.version, version: r.version, distro: r.distro,
      locked: !!r.locked, pinned: !!r.pinned,
      // which apps depend on THIS runtime (the dependency graph, reversed)
      dependents: apps.filter((a) => a.requires === (r.key || r.version)) }))

  // Runtime ACT (same model as the app table): pin (📍/📌), delete (🗑) guarded
  // by unpin-first AND lock (a deployed runtime is immutable).
  const rtAct = async (key, fn, label) => {
    setBusy(`rt:${key}`); setRtConfirm(null); setNote(null)
    try { await fn(); rtRefresh() } catch (e) { setNote(`${label}: ${e.message}`) }
    setBusy(null)
  }
  const pinRt = (r) => rtAct(r.key, () => api.pinRuntime(r.key, !r.pinned), 'pin')
  const delRt = (r) => rtAct(r.key, () => api.deleteRuntime(r.key), 'delete')

  return (
    <div className="h-full grid grid-rows-[1fr_auto] gap-3">
    <div className="grid grid-cols-2 gap-3 min-h-0">
      {/* Runtime plane (base) + its dependents */}
      <div className="pane">
        <div className="pane-head"><span className="badge bg-violet-500/15 text-violet-300 mr-2">base</span>Runtime + Services</div>
        <div className="flex-1 overflow-auto p-3 space-y-2">
          {runtimes.length === 0 && <div className="text-muted text-sm">no runtime releases</div>}
          {runtimes.map((r) => (
            <div key={r.key} className="rounded border border-edge bg-ink/40 p-3">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-slate-100">{r.key}</span>
                <span className="text-xs text-muted">{r.distro}</span>
                <Lock on={r.locked} />
                {/* ACT: same pin/delete model + icons as the app table */}
                <span className="ml-auto whitespace-nowrap">
                  {rtConfirm === r.key
                    ? <RowConfirm label="delete" onYes={() => delRt(r)} onNo={() => setRtConfirm(null)} />
                    : busy === `rt:${r.key}` ? <span className="text-muted text-xs">…</span>
                    : <span className="inline-flex gap-0.5">
                        <button className="icon-btn" title={r.pinned ? 'unpin' : 'pin (guard from delete)'}
                                onClick={() => pinRt(r)}>{r.pinned ? '📌' : '📍'}</button>
                        <button className="icon-btn"
                                title={r.locked ? 'locked: deployed, immutable' : r.pinned ? 'unpin before delete' : 'delete from plane'}
                                disabled={r.pinned || r.locked}
                                style={{ color: (r.pinned || r.locked) ? '#5a6b7d' : '#E57373' }}
                                onClick={() => !r.pinned && !r.locked && setRtConfirm(r.key)}>🗑</button>
                      </span>}
                </span>
              </div>
              <div className="mt-2 text-xs text-muted">
                {r.dependents.length === 0
                  ? 'no apps depend on this runtime'
                  : <>required by: {r.dependents.map((d) => (
                      <span key={`${d.app}/${d.version}`} className="badge bg-cyan-500/15 text-cyan-300 mr-1">{d.app} {d.version}</span>
                    ))}</>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* App plane (distributions) + their pinned runtime */}
      <div className="pane">
        <div className="pane-head"><span className="badge bg-cyan-500/15 text-cyan-300 mr-2">app</span>Distributions</div>
        <div className="flex-1 overflow-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-sidebar/60">
              <tr><th className="th">App</th><th className="th">Version</th><th className="th">Fleet</th><th className="th">Requires runtime</th><th className="th text-right">ACT</th></tr>
            </thead>
            <tbody className="divide-y divide-edge/40">
              {apps.length === 0 && <tr><td className="cell text-muted" colSpan={5}>no apps published — run <code className="text-accent">theia release-app</code></td></tr>}
              {apps.map((a) => (
                <tr key={`${a.fleet}/${a.app}/${a.version}`} className="hover:bg-edge/20">
                  <td className="cell text-sm">{a.app}</td>
                  <td className="cell font-mono text-xs">{a.version}<Lock on={a.locked} /></td>
                  <td className="cell text-xs text-muted">{a.fleet}</td>
                  <td className="cell text-xs">
                    {a.requires
                      ? <span className="font-mono text-violet-300">{a.requires}</span>
                      : <span className="badge bg-amber-500/15 text-amber-400">unpinned</span>}
                  </td>
                  <td className="cell text-right whitespace-nowrap">
                    {confirm === actKey(a)
                      ? <RowConfirm label="delete" onYes={() => del(a)} onNo={() => setConfirm(null)} />
                      : busy === actKey(a) ? <span className="text-muted text-xs">…</span>
                      : <span className="inline-flex gap-0.5">
                          <button className="icon-btn" title={a.pinned ? 'unpin' : 'pin (guard from delete)'}
                                  onClick={() => pin(a)}>{a.pinned ? '📌' : '📍'}</button>
                          <button className="icon-btn"
                                  title={a.locked ? 'locked: deployed, immutable' : a.pinned ? 'unpin before delete' : 'delete from plane'}
                                  disabled={a.pinned || a.locked}
                                  style={{ color: (a.pinned || a.locked) ? '#5a6b7d' : '#E57373' }}
                                  onClick={() => !a.pinned && !a.locked && setConfirm(actKey(a))}>🗑</button>
                        </span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-3 py-2 border-t border-edge text-[11px] text-muted">
          No backward compat — each app pins exactly one runtime. The deploy gate
          blocks an app whose required runtime ≠ the device's installed base.
        </div>
      </div>
    </div>

    {/* Roles plane — theia release-role <role>.mender bundles (the third plane) */}
    <div className="pane max-h-44">
      <div className="pane-head">
        <span className="badge bg-amber-500/15 text-amber-300 mr-2">role</span>Role artifacts
        <span className="text-muted font-normal text-xs ml-2">{roles.length} bundle(s)</span>
      </div>
      <div className="flex-1 overflow-auto">
        {roles.length === 0
          ? <div className="p-3 text-muted text-sm">no role artifacts — run <code className="text-accent">theia release-role</code></div>
          : <table className="w-full">
              <thead className="sticky top-0 bg-sidebar/60">
                <tr><th className="th">Role</th><th className="th">Fleet</th><th className="th">Version</th><th className="th">State</th></tr>
              </thead>
              <tbody className="divide-y divide-edge/40">
                {roles.map((r) => (
                  <tr key={`${r.fleet}/${r.version}/${r.role}`} className="hover:bg-edge/20">
                    <td className="cell text-sm font-mono">{r.role}</td>
                    <td className="cell text-xs text-muted">{r.fleet}</td>
                    <td className="cell font-mono text-xs">{r.version}</td>
                    <td className="cell text-xs">{r.locked ? <Lock on /> : <span className="text-muted">draft</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>}
      </div>
    </div>
    {note && <div className="text-xs text-red-400 px-1">{note}</div>}
    </div>
  )
}
