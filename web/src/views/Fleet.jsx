import React, { useState, useEffect, useMemo } from 'react'
import { api } from '../api'
import { usePoll, useSerialPoll } from '../App'
import { AddDeviceModal } from '../components/AddDeviceModal'
import { AlreadyRunning, fmtTime, isActive, useRuns } from '../components/RunDrawer'

// Fleet — ONE list of every device the GS knows (GET /api/fleet): Mender devices
// AND base-plane-only (colony) rigs such as the SK. The device NAME is the
// identity. Per row: source, com state (mTLS / plaintext / down / unknown), the
// last provisioning/deploy run, and "Deploy runtime" (same Run drawer as Add
// device). Mender extras (group, Health/SM/UCM) are merged in by mender_id.

// com state badge: mTLS green, plaintext red, down grey, unknown outline.
const COM = {
  mtls: ['mTLS', 'bg-ok/20 text-ok border border-ok/40'],
  plaintext: ['plaintext', 'bg-danger/20 text-danger border border-danger/50'],
  down: ['down', 'bg-slate-500/25 text-slate-400 border border-slate-500/40'],
  unknown: ['unknown', 'bg-transparent text-muted border border-muted/60'],
}
function ComBadge({ com }) {
  const st = com?.state || 'unknown'
  const [t, cls] = COM[st] || COM.unknown
  return <span className={`badge text-[11px] ${cls}`} title={com?.detail || st} data-testid={`com-${st}`}>{t}</span>
}
function SourceBadge({ s }) {
  if (s === 'colony') return <span className="badge text-[10px] bg-violet-500/15 text-violet-300" title="base plane only (colony), no Mender" data-testid="src-colony">colony only</span>
  return <span className="badge text-[10px] bg-cyan-500/15 text-cyan-300" title={s || ''} data-testid="src-mender">Mender + colony</span>
}
const RUN_COLOR = { pending: 'text-sky-300', running: 'text-amber-300', success: 'text-ok', failure: 'text-danger' }
function RunStatus({ s }) {
  return <span className={RUN_COLOR[s] || 'text-muted'}>
    {isActive(s) && <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 animate-pulse bg-current" />}{s || '—'}</span>
}

// Timeline event authority -> colour. base=colony (runtime base), app=Mender
// (the SWP/app), state=ECU plane (UCM/SM). Drives the Timeline drawer dots.
const AUTH_COLOR = { base: "#4CAF50", app: "#42A5F5", state: "#FFB300" }

// A small state pill (Health / SM / UCM). Greys out when the rig hasn't reported.
function Pill({ v }) {
  if (!v) return <span className="text-muted">—</span>
  return <span className="badge bg-edge/40 text-slate-200 font-mono text-[11px]">{v}</span>
}

// One timeline event. An app (Mender) deployment event carries a `log_ref` —
// expand it to fetch + show that device's Mender deployment LOG inline (the
// on-device update-module output), reusing this same per-device panel.
function TimelineEvent({ e }) {
  const [open, setOpen] = useState(false)
  const [log, setLog] = useState(null)   // null=unloaded, ''=empty, string=text
  const [err, setErr] = useState(null)
  const ref = e.log_ref
  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && log === null && ref) {
      try {
        const r = await api.deploymentDeviceLog(ref.deployment_id, ref.device_id)
        setLog(r?.log ?? '')
      } catch (x) { setErr(x.message) }
    }
  }
  return (
    <li className="ml-4 mb-3">
      <span className="absolute -left-[5px] w-2.5 h-2.5 rounded-full"
        style={{ background: AUTH_COLOR[e.authority] || '#90A4AE' }} />
      <div className="flex items-baseline gap-2">
        <span className="badge text-[10px]" style={{ background: (AUTH_COLOR[e.authority] || '#90A4AE') + '22', color: AUTH_COLOR[e.authority] || '#90A4AE' }}>
          {e.authority}
        </span>
        <span className="text-sm text-slate-200">{e.title}</span>
        {ref && (
          <button className="btn-ghost text-[10px] px-1 py-0" onClick={toggle}
            title="Mender deployment log for this device">
            {open ? 'log ▾' : 'log ▸'}
          </button>
        )}
        {e.status && <span className="text-[11px] text-muted ml-auto">{typeof e.status === "string" ? e.status : JSON.stringify(e.status)}</span>}
      </div>
      {e.detail && <div className="text-[11px] text-muted">{typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}</div>}
      {e.ts != null && <div className="text-[10px] text-muted/70">
        {typeof e.ts === 'number' ? new Date(e.ts * 1000).toLocaleString() : String(e.ts)}</div>}
      {open && (
        <div className="mt-1 rounded bg-black/40 border border-edge/50 p-2">
          {err && <div className="text-[11px] text-red-400">log: {err}</div>}
          {!err && log === null && <div className="text-[11px] text-muted">loading log…</div>}
          {!err && log === '' && <div className="text-[11px] text-muted">
            {e.status === 'success'
              ? 'no log (Mender keeps logs only for failed updates)'
              : 'no log reported by this device yet.'}</div>}
          {!err && log && (
            <pre className="text-[10px] leading-tight text-slate-300 whitespace-pre-wrap max-h-64 overflow-auto font-mono">{log}</pre>
          )}
        </div>
      )}
    </li>
  )
}

// The device side panel: the Mender merged timeline (Mender devices) + the
// provisioning / deploy runs of the rig (every row, colony-only included). The
// addresses are read-only: they come from the provision (no Set-IP / Add-to-VPN).
function Timeline({ device, onClose }) {
  const { openRun, tick } = useRuns()
  const [tl, setTl] = useState(null)
  const [err, setErr] = useState(null)
  const [runs, setRuns] = useState(null)
  const [runsErr, setRunsErr] = useState(null)
  useEffect(() => {
    let live = true
    if (device.mender_id) {
      api.deviceTimeline(device.mender_id).then((d) => live && setTl(d)).catch((e) => live && setErr(e.message))
    }
    return () => { live = false }
  }, [device.mender_id])
  useEffect(() => {
    let live = true
    api.runs(device.name).then((r) => live && setRuns(r)).catch((e) => live && setRunsErr(e.message))
    return () => { live = false }
  }, [device.name, tick])
  const events = tl?.events || []
  return (
    <div className="fixed inset-0 bg-black/40 flex justify-end z-50" onClick={onClose}>
      <div className="card w-[34rem] h-full p-4 overflow-auto rounded-none" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-1">
          <h3 className="font-semibold">{device.name}</h3>
          <SourceBadge s={device.source} />
          <ComBadge com={device.com} />
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <div className="text-xs text-muted mb-3 flex gap-3 flex-wrap">
          <span>role: <span className="text-slate-300">{device.role || '—'}</span></span>
          <span>class: <span className="text-slate-300">{device.rig_class || '—'}</span></span>
          {device.fleet && <span>fleet: <span className="text-slate-300">{device.fleet}</span></span>}
          {device.mender_id && <span>group: <span className="text-slate-300">{device.group || 'ungrouped'}</span></span>}
          <span>base: <span className="font-mono text-slate-300">{device.base_version || '—'}</span></span>
          <span>app: <span className="text-slate-300">{device.artifact || '—'}</span></span>
          {(device.health || device.sm_state || device.ucm_version) && (
            <span>Health <Pill v={device.health} /> SM <Pill v={device.sm_state} /> UCM <Pill v={device.ucm_version} /></span>
          )}
        </div>
        <div className="text-xs mb-3 flex items-center gap-3 flex-wrap border-y border-edge/40 py-2">
          <span className="text-muted">address: <span className="font-mono text-slate-300">{device.address || '—'}</span></span>
          <span className="text-muted">vpn ip: <span className="font-mono text-slate-300">{device.vpn_ip || '—'}</span></span>
          {device.com?.detail && <span className="text-muted">com: {device.com.detail}</span>}
        </div>

        <div className="text-xs font-semibold text-slate-200 mb-1">Runs</div>
        {runsErr && <div className="text-xs text-red-400 mb-2">runs: {runsErr}</div>}
        {!runs && !runsErr && <div className="text-xs text-muted mb-2">loading runs…</div>}
        {runs && runs.length === 0 && <div className="text-xs text-muted mb-2">no runs.</div>}
        {runs && runs.length > 0 && (
          <table className="w-full mb-3">
            <tbody className="divide-y divide-edge/40">
              {runs.slice(0, 12).map((r) => (
                <tr key={r.run_id} className="hover:bg-edge/20 cursor-pointer" onClick={() => openRun(r.run_id)}>
                  <td className="cell text-xs">{r.kind}</td>
                  <td className="cell text-xs"><RunStatus s={r.status} /></td>
                  <td className="cell text-[11px] text-muted">{fmtTime(r.finished || r.started)}</td>
                  <td className="cell text-right text-[11px] text-accent">open</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {device.mender_id && (
          <>
            <div className="text-xs font-semibold text-slate-200 mb-1">Timeline</div>
            {err && <div className="text-xs text-red-400">timeline: {err}</div>}
            {!tl && !err && <div className="text-sm text-muted">loading timeline…</div>}
            {tl?.errors && <div className="text-[11px] text-amber-400 mb-2">partial: {Object.entries(tl.errors).map(([k, v]) => `${k}: ${v}`).join('; ')}</div>}
            <ol className="relative border-l border-edge/60 ml-2">
              {events.map((e, i) => <TimelineEvent key={i} e={e} />)}
              {events.length === 0 && tl && <li className="ml-4 text-sm text-muted">no events.</li>}
            </ol>
          </>
        )}
      </div>
    </div>
  )
}

// Inline group editor — a select that PATCHes the device's Mender group on change
// (Mender devices only: a colony-only rig has no group).
function GroupCell({ device, groups, onChanged }) {
  const [busy, setBusy] = useState(false)
  if (!device.mender_id) return <span className="text-muted">—</span>
  const change = async (g) => {
    if (!g || g === device.group) return
    setBusy(true)
    try { await api.assignGroup(device.mender_id, g); onChanged() } catch (e) { alert(`group: ${e.message}`) }
    setBusy(false)
  }
  return (
    <select
      className="bg-ink text-xs text-muted hover:text-slate-200 outline-none cursor-pointer rounded px-1"
      disabled={busy} value={device.group || ''} onClick={(e) => e.stopPropagation()}
      onChange={(e) => change(e.target.value)}>
      <option value="">{device.group || 'ungrouped'}</option>
      {groups.filter((g) => g.name !== device.group).map((g) => (
        <option key={g.name} value={g.name}>{g.name}</option>
      ))}
    </select>
  )
}

// Deploy runtime: pick a runtime version from the runtime plane (the same list
// Releases shows) -> POST /api/rigs/{name}/deploy -> the Run drawer.
function DeployRuntimeDialog({ device, onClose }) {
  const { started } = useRuns()
  const [versions, setVersions] = useState(null)
  const [loadErr, setLoadErr] = useState(null)
  const [ver, setVer] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.runtimePlane().then((d) => {
      // runtime_version is ABI-agnostic (colony resolves the build per rig), so
      // one entry per version, listing the distros it is built for.
      const by = new Map()
      for (const r of (d?.releases || []).filter((x) => !x._error && x.version)) {
        if (!by.has(r.version)) by.set(r.version, [])
        if (r.distro) by.get(r.version).push(r.distro)
      }
      const list = [...by.entries()].map(([version, distros]) => ({ version, distros }))
        .sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }))
      setVersions(list)
      setVer(list[0]?.version || '')
    }).catch((e) => setLoadErr(e.message))
  }, [])
  const deploy = async () => {
    if (!ver) return
    setBusy(true); setErr(null)
    try {
      const r = await api.deployRig(device.name, ver)
      started(device.name, r.run_id)
      onClose()
    } catch (e) { setErr(e) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[28rem] p-5" data-testid="deploy-runtime" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-medium flex-1" style={{ color: '#4A90E2' }}>Deploy runtime on {device.name}</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>✕</button>
        </div>
        <div className="text-xs text-muted mb-2">
          current base: <span className="font-mono text-slate-300">{device.base_version || '—'}</span>
        </div>
        {loadErr && <div className="text-xs text-red-400 mb-2">runtime plane: {loadErr}</div>}
        {!versions && !loadErr && <div className="text-xs text-muted mb-2">loading runtime versions…</div>}
        {versions && versions.length === 0 && <div className="text-xs text-muted mb-2">no runtime in the runtime plane — publish one first (Releases).</div>}
        {versions && versions.length > 0 && (
          <select className="input w-full text-sm mb-3" name="runtime_version" value={ver} onChange={(e) => setVer(e.target.value)}>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>{v.version}{v.distros.length ? `  (${v.distros.join(', ')})` : ''}</option>
            ))}
          </select>
        )}
        {err && err.status === 409 && <AlreadyRunning err={err} rig={device.name} onOpened={onClose} />}
        {err && err.status !== 409 && <div className="text-xs text-red-400">{err.message}</div>}
        <button className="btn w-full mt-3" disabled={busy || !ver} onClick={deploy}>{busy ? '…' : 'Deploy →'}</button>
      </div>
    </div>
  )
}

const COLS = 12
function DeviceRow({ d, groups, onSelect, reload, checked, onCheck, activeRun, onDeploy }) {
  const { openRun } = useRuns()
  const lr = d.last_run
  return (
    <tr className="hover:bg-edge/20 cursor-pointer" data-testid={`row-${d.name}`}>
      <td className="cell" onClick={(e) => e.stopPropagation()}>
        {d.mender_id && <input type="checkbox" checked={checked} onChange={(e) => onCheck(d.mender_id, e.target.checked)} />}
      </td>
      <td className="cell font-mono text-xs" onClick={() => onSelect(d)}>{d.name}</td>
      <td className="cell" onClick={() => onSelect(d)}><SourceBadge s={d.source} /></td>
      <td className="cell text-xs" onClick={() => onSelect(d)}>{d.role || '—'}
        {d.rig_class && <span className="block text-[10px] text-muted">{d.rig_class}</span>}</td>
      <td className="cell text-xs font-mono" onClick={() => onSelect(d)}>{d.address || '—'}
        {d.vpn_ip && <span className="block text-[10px] text-muted">vpn {d.vpn_ip}</span>}</td>
      <td className="cell text-xs"><GroupCell device={d} groups={groups} onChanged={reload} /></td>
      <td className="cell text-xs font-mono" onClick={() => onSelect(d)}>{d.base_version || <span className="text-muted">—</span>}</td>
      <td className="cell text-xs" onClick={() => onSelect(d)}>{d.artifact || <span className="text-muted">—</span>}</td>
      <td className="cell text-xs" onClick={() => onSelect(d)}>{d.auth_status || <span className="text-muted">—</span>}</td>
      <td className="cell" onClick={() => onSelect(d)}><ComBadge com={d.com} /></td>
      <td className="cell text-xs" onClick={(e) => { e.stopPropagation(); if (lr?.run_id) openRun(lr.run_id) }}
          title={lr?.run_id ? 'open this run' : undefined}>
        {lr ? <span className="hover:underline">{lr.kind} <RunStatus s={lr.status} /></span> : <span className="text-muted">—</span>}
      </td>
      <td className="cell text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
        {activeRun && (
          <button className="btn-ghost text-[11px] px-2 py-0.5 mr-1" onClick={() => openRun(activeRun)}
                  data-testid={`open-run-${d.name}`}>running — open</button>
        )}
        <button className="btn text-[11px] px-2 py-1" disabled={!!activeRun}
                title={activeRun ? 'a run is active on this device' : 'deploy a runtime version on this device'}
                onClick={() => onDeploy(d)} data-testid={`deploy-${d.name}`}>Deploy runtime</button>
      </td>
    </tr>
  )
}

const GROUP_AXES = {
  none: () => null,
  Source: (d) => (d.source === 'colony' ? 'colony only' : 'Mender + colony'),
  Role: (d) => d.role || '(no role)',
  App: (d) => d.artifact || '(no app)',
  Group: (d) => (d.mender_id ? d.group || '(ungrouped)' : '(not in Mender)'),
}
// client-side filter on the Mender auth status; colony-only rigs have none.
const AUTH_FILTERS = {
  all: () => true,
  accepted: (d) => d.auth_status === 'accepted',
  pending: (d) => d.auth_status === 'pending',
  preauthorized: (d) => d.auth_status === 'preauthorized',
  rejected: (d) => d.auth_status === 'rejected',
  'colony only': (d) => d.source === 'colony',
}

// Assign N selected devices to a static group (existing or new) — the Mender
// "check devices -> Group" action. Group membership is written into Mender.
function GroupDialog({ ids, groups, onClose, onDone }) {
  const [group, setGroup] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const apply = async () => {
    if (!group.trim()) { setErr('enter or pick a group name'); return }
    setBusy(true); setErr(null)
    try { for (const id of ids) await api.assignGroup(id, group.trim()); onDone() }
    catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[26rem] p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3"><h3 className="font-semibold">Add {ids.length} device(s) to group</h3>
          <button className="btn-ghost ml-auto" onClick={onClose}>✕</button></div>
        <input className="input w-full mb-2 text-sm" placeholder="group name (new or existing)"
               list="grouplist" value={group} onChange={(e) => setGroup(e.target.value)} />
        <datalist id="grouplist">{groups.map((g) => <option key={g.name} value={g.name} />)}</datalist>
        {err && <div className="text-xs text-red-400 mb-2">{err}</div>}
        <button className="btn w-full" disabled={busy} onClick={apply}>{busy ? '…' : 'Add to group'}</button>
      </div>
    </div>
  )
}

export function Fleet() {
  const { active, tick } = useRuns()
  // ONE list (Mender + colony-only), polled serially: /api/fleet probes com per rig.
  const { data, loading, error, refresh } = useSerialPoll(() => api.fleet(), [], 6000)
  // Mender extras (group, fleet type, Health/SM/UCM), merged by mender_id.
  const { data: mdata, refresh: mrefresh } = usePoll(() => api.devices(null, 'any'), [], 15000)
  const { data: gdata, refresh: grefresh } = usePoll(() => api.groups(), [], 30000)
  const [showAdd, setShowAdd] = useState(false)
  const [showGroup, setShowGroup] = useState(false)
  const [deployFor, setDeployFor] = useState(null)
  const [selected, setSelected] = useState(null)
  const [groupBy, setGroupBy] = useState('none')
  const [authFilter, setAuthFilter] = useState('all')
  const [checked, setChecked] = useState({})                // mender_id -> bool

  // a run ended (drawer saw success/failure): refresh the list now.
  useEffect(() => { if (tick) refresh() }, [tick, refresh])

  const byMender = Object.fromEntries((mdata?.devices || []).map((d) => [d.id, d]))
  const devices = (data || []).map((r) => {
    const m = r.mender_id ? byMender[r.mender_id] : null
    return m ? { ...r, group: m.group, fleet: m.fleet, health: m.health, sm_state: m.sm_state,
                 ucm_version: m.ucm_version, artifact: r.artifact || m.artifact } : r
  }).filter(AUTH_FILTERS[authFilter] || AUTH_FILTERS.all)
  const groups = gdata?.groups || []
  const reload = () => { refresh(); mrefresh(); grefresh() }
  const checkedIds = Object.keys(checked).filter((k) => checked[k])
  const onCheck = (id, v) => setChecked((c) => ({ ...c, [id]: v }))
  const clearChecks = () => setChecked({})
  // the active run of a rig: what the drawer saw, else the fleet's last_run.
  const activeRunOf = (d) => active[d.name] || (isActive(d.last_run?.status) ? d.last_run.run_id : null)

  const keyer = GROUP_AXES[groupBy] || GROUP_AXES.none
  const sections = useMemo(() => {
    if (groupBy === 'none') return null
    const by = {}
    for (const d of devices) (by[keyer(d)] ||= []).push(d)
    return Object.keys(by).sort().map((k) => ({ key: k, rows: by[k] }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, mdata, groupBy, authFilter])

  const renderRows = (list) => list.map((d) => (
    <DeviceRow key={d.name} d={d} groups={groups} onSelect={setSelected} reload={reload}
               checked={!!checked[d.mender_id]} onCheck={onCheck}
               activeRun={activeRunOf(d)} onDeploy={setDeployFor} />
  ))
  const selDev = selected && (devices.find((d) => d.name === selected.name) || selected)

  return (
    <div className="pane h-full">
      <div className="pane-head">
        Fleet
        <span className="text-muted font-normal text-xs ml-2">{devices.length} · {groups.length} group(s)</span>
        <span className="ml-3 text-xs text-muted">Show:</span>
        <select className="bg-ink border border-edge rounded text-xs ml-1 px-1 outline-none cursor-pointer text-slate-200"
                value={authFilter} onChange={(e) => { setAuthFilter(e.target.value); clearChecks() }}>
          {Object.keys(AUTH_FILTERS).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="ml-3 text-xs text-muted">Group by:</span>
        <select className="bg-ink border border-edge rounded text-xs ml-1 px-1 outline-none cursor-pointer text-slate-200"
                value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
          {Object.keys(GROUP_AXES).map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <span className="ml-auto flex gap-1 items-center">
          <button className="btn" disabled={checkedIds.length === 0}
                  title={checkedIds.length === 0 ? 'select Mender device(s) to group' : ''}
                  onClick={() => setShowGroup(true)}>
            Group{checkedIds.length ? ` (${checkedIds.length})` : ''}
          </button>
          <button className="btn" onClick={() => setShowAdd(true)} data-testid="add-device-btn">Add device</button>
        </span>
      </div>
      {error && (
        <div className="bg-red-500/15 border-b border-red-500/40 text-red-300 text-xs px-3 py-2">
          ⚠ The fleet can't be read (devices are NOT deleted; connectivity/auth error). {error}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr>
              <th className="th w-6"></th>
              <th className="th">Device</th><th className="th">Source</th><th className="th">Role</th>
              <th className="th">Address</th><th className="th">Group</th>
              <th className="th">Base runtime</th><th className="th">App</th>
              <th className="th">Auth</th><th className="th">Com</th><th className="th">Last run</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && <tr><td className="cell text-muted" colSpan={COLS}>loading…</td></tr>}
            {error && !data && <tr><td className="cell text-red-300" colSpan={COLS}>fleet unavailable — see banner</td></tr>}
            {data && devices.length === 0 && <tr><td className="cell text-muted" colSpan={COLS}>no devices ({authFilter}) — Add device to bring a board in</td></tr>}
            {!sections && renderRows(devices)}
            {sections && sections.map((s) => (
              <React.Fragment key={s.key}>
                <tr className="bg-edge/30"><td className="cell text-xs font-semibold text-slate-200" colSpan={COLS}>
                  {groupBy}: {s.key} <span className="text-muted font-normal">· {s.rows.length}</span></td></tr>
                {renderRows(s.rows)}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {showAdd && <AddDeviceModal onClose={() => setShowAdd(false)} onStarted={() => refresh()} />}
      {deployFor && <DeployRuntimeDialog device={deployFor} onClose={() => { setDeployFor(null); refresh() }} />}
      {showGroup && <GroupDialog ids={checkedIds} groups={groups} onClose={() => setShowGroup(false)}
                                 onDone={() => { setShowGroup(false); clearChecks(); reload() }} />}
      {selDev && <Timeline device={selDev} onClose={() => setSelected(null)} />}
    </div>
  )
}
