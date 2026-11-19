import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { AlreadyRunning, useRuns } from './RunDrawer'

// Add device — the operator's ONE way in (spec ui-provisioning §0 POST /api/rigs).
// The device NAME is the identity (Mender id, certificate CN, tailnet host): no
// MAC, no controller id, no Mender id to type. The GS runs a fast ssh PREFLIGHT
// (422 with a reason) and then provisions asynchronously (202 {run_id}); the Run
// drawer shows the progress.

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const USER_RE = /^[a-z_][a-z0-9_-]{0,31}$/
const IPV4_RE = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/
const HOST_RE = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/
const REASONS = ['unreachable', 'auth', 'host-key-changed', 'no-sudo']

// Read the preflight reason out of a 422. The contract fixes the reason words;
// the body may carry them as {detail: {reason, message, pubkey}}, {reason, ...}
// or a plain detail string ("auth: Permission denied ..."): all are accepted.
// A FastAPI validation 422 (detail = [{loc, msg}]) is not a preflight.
export function preflightOf(err) {
  if (!err || err.status !== 422) return null
  const b = err.body && typeof err.body === 'object' ? err.body : {}
  const d = b.detail
  if (Array.isArray(d)) {
    return { reason: 'invalid', message: d.map((x) => `${(x.loc || []).slice(-1)[0] || ''}: ${x.msg}`).join('; ') }
  }
  // gs-api (ui-provisioning 46e4b8a) answers {detail, reason[, pubkey]} at the top level.
  const obj = d && typeof d === 'object' ? d : b
  let reason = obj.reason || obj.code || obj.error || obj.preflight
  let message = obj.message || obj.msg || (typeof d === 'string' ? d : typeof err.body === 'string' ? err.body : '')
  if (typeof reason === 'string' && reason) {
    if (!REASONS.includes(reason)) reason = 'invalid'   // invalid, no-address, ...: show the detail
  } else {
    const text = String(message || err.message || '')
    const m = text.match(/\b(unreachable|auth|host-key-changed|no-sudo)\b/)
    if (m) reason = m[1]
    else if (/permission denied|publickey|GS key|authorized_keys/i.test(text)) reason = 'auth'
    else if (/host key|known_hosts/i.test(text)) reason = 'host-key-changed'
    else if (/sudo/i.test(text)) reason = 'no-sudo'
    else if (/no route|timed? ?out|unreachable|refused/i.test(text)) reason = 'unreachable'
    else reason = 'invalid'
    message = text
  }
  const keyIn = String(message || '').match(/(ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]+(?: [^\s"']+)?/)
  return { reason, message, pubkey: obj.pubkey || (keyIn && keyIn[0]) || null }
}

function CopyBox({ text, testid }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) }
    catch { /* no clipboard (http): the text is selectable */ }
  }
  return (
    <div className="flex gap-2 items-start">
      <textarea readOnly className="input text-[10px] h-14 font-mono flex-1" value={text} data-testid={testid}
                onFocus={(e) => e.target.select()} />
      <button className="btn-ghost text-xs" onClick={copy}>{done ? 'copied' : 'copy'}</button>
    </div>
  )
}

function PreflightHelp({ pf, user, address }) {
  const [key, setKey] = useState(pf.pubkey || '')
  const [keyErr, setKeyErr] = useState(null)
  useEffect(() => {
    if (pf.reason !== 'auth' || pf.pubkey) return
    api.ourPubkey().then((d) => setKey((d?.pubkey || '').trim())).catch((e) => setKeyErr(e.message))
  }, [pf.reason, pf.pubkey])
  const box = 'rounded border border-danger/50 bg-danger/10 p-3 text-xs space-y-2'
  if (pf.reason === 'auth') {
    return (
      <div className={box} data-testid="preflight-auth">
        <div className="font-semibold text-danger text-sm">The board refused the Ground Station's ssh key</div>
        <div className="text-slate-200">
          {address} answered, but user <span className="font-mono">{user}</span> does not accept the GS key.
          Add this line to <span className="font-mono">~{user}/.ssh/authorized_keys</span> on {address}, then press Add device again:
        </div>
        {keyErr && <div className="text-amber-300">the GS public key could not be read: {keyErr}</div>}
        {!key && !keyErr && <div className="text-muted">loading the GS public key…</div>}
        {key && <CopyBox text={key} testid="gs-pubkey" />}
        {key && (
          <div className="text-muted">
            or, logged in on the board as {user}:
            <CopyBox text={`mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '${key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys`} />
          </div>
        )}
      </div>
    )
  }
  const words = {
    unreachable: ['The board cannot be reached',
      `Nothing answered at ${address} within 8 s (no route, or it timed out). Check the address, that the board is powered on, and that it is on this network or the VPN.`],
    'host-key-changed': ['The board identifies itself differently than before',
      `The ssh host key of ${address} is not the one the Ground Station saw before. Either the board was re-flashed (then its old host key must be removed from the GS known_hosts) or a different machine now has this address. Check the address first.`],
    'no-sudo': ['The user cannot run sudo',
      `User ${user} can log in on ${address} but cannot run sudo without a password. Give ${user} passwordless sudo, or use a user that has it.`],
    'ssh-failed': ['ssh to the board failed', null],
    invalid: ['Not accepted', null],
  }
  const [title, text] = words[pf.reason] || words.invalid
  return (
    <div className={box} data-testid={`preflight-${pf.reason}`}>
      <div className="font-semibold text-danger text-sm">{title}</div>
      {text && <div className="text-slate-200">{text}</div>}
      {pf.message && <div className="text-muted font-mono break-all">{pf.message}</div>}
    </div>
  )
}

export function AddDeviceModal({ onClose, onStarted }) {
  const { started } = useRuns()
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [user, setUser] = useState('debian')
  const [role, setRole] = useState('master')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)          // {kind: 'field'|'preflight'|'conflict'|'other', ...}

  const fieldErr = () => {
    if (!NAME_RE.test(name)) return 'name: lower-case letters, digits and -, starting with a letter or digit (max 63); it becomes the device identity'
    const a = address.trim()
    if (!(IPV4_RE.test(a) || (HOST_RE.test(a) && !/^[\d.]+$/.test(a)))) return 'address: an IPv4 address or a host name'
    if (!USER_RE.test(user)) return 'ssh user: lower-case letters, digits, _ and - (max 32)'
    return null
  }

  const submit = async () => {
    const fe = fieldErr()
    if (fe) { setErr({ kind: 'field', message: fe }); return }
    setBusy(true); setErr(null)
    try {
      const r = await api.addRig(name, address.trim(), user, role)
      started(r.rig || name, r.run_id)
      onStarted && onStarted(r)
      onClose()
    } catch (e) {
      if (e.status === 409) setErr({ kind: 'conflict', e })
      else {
        const pf = preflightOf(e)
        setErr(pf ? { kind: 'preflight', pf, user, address: address.trim() } : { kind: 'other', message: e.message })
      }
    }
    setBusy(false)
  }

  const field = (label, el, hint) => (
    <div className="flex items-start gap-2">
      <label className="w-24 text-right text-xs text-muted pt-2">{label}</label>
      <div className="flex-1">{el}{hint && <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>}</div>
    </div>
  )

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[34rem] p-5" data-testid="add-device" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-4">
          <h3 className="font-medium text-center flex-1" style={{ color: '#4A90E2' }}>Add device</h3>
          <button className="text-slate-400 hover:text-slate-200 -mt-3 -mr-1" onClick={onClose}>✕</button>
        </div>
        <div className="space-y-3">
          {field('Name', <input className="input w-full text-sm font-mono" placeholder="ai64-2" name="name"
                                value={name} onChange={(e) => setName(e.target.value.toLowerCase())} autoFocus />,
                 'the device identity: Mender id, certificate name, VPN host name')}
          {field('Address', <input className="input w-full text-sm font-mono" placeholder="10.0.0.22" name="address"
                                   value={address} onChange={(e) => setAddress(e.target.value)} />,
                 'IPv4 address or host name the Ground Station can ssh to now')}
          {field('SSH user', <input className="input w-full text-sm font-mono" name="user"
                                    value={user} onChange={(e) => setUser(e.target.value)} />)}
          {field('Role', (
            <select className="input w-full text-sm" name="role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="master">master</option>
              <option value="zonal">zonal</option>
            </select>
          ))}
        </div>
        <div className="mt-4 space-y-2">
          {err?.kind === 'field' && <div className="text-xs text-red-400">{err.message}</div>}
          {err?.kind === 'other' && <div className="text-xs text-red-400">{err.message}</div>}
          {err?.kind === 'conflict' && <AlreadyRunning err={err.e} rig={name} onOpened={onClose} />}
          {err?.kind === 'preflight' && <PreflightHelp pf={err.pf} user={err.user} address={err.address} />}
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={busy} onClick={submit}>{busy ? 'checking ssh…' : 'Add device'}</button>
        </div>
        <div className="text-[10px] text-slate-500 mt-2">
          Add device checks ssh (8 s), then provisions the board and accepts it in Mender. Progress opens in the run drawer.
        </div>
      </div>
    </div>
  )
}
