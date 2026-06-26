import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { Devices } from './views/Devices'
import { Deployments } from './views/Deployments'
import { Vendoring } from './views/Vendoring'

const TABS = [
  { id: 'devices', label: 'Fleet' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'vendoring', label: 'Vendoring' },
]

export default function App() {
  const [tab, setTab] = useState('devices')
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.config().then(setCfg).catch((e) => setErr(e.message))
  }, [])

  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-edge bg-panel/60 backdrop-blur">
        <div className="mx-auto max-w-7xl px-6 py-3 flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="text-accent text-xl font-bold">◆</span>
            <h1 className="text-lg font-semibold tracking-tight">Theia Ground Station</h1>
          </div>
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-3 py-1.5 rounded text-sm font-medium ${
                  tab === t.id ? 'bg-accent/15 text-accent' : 'text-muted hover:text-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto text-xs text-muted">
            {cfg ? (
              <span>
                GW <span className="text-slate-300">{cfg.mender_server}</span>
                {' · '}
                <span className={cfg.token_set ? 'text-emerald-400' : 'text-amber-400'}>
                  {cfg.token_set ? 'authed' : 'no token'}
                </span>
              </span>
            ) : (
              'connecting…'
            )}
          </div>
        </div>
      </header>

      {err && (
        <div className="mx-auto max-w-7xl w-full px-6 mt-4">
          <div className="card border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">
            backend: {err}
          </div>
        </div>
      )}

      <main className="mx-auto max-w-7xl w-full px-6 py-6 flex-1">
        {tab === 'devices' && <Devices />}
        {tab === 'deployments' && <Deployments />}
        {tab === 'vendoring' && <Vendoring />}
      </main>

      <footer className="border-t border-edge px-6 py-3 text-center text-xs text-muted">
        Mender OTA control · runtime + app vendoring planes · the fleet operator surface
      </footer>
    </div>
  )
}

// shared polling hook — keeps the dashboard live (Mender-like auto-refresh)
export function usePoll(fn, deps = [], intervalMs = 8000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const run = useCallback(() => {
    fn()
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => {
    run()
    const t = setInterval(run, intervalMs)
    return () => clearInterval(t)
  }, [run, intervalMs])
  return { data, error, loading, refresh: run }
}
