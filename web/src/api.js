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
  devices: (group) => call(`/devices${group ? `?group=${encodeURIComponent(group)}` : ''}`),
  deployments: () => call('/deployments'),
  deployment: (id) => call(`/deployments/${id}`),
  artifacts: () => call('/deployments/artifacts/list'),
  createDeployment: (artifact_name, group, name) =>
    call('/deployments', { method: 'POST', body: JSON.stringify({ artifact_name, group, name }) }),
  runtimePlane: () => call('/planes/runtime'),
  appsPlane: () => call('/planes/apps'),
  publishApp: (fleet, app, version, deploy) =>
    call('/planes/apps/publish', {
      method: 'POST',
      body: JSON.stringify({ fleet, app, version, deploy }),
    }),
}
