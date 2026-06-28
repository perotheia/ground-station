import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { Deployment } from './views/Deployment'
import { Deployments } from './views/Deployments'
import { Releases } from './views/Releases'
import { Rollouts } from './views/Rollouts'
import { Fleet } from './views/Fleet'

// Update-Factory-style left-rail nav. Deployment is the default landing view (the
// 3-column board). Scope-cut items (Usage, Users, Config-as-MFA) are dropped.
const NAV = [
  { id: 'deployment', label: 'Deployment', icon: '⊞' },
  { id: 'fleet', label: 'Fleet', icon: '▤' },
  { id: 'releases', label: 'Releases', icon: '◈' },
  { id: 'rollouts', label: 'Rollouts', icon: '⟳' },
  { id: 'deployments', label: 'History', icon: '≡' },
]

const TITLES = {
  deployment: 'Deployment Management',
  fleet: 'Fleet',
  releases: 'Releases & Distributions',
  rollouts: 'Rollouts',
  deployments: 'Deployment History',
}

export default function App() {
  const [view, setView] = useState('deployment')
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.config().then(setCfg).catch((e) => setErr(e.message))
  }, [])

  return (
    <div className="h-full flex flex-col">
      {/* ── Top header bar ─────────────────────────────────────────────── */}
      <header className="flex items-center h-12 px-4 bg-sidebar border-b border-edge shrink-0">
        <span className="text-muted text-lg mr-3 select-none">☰</span>
        <h1 className="text-base font-semibold text-white">{TITLES[view]}</h1>
        <div className="ml-auto flex items-center gap-4">
          <span className="text-lg font-bold tracking-tight text-white">Theia Ground Station</span>
          <span className="text-xs text-muted px-2 py-1 rounded-full border border-edge">
            {cfg ? (cfg.token_set ? '● authed' : '○ no token') : '…'}
          </span>
        </div>
      </header>

      <div className="flex flex-1 min-h-0">
        {/* ── Left sidebar ─────────────────────────────────────────────── */}
        <aside className="w-52 bg-sidebar border-r border-edge flex flex-col shrink-0">
          <div className="px-4 py-4 border-b border-edge">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center text-accent text-sm font-bold">◆</div>
              <div className="text-sm font-medium text-slate-100">operator</div>
            </div>
            <div className="mt-2 inline-block text-[10px] font-semibold tracking-wider text-ok px-2 py-0.5 rounded bg-ok/10 border border-ok/30">
              LAB
            </div>
          </div>
          <nav className="flex-1 py-2">
            {NAV.map((n) => (
              <div
                key={n.id}
                onClick={() => setView(n.id)}
                className={`nav-item ${view === n.id ? 'nav-item-active' : ''}`}
              >
                <span className="w-4 text-center opacity-80">{n.icon}</span>
                {n.label}
              </div>
            ))}
          </nav>
          <a
            href="https://docs.updatefactory.io/"
            target="_blank"
            rel="noreferrer"
            className="nav-item border-t border-edge text-xs"
          >
            <span className="w-4 text-center">?</span> Documentation
          </a>
        </aside>

        {/* ── Main workspace ───────────────────────────────────────────── */}
        <main className="flex-1 min-w-0 p-3 overflow-hidden">
          {err && (
            <div className="card border-danger/40 bg-danger/10 px-4 py-2 text-sm text-danger mb-3">
              backend: {err}
            </div>
          )}
          {view === 'deployment' && <Deployment />}
          {view === 'fleet' && <Fleet />}
          {view === 'releases' && <Releases />}
          {view === 'rollouts' && <Rollouts />}
          {view === 'deployments' && <Deployments />}
        </main>
      </div>
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
