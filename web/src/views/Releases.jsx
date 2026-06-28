import React, { useMemo } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// Releases (UF "Distributions" + "Upload") — the deployable-unit catalog across
// both planes, surfacing the runtime↔app DEPENDENCY (no backward compat: each app
// pins exactly one runtime). Runtime rows list the apps that depend on them.

export function Releases() {
  const { data: rtData } = usePoll(() => api.runtimePlane(), [], 8000)
  const { data: appData } = usePoll(() => api.appsPlane(), [], 8000)

  const apps = useMemo(() => {
    const out = []
    const tree = appData?.tree || {}
    for (const [fleet, byApp] of Object.entries(tree)) {
      for (const [app, vers] of Object.entries(byApp)) {
        for (const v of vers) {
          out.push({ fleet, app, version: v.version,
            requires: v.requires_runtime || v.requires || '' })
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
              <tr><th className="th">App</th><th className="th">Version</th><th className="th">Fleet</th><th className="th">Requires runtime</th></tr>
            </thead>
            <tbody className="divide-y divide-edge/40">
              {apps.length === 0 && <tr><td className="cell text-muted" colSpan={4}>no apps published — run <code className="text-accent">theia release-app</code></td></tr>}
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
