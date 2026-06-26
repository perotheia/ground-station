import React, { useState } from 'react'
import { api } from '../api'
import { usePoll } from '../App'

// The vendoring view = the Theia-specific superpower over a vanilla Mender UI:
// what's PUBLISHED in our two distribution planes, and the one-click bridge to
// roll an app version out to its fleet via Mender.

function RuntimePlane() {
  const { data, error, loading } = usePoll(() => api.runtimePlane(), [], 15000)
  const releases = (data?.releases || []).filter((r) => !r._error)
  return (
    <div className="card">
      <div className="border-b border-edge px-4 py-3 flex items-center gap-2">
        <h3 className="font-semibold">Runtime plane</h3>
        <span className="text-xs text-muted">platform · supervisor + services · factory install (colony)</span>
      </div>
      <table className="w-full">
        <thead className="bg-ink/40"><tr>
          <th className="th">Version</th><th className="th">ABI / distro</th><th className="th">Artifacts</th><th className="th">Key</th>
        </tr></thead>
        <tbody className="divide-y divide-edge/60">
          {loading && <tr><td className="cell text-muted" colSpan={4}>loading…</td></tr>}
          {error && <tr><td className="cell text-red-300" colSpan={4}>{error}</td></tr>}
          {releases.map((r) => (
            <tr key={r._key} className="hover:bg-ink/30">
              <td className="cell font-medium">{r.version}</td>
              <td className="cell">{r.distro && r.distro !== 'any'
                ? <span className="badge bg-indigo-500/15 text-indigo-300">{r.distro}</span>
                : <span className="text-muted">arch-agnostic</span>}</td>
              <td className="cell text-xs text-muted">{(r.debs || []).map((d) => d.file?.split('/').pop()).join(', ')}</td>
              <td className="cell font-mono text-xs text-muted">{r._key}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AppsPlane() {
  const { data, error, loading, refresh } = usePoll(() => api.appsPlane(), [], 15000)
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const tree = data?.tree || {}

  const publish = async (fleet, app, version, deploy) => {
    setBusy(`${fleet}/${app}/${version}`)
    setMsg(null)
    try {
      const r = await api.publishApp(fleet, app, version, deploy)
      setMsg({
        ok: true,
        text: deploy && r.deployment
          ? `Deployed ${r.artifact_name} → ${r.deployment.devices} device(s) in '${fleet}' (deployment ${r.deployment.id.slice(0, 8)})`
          : `Uploaded ${r.artifact_name} to the Mender GW (${r.upload})`,
      })
    } catch (e) {
      setMsg({ ok: false, text: e.message })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="card">
      <div className="border-b border-edge px-4 py-3 flex items-center gap-2">
        <h3 className="font-semibold">App plane</h3>
        <span className="text-xs text-muted">user FC apps · day-2 Mender OTA · keyed fleet / app / version</span>
        <button onClick={refresh} className="btn-ghost ml-auto">Refresh</button>
      </div>

      {msg && (
        <div className={`px-4 py-2 text-sm ${msg.ok ? 'text-emerald-300 bg-emerald-500/10' : 'text-red-300 bg-red-500/10'}`}>
          {msg.text}
        </div>
      )}

      <div className="p-4 space-y-4">
        {loading && <div className="text-muted text-sm">loading…</div>}
        {error && <div className="text-red-300 text-sm">{error}</div>}
        {!loading && Object.keys(tree).length === 0 && (
          <div className="text-muted text-sm">no apps published — run <code className="text-accent">theia release-app &lt;app&gt;</code></div>
        )}
        {Object.entries(tree).map(([fleet, apps]) => (
          <div key={fleet}>
            <div className="mb-2 text-sm">
              <span className="badge bg-accent/10 text-accent">{fleet}</span>
              <span className="ml-2 text-muted text-xs">hardware-capability fleet (Mender device-group)</span>
            </div>
            <div className="space-y-2 pl-2">
              {Object.entries(apps).map(([app, versions]) => (
                <div key={app} className="rounded border border-edge/60">
                  <div className="px-3 py-2 text-sm font-medium border-b border-edge/40">{app}</div>
                  <table className="w-full">
                    <tbody className="divide-y divide-edge/40">
                      {versions.map((v) => {
                        const key = `${fleet}/${app}/${v.version}`
                        return (
                          <tr key={v.version} className="hover:bg-ink/30">
                            <td className="cell">v{v.version}</td>
                            <td className="cell text-xs text-muted">{v.artifact}</td>
                            <td className="cell text-right space-x-2">
                              <button className="btn-ghost" disabled={busy === key}
                                      onClick={() => publish(fleet, app, v.version, false)}>
                                Upload to GW
                              </button>
                              <button className="btn" disabled={busy === key}
                                      onClick={() => publish(fleet, app, v.version, true)}>
                                {busy === key ? 'Deploying…' : `Deploy to ${fleet}`}
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function Vendoring() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold mb-1">Vendoring</h2>
        <p className="text-sm text-muted">
          The Theia distribution planes — what's <span className="text-slate-300">publishable</span>, vs Mender's record of what's deployed.
          Runtime is colony's factory install; apps are the day-2 OTA delivery unit.
        </p>
      </div>
      <AppsPlane />
      <RuntimePlane />
    </div>
  )
}
