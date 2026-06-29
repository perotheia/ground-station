import React, { useState, useEffect, useMemo } from 'react'
import { api } from '../api'
import { usePoll } from '../App'
import { CreateTargetModal } from '../components/CreateTargetModal'

// Fleet — the device inventory (UF "Targets"): status dots, the base/app version
// pair, Theia state (Health · SM · UCM), group assignment, Connect (the GS
// funnel), and a per-device MERGED TIMELINE side-panel (colony base + Mender app
// + state, chronological).

// UF status-dot color model. A board is `synced` only when BOTH planes agree
// (Mender accepted AND present in the Observability cluster); otherwise it's
// degraded/registered. base_source=mirror (no live supervisor read) shows amber.
const DOT = {
  'mender+com': ['#4CAF50', 'synchronized (Mender + Observability)'],
  'mender-only': ['#E57373', 'registered, no observability'],
  'com-only': ['#FFB300', 'observed, not enrolled'],
}
function Dot({ s }) {
  const [c, t] = DOT[s] || ['#90A4AE', s || 'unknown']
  return <span title={t} className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: c }} />
}

// A small state pill (Health / SM / UCM). Greys out when the rig hasn't reported.
function Pill({ v }) {
  if (!v) return <span className="text-muted">—</span>
  return <span className="badge bg-edge/40 text-slate-200 font-mono text-[11px]">{v}</span>
}

function Timeline({ device, onClose }) {
  const [tl, setTl] = useState(null)
  const [err, setErr] = useState(null)
  const [localIp, setLocalIp] = useState(device.local_ip || '')
  const [remoteIp, setRemoteIp] = useState(device.remote_ip || '')
  const [ipMsg, setIpMsg] = useState(null)
  const addVpn = async () => {
    const ip = window.prompt('Remote (VPN) IP for this device:', remoteIp || '')
    if (!ip) return
    try { await api.addToVpn(device.id, ip.trim()); setRemoteIp(ip.trim()); setIpMsg('remote_ip set — ops now prefer it') }
    catch (e) { setIpMsg(`vpn: ${e.message}`) }
  }
  const editLocal = async () => {
    const ip = window.prompt('Local (on-site) IP for this device:', localIp || '')
    if (!ip) return
    try { await api.setIp(device.id, ip.trim(), 'local'); setLocalIp(ip.trim()); setIpMsg('local_ip set') }
    catch (e) { setIpMsg(`ip: ${e.message}`) }
  }
  useEffect(() => {
    let live = true
    api.deviceTimeline(device.id).then((d) => live && setTl(d)).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [device.id])
  const events = tl?.events || []
  return (
    <div className="fixed inset-0 bg-black/40 flex justify-end z-50" onClick={onClose}>
      <div className="card w-[34rem] h-full p-4 overflow-auto rounded-none" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-1">
          <h3 className="font-semibold">{device.name || device.id?.slice(0, 12)}</h3>
          <Dot s={device.connected} />
          <button className="btn-ghost ml-auto" onClick={onClose}>Close</button>
        </div>
        <div className="text-xs text-muted mb-3 flex gap-3 flex-wrap">
          <span>fleet: <span className="text-slate-300">{device.fleet || '—'}</span></span>
          <span>group: <span className="text-slate-300">{device.group || 'ungrouped'}</span></span>
          <span>base: <span className="font-mono text-slate-300">{device.base_version || '—'}</span>
            {device.base_source && <span className="text-muted"> ({device.base_source})</span>}</span>
          <span>app: <span className="text-slate-300">{device.artifact || '—'}</span></span>
        </div>
        {/* IP / reachability — local (deploy-time) + remote (VPN). We DON'T assume
            local stays reachable; ops prefer remote_ip||local_ip. */}
        <div className="text-xs mb-3 flex items-center gap-3 flex-wrap border-y border-edge/40 py-2">
          <span className="text-muted">local_ip: <span className="font-mono text-slate-300">{localIp || '—'}</span>
            <button className="icon-btn ml-1" title="set local IP" onClick={editLocal}>✎</button></span>
          <span className="text-muted">remote_ip: <span className="font-mono text-slate-300">{remoteIp || '—'}</span></span>
          <button className="btn-ghost text-xs ml-auto" onClick={addVpn}>Add to VPN</button>
        </div>
        {ipMsg && <div className="text-[11px] text-ok mb-2">{ipMsg}</div>}
        {err && <div className="text-xs text-red-400">timeline: {err}</div>}
        {!tl && !err && <div className="text-sm text-muted">loading timeline…</div>}
        {tl?.errors && <div className="text-[11px] text-amber-400 mb-2">partial: {Object.entries(tl.errors).map(([k, v]) => `${k}: ${v}`).join('; ')}</div>}
        <ol className="relative border-l border-edge/60 ml-2">
          {events.map((e, i) => (
            <li key={i} className="ml-4 mb-3">
              <span className="absolute -left-[5px] w-2.5 h-2.5 rounded-full"
                style={{ background: AUTH_COLOR[e.authority] || '#90A4AE' }} />
              <div className="flex items-baseline gap-2">
                <span className="badge text-[10px]" style={{ background: (AUTH_COLOR[e.authority] || '#90A4AE') + '22', color: AUTH_COLOR[e.authority] || '#90A4AE' }}>
                  {e.authority}
                </span>
                <span className="text-sm text-slate-200">{e.title}</span>
                {e.status && <span className="text-[11px] text-muted ml-auto">{e.status}</span>}
              </div>
              {e.detail && <div className="text-[11px] text-muted">{e.detail}</div>}
              {e.ts && <div className="text-[10px] text-muted/70">{String(e.ts)}</div>}
            </li>
          ))}
          {events.length === 0 && tl && <li className="ml-4 text-sm text-muted">no events.</li>}
        </ol>
      </div>
    </div>
  )
}

// Inline group editor — a select that PATCHes the device's group on change.
function GroupCell({ device, groups, onChanged }) {
  const [busy, setBusy] = useState(false)
  const change = async (g) => {
    if (!g || g === device.group) return
    setBusy(true)
    try { await api.assignGroup(device.id, g); onChanged() } catch (e) { alert(`group: ${e.message}`) }
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

// One device row — checkbox + the device columns. Renders flat or under a header.
function DeviceRow({ d, groups, onSelect, reload, checked, onCheck }) {
  return (
    <tr className="hover:bg-edge/20 cursor-pointer">
      <td className="cell" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={checked} onChange={(e) => onCheck(d.id, e.target.checked)} />
      </td>
      <td className="cell font-mono text-xs" onClick={() => onSelect(d)}>{d.name || d.id?.slice(0, 12)}</td>
      <td className="cell text-xs" onClick={() => onSelect(d)}>{d.fleet || <span className="text-muted">—</span>}</td>
      <td className="cell text-xs"><GroupCell device={d} groups={groups} onChanged={reload} /></td>
      <td className="cell text-xs font-mono" onClick={() => onSelect(d)}>
        {d.base_version || <span className="text-muted">—</span>}
        {d.base_source === 'mirror' && <span title="from Mender mirror tag, not a live supervisor read" className="ml-1 text-amber-400">◐</span>}
      </td>
      <td className="cell text-xs" onClick={() => onSelect(d)}>{d.artifact || <span className="text-muted">—</span>}</td>
      <td className="cell text-xs" onClick={() => onSelect(d)}><Pill v={d.health} /></td>
      <td className="cell text-xs" onClick={() => onSelect(d)}><Pill v={d.sm_state} /></td>
      <td className="cell text-xs" onClick={() => onSelect(d)}><Pill v={d.ucm_version} /></td>
      <td className="cell flex items-center gap-2 text-xs" onClick={() => onSelect(d)}>
        <Dot s={d.connected} /><span className="text-muted">{d.connected}</span>
      </td>
    </tr>
  )
}

const GROUP_AXES = {
  none: () => null,
  Fleet: (d) => d.fleet || '(no fleet)',
  App: (d) => d.artifact || '(no app)',
  Group: (d) => d.group || '(ungrouped)',
}
const STATUSES = ['accepted', 'pending', 'preauthorized', 'rejected', 'any']

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

// Preauthorize a device before it checks in: identity MAC + pubkey → devauth.
// Preauthorize a 3rd-party-installed device BEFORE it checks in. Same fields as
// Create-New-Target, but there's no host to probe: Controller ID is a GENERATED
// UUID (the device must report it as its mender identity), and we paste the
// device's PEM public key (the 3rd party gives us). On check-in Mender matches
// identity+key and auto-accepts. We also hand them OUR pubkey for authorized_keys
// (so we can SSH it to verify reachability / deploy).
function PreauthorizeDialog({ onClose, onDone }) {
  const [cid] = useState(() => (crypto.randomUUID ? crypto.randomUUID() : `dev-${Date.now()}`))
  const [name, setName] = useState('')
  const [type, setType] = useState('')
  const [desc, setDesc] = useState('')
  const [pubkey, setPubkey] = useState('')
  const [types, setTypes] = useState([])
  const [ourKey, setOurKey] = useState('')
  const [busy, setBusy] = useState(false); const [err, setErr] = useState(null)
  useEffect(() => {
    api.deviceTypes().then((d) => { setTypes(d.types || []); setType((d.types || [])[0] || '') }).catch(() => {})
    api.ourPubkey().then((d) => setOurKey(d.pubkey || '')).catch(() => {})
  }, [])
  const save = async () => {
    if (!pubkey.trim()) { setErr("paste the device's PEM public key"); return }
    setBusy(true); setErr(null)
    try {
      await api.preauthorize(cid, pubkey.trim(), name || undefined, type || undefined, desc || undefined)
      onDone()
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[32rem] p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-3">
          <h3 className="font-medium text-center flex-1" style={{ color: '#4A90E2' }}>Preauthorize a device</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>✕</button>
        </div>
        <div className="mb-3 rounded border border-edge bg-ink/60 p-2">
          <div className="text-[11px] text-muted mb-1">① Give the installer OUR public key (add to the device's
            <code> authorized_keys</code> so we can reach it):</div>
          <div className="flex gap-2 items-start">
            <textarea readOnly className="input text-[10px] h-12 font-mono flex-1" value={ourKey} />
            <button className="btn-ghost text-xs" onClick={() => navigator.clipboard?.writeText(ourKey)}>copy</button>
          </div>
        </div>
        <div className="text-[11px] text-muted mb-2">② Register the device:</div>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs">Controller ID <span className="text-red-400">*</span></label>
            <input className="input flex-1 text-sm font-mono" value={cid} readOnly title="generated identity (UUID)" />
          </div>
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs text-muted">Name</label>
            <input className="input flex-1 text-sm" placeholder="rig-2" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs text-muted">Type</label>
            <select className="input flex-1 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
              {types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex items-start gap-2">
            <label className="w-28 text-right text-xs text-muted pt-1">Description</label>
            <textarea className="input flex-1 text-sm h-12" placeholder="3rd-party installed" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="flex items-start gap-2">
            <label className="w-28 text-right text-xs">Public key <span className="text-red-400">*</span></label>
            <textarea className="input flex-1 text-xs h-20 font-mono" placeholder="-----BEGIN PUBLIC KEY-----" value={pubkey} onChange={(e) => setPubkey(e.target.value)} />
          </div>
        </div>
        <div className="text-[11px] text-red-400 mt-3">* Mandatory Field</div>
        {err && <div className="text-xs text-red-400 mt-1">{err}</div>}
        <div className="flex justify-center gap-6 mt-3 text-slate-300">
          <button className="hover:text-white" disabled={busy} onClick={save}>💾 Preauthorize</button>
          <button className="hover:text-white" onClick={onClose}>✕ Cancel</button>
        </div>
      </div>
    </div>
  )
}

export function Fleet() {
  const [status, setStatus] = useState('accepted')
  const { data, loading, error, refresh } = usePoll(() => api.devices(null, status), [status], 6000)
  const { data: gdata, refresh: grefresh } = usePoll(() => api.groups(), [], 30000)
  const [showConnect, setShowConnect] = useState(false)     // SSH-probe Create-Target modal
  const [showPreauth, setShowPreauth] = useState(false)
  const [showGroup, setShowGroup] = useState(false)
  const [connectMenu, setConnectMenu] = useState(false)
  const [selected, setSelected] = useState(null)
  const [groupBy, setGroupBy] = useState('none')
  const [checked, setChecked] = useState({})                // id -> bool
  const devices = data?.devices || []
  const groups = gdata?.groups || []
  const reload = () => { refresh(); grefresh() }
  const checkedIds = Object.keys(checked).filter((k) => checked[k])
  const onCheck = (id, v) => setChecked((c) => ({ ...c, [id]: v }))
  const clearChecks = () => setChecked({})

  const keyer = GROUP_AXES[groupBy] || GROUP_AXES.none
  const sections = useMemo(() => {
    if (groupBy === 'none') return null
    const by = {}
    for (const d of devices) (by[keyer(d)] ||= []).push(d)
    return Object.keys(by).sort().map((k) => ({ key: k, rows: by[k] }))
  }, [devices, groupBy])

  const renderRows = (list) => list.map((d) => (
    <DeviceRow key={d.id} d={d} groups={groups} onSelect={setSelected} reload={reload}
               checked={!!checked[d.id]} onCheck={onCheck} />
  ))

  return (
    <div className="pane h-full">
      <div className="pane-head">
        Fleet
        <span className="text-muted font-normal text-xs ml-2">{devices.length} · {groups.length} group(s)</span>
        <span className="ml-3 text-xs text-muted">Status:</span>
        <select className="bg-ink border border-edge rounded text-xs ml-1 px-1 outline-none cursor-pointer text-slate-200"
                value={status} onChange={(e) => { setStatus(e.target.value); clearChecks() }}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="ml-3 text-xs text-muted">Group by:</span>
        <select className="bg-ink border border-edge rounded text-xs ml-1 px-1 outline-none cursor-pointer text-slate-200"
                value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
          {Object.keys(GROUP_AXES).map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <span className="ml-auto flex gap-1 items-center">
          <button className="btn" disabled={checkedIds.length === 0}
                  title={checkedIds.length === 0 ? 'select device(s) to group' : ''}
                  onClick={() => setShowGroup(true)}>
            Group{checkedIds.length ? ` (${checkedIds.length})` : ''}
          </button>
          <span className="relative">
            <button className="btn" onClick={() => setConnectMenu((m) => !m)}>Connect Device ▾</button>
            {connectMenu && (
              <div className="absolute right-0 mt-1 card p-1 z-50 w-52 text-sm" onMouseLeave={() => setConnectMenu(false)}>
                <button className="block w-full text-left px-2 py-1 hover:bg-edge/40 rounded"
                        onClick={() => { setConnectMenu(false); setShowConnect(true) }}>Connect a new device</button>
                <button className="block w-full text-left px-2 py-1 hover:bg-edge/40 rounded"
                        onClick={() => { setConnectMenu(false); setShowPreauth(true) }}>Preauthorize a device</button>
              </div>
            )}
          </span>
          <button className="btn-ghost" onClick={reload}>Refresh</button>
        </span>
      </div>
      {error && (
        <div className="bg-red-500/15 border-b border-red-500/40 text-red-300 text-xs px-3 py-2">
          ⚠ Can't reach Mender — the fleet can't be read (devices are NOT deleted; connectivity/auth error). {error}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-sidebar/60">
            <tr>
              <th className="th w-6"></th>
              <th className="th">Device</th><th className="th">Fleet</th><th className="th">Group</th>
              <th className="th">Base runtime</th><th className="th">App</th>
              <th className="th">Health</th><th className="th">SM</th><th className="th">UCM</th>
              <th className="th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && !data && <tr><td className="cell text-muted" colSpan={10}>loading…</td></tr>}
            {error && <tr><td className="cell text-red-300" colSpan={10}>fleet unavailable — see banner</td></tr>}
            {!loading && !error && devices.length === 0 && <tr><td className="cell text-muted" colSpan={10}>no devices ({status})</td></tr>}
            {!sections && renderRows(devices)}
            {sections && sections.map((s) => (
              <React.Fragment key={s.key}>
                <tr className="bg-edge/30"><td className="cell text-xs font-semibold text-slate-200" colSpan={10}>
                  {groupBy}: {s.key} <span className="text-muted font-normal">· {s.rows.length}</span></td></tr>
                {renderRows(s.rows)}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {showConnect && <CreateTargetModal onClose={() => setShowConnect(false)} onCreated={() => { setShowConnect(false); reload() }} />}
      {showPreauth && <PreauthorizeDialog onClose={() => setShowPreauth(false)} onDone={() => { setShowPreauth(false); reload() }} />}
      {showGroup && <GroupDialog ids={checkedIds} groups={groups} onClose={() => setShowGroup(false)}
                                 onDone={() => { setShowGroup(false); clearChecks(); reload() }} />}
      {selected && <Timeline device={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
