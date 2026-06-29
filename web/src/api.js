// Thin fetch wrapper over the GS backend (/api). All calls return parsed JSON or
// throw with the backend's detail message (so the UI can surface 502s from Mender).
async function call(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  const text = await res.text()
  const body = text ? JSON.parse(text) : null
  if (!res.ok) {
    const msg = body?.detail || `${res.status} ${res.statusText}`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return body
}

export const api = {
  config: () => call('/config'),
  health: () => call('/health'),
  devices: (group, status) => {
    const q = new URLSearchParams()
    if (group) q.set('group', group)
    if (status) q.set('status', status)
    const s = q.toString()
    return call(`/devices${s ? `?${s}` : ''}`)
  },
  deployments: () => call('/deployments'),
  deployment: (id) => call(`/deployments/${id}`),
  artifacts: () => call('/deployments/artifacts/list'),
  createDeployment: (artifact_name, group, name) =>
    call('/deployments', { method: 'POST', body: JSON.stringify({ artifact_name, group, name }) }),
  runtimePlane: () => call('/planes/runtime'),
  appsPlane: () => call('/planes/apps'),
  rolesPlane: () => call('/planes/roles'),
  publishApp: (fleet, app, version, deploy) =>
    call('/planes/apps/publish', {
      method: 'POST',
      body: JSON.stringify({ fleet, app, version, deploy }),
    }),
  // rollout — both planes for one deployment (Mender transport + UCM/SM ECU)
  rollout: (depId) => call(`/deployments/${depId}/rollout`),
  abort: (depId) => call(`/deployments/${depId}/abort`, { method: 'POST' }),
  // ── Phased rollouts (P6): split a group into N sequential sub-groups ───────
  createRollout: (body) =>
    call('/deployments/rollouts', { method: 'POST', body: JSON.stringify(body) }),
  advanceRollout: (artifact_name, name, devices) =>
    call('/deployments/rollouts/advance', { method: 'POST', body: JSON.stringify({ artifact_name, name, devices }) }),
  // per-device ECU lifecycle
  ucmProgress: (deviceId) => call(`/ucm/${deviceId}/progress`),

  // ── Connect Device (the GS funnel) ────────────────────────────────────────
  pending: () => call('/devices/pending'),
  connect: (mac, fleet, group, name) =>
    call('/devices/connect', { method: 'POST', body: JSON.stringify({ mac, fleet, group, name }) }),
  decommission: (id) => call(`/devices/${id}`, { method: 'DELETE' }),
  // pin guards a device from deletion (unpin before delete)
  pinDevice: (id, pinned) =>
    call(`/devices/${id}/pin`, { method: 'POST', body: JSON.stringify({ pinned }) }),
  // ── Create New Target (enrolment) — SSH-probe a host + the Type options ───
  probe: (host) => call(`/devices/probe?host=${encodeURIComponent(host)}`),
  deviceTypes: () => call('/devices/types'),
  // ── Fleet panel (P3): groups + per-device merged timeline ─────────────────
  groups: () => call('/devices/groups/list'),
  preauthorize: (controller_id, pubkey, name, fleet, description) =>
    call('/devices/preauthorize', { method: 'POST', body: JSON.stringify({ controller_id, pubkey, name, fleet, description }) }),
  ourPubkey: () => call('/devices/our-pubkey'),
  setIp: (id, ip, kind) =>
    call(`/devices/${id}/ip`, { method: 'POST', body: JSON.stringify({ ip, kind }) }),
  addToVpn: (id, ip) =>
    call(`/devices/${id}/vpn`, { method: 'POST', body: JSON.stringify({ ip, kind: 'remote' }) }),
  assignGroup: (id, group) =>
    call(`/devices/${id}/group`, { method: 'POST', body: JSON.stringify({ group }) }),
  removeGroup: (id, group) =>
    call(`/devices/${id}/group?group=${encodeURIComponent(group)}`, { method: 'DELETE' }),
  deviceTimeline: (id) => call(`/devices/${id}/timeline`),

  // ── BASE deployment (colony) ──────────────────────────────────────────────
  deployBase: (rig, kind = 'orchestrate', ip, device_id) =>
    call('/deployments/base', { method: 'POST', body: JSON.stringify({ rig, kind, ip, device_id }) }),

  // ── Releases plane management (ACT: pin/delete) ───────────────────────────
  pinApp: (fleet, app, version, pinned) =>
    call('/planes/apps/pin', { method: 'POST', body: JSON.stringify({ fleet, app, version, pinned }) }),
  deleteApp: (fleet, app, version) =>
    call('/planes/apps', { method: 'DELETE', body: JSON.stringify({ fleet, app, version }) }),
  pinRuntime: (key, pinned) =>
    call('/planes/runtime/pin', { method: 'POST', body: JSON.stringify({ key, pinned }) }),
  deleteRuntime: (key) =>
    call('/planes/runtime', { method: 'DELETE', body: JSON.stringify({ key }) }),
}
