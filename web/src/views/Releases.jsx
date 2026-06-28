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

export function Releases() {
  const { data: rtData } = usePoll(() => api.runtimePlane(), [], 8000)
  const { data: appData, refresh } = usePoll(() => api.appsPlane(), [], 8000)
  const [confirm, setConfirm] = useState(null)
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
            requires: v.requires_runtime || v.requires || '', pinned: !!v.pinned })
        }
      }
    }
    return out
  }, [appData])

  const runtimes = (rtData?.releases || []).filter((r) => !r._error)
    .map((r) => ({ key: r.key || r.version, version: r.version, distro: r.distro,
      // which apps depend on THIS runtime (the dependency graph, reversed)
      dependents: apps.filter((a) => a.requires === (r.key || r.version)) }))

  return (
    <div className="h-full grid grid-cols-2 gap-3">
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
                  <td className="cell font-mono text-xs">{a.version}</td>
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
                          <button className="icon-btn" title={a.pinned ? 'unpin before delete' : 'delete from plane'}
                                  disabled={a.pinned} style={{ color: a.pinned ? '#5a6b7d' : '#E57373' }}
                                  onClick={() => !a.pinned && setConfirm(actKey(a))}>🗑</button>
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
  )
}
