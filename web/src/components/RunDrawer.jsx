import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api'

// The Run drawer — live progress of ONE provisioning / deploy run (spec
// ui-provisioning §0 GET /api/runs/{id}). Polls every 2 s, SERIALLY (the next
// request starts only after the previous answered: never two in flight) and
// stops when the run is final (success / failure). The run state lives in
// colony's journal, so the drawer can be re-opened after a page reload.

export const isActive = (status) => status === 'pending' || status === 'running'
const isFinal = (status) => status === 'success' || status === 'failure'
const POLL_MS = 2000
const LAST_RIG = 'gs.lastRunRig'     // the rig of the last run started from this browser

export function fmtTime(t) {
  if (t == null || t === '') return '—'
  let d
  if (typeof t === 'number') d = new Date(t > 1e12 ? t : t * 1000)
  else d = new Date(t)
  return isNaN(d.getTime()) ? String(t) : d.toLocaleString()
}

const KIND_LABEL = { provision: 'Provision', deploy: 'Deploy runtime', orchestrate: 'Deploy runtime' }
const RESULT_STYLE = {
  ok: 'bg-ok/15 text-ok',
  changed: 'bg-amber-500/15 text-amber-300',
  skipped: 'bg-slate-500/20 text-slate-400',
  failed: 'bg-danger/20 text-danger',
}
const STATUS_STYLE = {
  pending: 'bg-sky-500/15 text-sky-300',
  running: 'bg-amber-500/15 text-amber-300',
  success: 'bg-ok/15 text-ok',
  failure: 'bg-danger/20 text-danger',
}

// ── the run context: one drawer for the whole app + resume after reload ─────
const RunsCtx = createContext({ openRun: () => {}, active: {}, tick: 0, started: () => {} })
export const useRuns = () => useContext(RunsCtx)

export function RunsProvider({ children }) {
  const [openId, setOpenId] = useState(null)
  const [active, setActive] = useState({})      // rig -> run_id (seen pending/running)
  const [offers, setOffers] = useState([])      // other active runs found at page load
  const [tick, setTick] = useState(0)           // bumps when a run ends (lists refresh)

  const openRun = useCallback((id) => { setOpenId(id); setOffers((o) => o.filter((r) => r.run_id !== id)) }, [])
  // a run was just started from this browser (202): remember its rig for a reload
  const started = useCallback((rig, runId) => {
    try { localStorage.setItem(LAST_RIG, rig) } catch { /* private mode */ }
    setActive((a) => ({ ...a, [rig]: runId }))
    setOpenId(runId)
  }, [])
  const onRunState = useCallback((run) => {
    if (!run?.rig) return
    if (isActive(run.status)) setActive((a) => (a[run.rig] === run.run_id ? a : { ...a, [run.rig]: run.run_id }))
    else if (isFinal(run.status)) {
      setActive((a) => { if (!(run.rig in a)) return a; const n = { ...a }; delete n[run.rig]; return n })
      setTick((t) => t + 1)
    }
  }, [])

  // On page load: any rig whose last run is pending/running re-opens its drawer
  // (the first one) or is offered (the rest). Candidates = /api/fleet rows with an
  // active last_run + the rig of the last run started here (it may not be listed
  // yet); each is confirmed from GET /api/runs?rig= (newest first).
  useEffect(() => {
    let live = true
    ;(async () => {
      const rigs = []
      try {
        const fleet = await api.fleet()
        for (const r of fleet) if (isActive(r.last_run?.status)) rigs.push(r.name)
      } catch { /* the Fleet view reports fleet errors */ }
      let last = null
      try { last = localStorage.getItem(LAST_RIG) } catch { /* ignore */ }
      if (last && !rigs.includes(last)) rigs.push(last)
      const found = []
      for (const rig of rigs.slice(0, 10)) {
        try {
          const runs = await api.runs(rig)
          const a = runs.find((r) => isActive(r.status))
          if (a) found.push({ ...a, rig: a.rig || rig })
        } catch { /* skip this rig */ }
      }
      if (!live || found.length === 0) return
      setActive((cur) => {
        const n = { ...cur }
        for (const r of found) n[r.rig] = r.run_id
        return n
      })
      setOpenId((cur) => cur || found[0].run_id)
      setOffers(found.slice(1))
    })()
    return () => { live = false }
  }, [])

  return (
    <RunsCtx.Provider value={{ openRun, active, tick, started }}>
      {children}
      {offers.length > 0 && !openId && (
        <div className="fixed bottom-3 left-56 z-40 card px-3 py-2 text-xs flex items-center gap-2" data-testid="run-offers">
          <span className="text-amber-300">● running:</span>
          {offers.map((r) => (
            <button key={r.run_id} className="btn-ghost text-[11px] px-2 py-0.5" onClick={() => openRun(r.run_id)}>
              {KIND_LABEL[r.kind] || r.kind} {r.rig} — open
            </button>
          ))}
          <button className="text-muted hover:text-slate-200 ml-1" onClick={() => setOffers([])}>✕</button>
        </div>
      )}
      {openId && <RunDrawer runId={openId} onClose={() => setOpenId(null)} onRunState={onRunState} />}
    </RunsCtx.Provider>
  )
}

// GET /api/runs/{id} every 2 s while not final; serial.
function useRun(runId, onRunState) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const cb = useRef(onRunState)
  cb.current = onRunState
  useEffect(() => {
    let live = true
    let timer = null
    setRun(null); setError(null)
    const tick = async () => {
      let r = null
      try {
        r = await api.run(runId)
        if (!live) return
        setRun(r); setError(null)
        cb.current && cb.current(r)
      } catch (e) {
        if (!live) return
        setError(e.message)
        if (e.status === 404) return          // no such run: stop
      }
      if (live && !(r && isFinal(r.status))) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => { live = false; clearTimeout(timer) }
  }, [runId])
  return { run, error }
}

const ACCEPTED_TEXT = (v, note) => {
  if (v === true) return ['accepted in Mender (identity = the device name, key read from the board)', 'text-ok']
  if (v === false) return [`NOT accepted: ${note || 'no matching Mender auth set'}`, 'text-amber-300']
  if (v === 'not-a-mender-rig') return ['no Mender on this rig (by design: opkg rig)', 'text-slate-300']
  return null
}

function Row({ k, children }) {
  return (
    <div className="flex gap-2 py-0.5">
      <span className="text-muted w-36 shrink-0">{k}</span>
      <span className="text-slate-200 break-all">{children}</span>
    </div>
  )
}

export function RunDrawer({ runId, onClose, onRunState }) {
  const { run, error } = useRun(runId, onRunState)
  const listRef = useRef(null)
  const tasks = run?.tasks || []
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [tasks.length])

  const status = run?.status
  const step = run?.step
  const pct = step && step.total_estimate
    ? Math.min(100, Math.round((100 * (isFinal(status) ? step.total_estimate : Math.max(0, (step.index || 0) - 1))) / step.total_estimate))
    : null
  const acc = run ? ACCEPTED_TEXT(run.accepted, run.note) : null
  const skipped = run?.skipped_by_design || []

  return (
    <div className="fixed inset-0 bg-black/40 flex justify-end z-50" onClick={onClose}>
      <div className="card w-[36rem] h-full p-4 overflow-auto rounded-none flex flex-col gap-3"
           data-testid="run-drawer" data-status={status || 'loading'} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">{run ? `${KIND_LABEL[run.kind] || run.kind} ${run.rig}` : 'Run'}</h3>
          {status && <span className={`badge ${STATUS_STYLE[status] || 'bg-edge/40'}`} data-testid="run-status">
            {isActive(status) && <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 animate-pulse bg-current" />}
            {status}</span>}
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <div className="text-[11px] text-muted font-mono -mt-2" data-testid="run-id">run {runId}</div>

        {error && (
          <div className="text-xs text-amber-300 border border-amber-500/40 bg-amber-500/10 rounded px-2 py-1">
            can't read the run: {error}{error && isActive(status) ? ' (retrying)' : ''}
          </div>
        )}
        {!run && !error && <div className="text-sm text-muted">loading…</div>}

        {run && (
          <div className="text-xs">
            <Row k="started">{fmtTime(run.started)}</Row>
            <Row k="finished">{isFinal(status) ? fmtTime(run.finished) : '— (in progress)'}</Row>
          </div>
        )}

        {/* progress: the current step (index of ~total) */}
        {run && step && (
          <div className="text-xs" data-testid="run-step">
            <div className="flex mb-1">
              <span className="text-slate-200">
                {isFinal(status) ? 'done' : `step ${step.index ?? '?'}${step.total_estimate ? ` of ~${step.total_estimate}` : ''}`}
                {!isFinal(status) && step.name && <span className="text-muted">: {step.name}</span>}
              </span>
            </div>
            {pct != null && (
              <div className="h-1.5 rounded bg-edge/60 overflow-hidden">
                <div className={`h-full ${status === 'failure' ? 'bg-danger' : status === 'success' ? 'bg-ok' : 'bg-accent'}`}
                     style={{ width: `${status === 'failure' ? 100 : pct}%` }} />
              </div>
            )}
          </div>
        )}

        {/* the failing task, in plain words */}
        {run && status === 'failure' && (
          <div className="rounded border border-danger/50 bg-danger/10 p-3 text-sm" data-testid="run-error">
            <div className="font-semibold text-danger mb-1">
              Failed{run.error?.task ? <> at task <span className="font-mono">{run.error.task}</span></> : ''}
            </div>
            <div className="text-slate-200 whitespace-pre-wrap text-xs">
              {run.error?.message || 'The run failed without a message; see the step list below.'}
            </div>
          </div>
        )}

        {/* the result */}
        {run && status === 'success' && (
          <div className="rounded border border-ok/40 bg-ok/10 p-3 text-xs" data-testid="run-result">
            <div className="font-semibold text-ok mb-1 text-sm">
              {run.kind === 'provision' ? `${run.rig} is provisioned` : `runtime deployed on ${run.rig}`}
            </div>
            {run.vpn_ip && <Row k="VPN IP"><span className="font-mono">{run.vpn_ip}</span></Row>}
            {run.pki && <Row k="certificate until">{fmtTime(run.pki.not_after)}
              {run.pki.serial && <span className="text-muted font-mono"> · serial {String(run.pki.serial)}</span>}</Row>}
            {acc && <Row k="Mender"><span className={acc[1]}>{acc[0]}</span></Row>}
            {run.rig_class && <Row k="rig class">{run.rig_class}</Row>}
            {skipped.length > 0 && (
              <Row k="skipped by design">
                <span>{skipped.map((s) => (typeof s === 'string' ? s : (s.name || JSON.stringify(s)))).join(', ')}</span>
                {run.rig_class && <span className="block text-muted">(not applicable to a {run.rig_class} rig — not an error)</span>}
              </Row>
            )}
          </div>
        )}

        {/* the step list */}
        {run && (
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="text-xs text-muted mb-1">tasks ({tasks.length})</div>
            <ol ref={listRef} className="flex-1 overflow-auto border border-edge/50 rounded bg-black/20 text-xs divide-y divide-edge/30" data-testid="run-tasks">
              {tasks.map((t, i) => (
                <li key={i} className={`flex items-center gap-2 px-2 py-1 ${t.result === 'failed' ? 'bg-danger/10' : ''}`}>
                  <span className={`badge text-[10px] w-16 justify-center ${RESULT_STYLE[t.result] || 'bg-edge/40'}`}>{t.result}</span>
                  <span className="font-mono text-slate-200">{t.name}</span>
                </li>
              ))}
              {isActive(status) && (
                <li className="flex items-center gap-2 px-2 py-1">
                  <span className="badge text-[10px] w-16 justify-center bg-sky-500/15 text-sky-300">running</span>
                  <span className="font-mono text-muted">{step?.name || (status === 'pending' ? 'waiting to start…' : '…')}</span>
                </li>
              )}
              {tasks.length === 0 && !isActive(status) && <li className="px-2 py-1 text-muted">no tasks reported</li>}
            </ol>
          </div>
        )}
      </div>
    </div>
  )
}

// A 409 from POST /api/rigs or /deploy: "already running: open".
export function AlreadyRunning({ err, rig, onOpened }) {
  const { openRun } = useRuns()
  const runId = err?.body?.run_id || err?.body?.detail?.run_id
  return (
    <div className="text-xs text-amber-300" data-testid="already-running">
      already running{rig ? ` on ${rig}` : ''}: {runId
        ? <button className="underline hover:text-white" onClick={() => { openRun(runId); onOpened && onOpened() }}>open</button>
        : <span className="text-muted">(see the device row)</span>}
    </div>
  )
}
