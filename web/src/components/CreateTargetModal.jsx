import React, { useState, useEffect } from 'react'
import { api } from '../api'

// Shared device-onboarding modal (SSH-probe). Used by the Fleet tab
// "Connect a new device" and the Deployment board + icon.
export function CreateTargetModal({ onClose, onCreated }) {
  const [host, setHost] = useState('')          // Host IP to probe
  const [controllerId, setControllerId] = useState('')  // = UUID (mender identity we set on the device)
  const [probedHost, setProbedHost] = useState('')
  const [name, setName] = useState('')
  const [type, setType] = useState('')
  const [desc, setDesc] = useState('')
  const [verifyKey, setVerifyKey] = useState('')   // optional per-device SWP verify PEM
  const [types, setTypes] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  useEffect(() => { api.deviceTypes().then((d) => {
    setTypes(d.types || []); setType((d.types || [])[0] || '')
  }).catch(() => {}) }, [])

  const reload = async () => {
    if (!host.trim()) { setErr('enter a Host IP first'); return }
    setBusy(true); setErr(null)
    try {
      const r = await api.probe(host.trim())
      setControllerId(r.controller_id || '')   // UUID identity (consistent w/ preauth)
      setName(r.hostname || '')
      setProbedHost(host.trim())
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const save = async () => {
    if (!controllerId.trim()) { setErr('Controller ID is required — reload from a Host IP'); return }
    const pemTrim = verifyKey.trim()
    if (pemTrim && !(pemTrim.startsWith('-----BEGIN PUBLIC KEY-----') &&
                     pemTrim.endsWith('-----END PUBLIC KEY-----'))) {
      setErr('SWP verify key must be a PEM public key (-----BEGIN PUBLIC KEY----- … END)'); return
    }
    setBusy(true); setErr(null)
    try {
      // enrol = accept the pending Mender auth-set by MAC + set the operator name
      // + the device_type (Type). Description is an optional tag.
      // set the device's mender identity to our UUID so accept-by-UUID matches
      if (probedHost) await api.setIdentity(probedHost, controllerId.trim())
      const r = await api.connect(controllerId.trim(), type || undefined, undefined, name || undefined)
      // Optional: pin a PER-DEVICE SWP verify key (opt-in override of the fleet
      // key). Empty → the rig uses the fleet key. Applied at next provision.
      const pem = verifyKey.trim()
      if (pem && r && r.device_id) await api.setVerifyKey(r.device_id, pem)
      onCreated()
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[30rem] p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center mb-4">
          <h3 className="font-medium text-center flex-1" style={{ color: '#4A90E2' }}>Connect a new device</h3>
          <button className="text-slate-400 hover:text-slate-200 -mt-3 -mr-1" onClick={onClose}>✕</button>
        </div>

        {/* Host IP + reload (probe) */}
        <div className="flex items-center gap-2 mb-4">
          <label className="w-28 text-right text-xs text-muted">Host IP</label>
          <input className="input flex-1 text-sm" placeholder="10.0.0.22" value={host}
                 onChange={(e) => setHost(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && reload()} />
          <button className="btn-ghost text-xs" disabled={busy} onClick={reload}>
            {busy ? '…' : '⟳ reload'}
          </button>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs">Controller ID <span className="text-red-400">*</span></label>
            <input className="input flex-1 text-sm font-mono" placeholder="device-00FF33445566"
                   value={controllerId} onChange={(e) => setControllerId(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs text-muted">Name</label>
            <input className="input flex-1 text-sm" placeholder="iMX8-1"
                   value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <label className="w-28 text-right text-xs text-muted">Type</label>
            <select className="input flex-1 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
              {types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex items-start gap-2">
            <label className="w-28 text-right text-xs text-muted pt-1">Description</label>
            <textarea className="input flex-1 text-sm h-16" placeholder="My iMX8 device #1."
                      value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="flex items-start gap-2">
            <label className="w-28 text-right text-xs text-muted pt-1">
              SWP Verify Key
              <span className="block text-[10px] text-slate-500">optional</span>
            </label>
            <div className="flex-1">
              <textarea className="input w-full text-sm h-16 font-mono text-[11px]"
                        placeholder="-----BEGIN PUBLIC KEY-----&#10;… (paste this rig's SWP verify PEM, or leave blank to use the fleet key)"
                        value={verifyKey} onChange={(e) => setVerifyKey(e.target.value)} />
              <div className="flex items-center gap-2 mt-1">
                <label className="btn-ghost text-[11px] cursor-pointer">
                  📎 upload .pem
                  <input type="file" accept=".pem,.pub,.key,.crt" className="hidden"
                         onChange={(e) => {
                           const f = e.target.files && e.target.files[0]
                           if (!f) return
                           const rd = new FileReader()
                           rd.onload = () => setVerifyKey(String(rd.result || '').trim())
                           rd.readAsText(f)
                         }} />
                </label>
                {verifyKey.trim() &&
                  <button className="text-[11px] text-slate-400 hover:text-slate-200"
                          onClick={() => setVerifyKey('')}>clear</button>}
                <span className="text-[10px] text-slate-500">
                  pins a per-rig verify key (overrides the fleet key); applied at provision
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="text-[11px] text-red-400 mt-4">* Mandatory Field</div>
        {err && <div className="text-xs text-red-400 mt-1">{err}</div>}
        <div className="flex justify-center gap-6 mt-4 text-slate-300">
          <button className="hover:text-white" disabled={busy} onClick={save}>💾 Save</button>
          <button className="hover:text-white" onClick={onClose}>✕ Cancel</button>
        </div>
      </div>
    </div>
  )
}
