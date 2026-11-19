#!/usr/bin/env node
// MOCK gs-api for the provisioning UI (spec ui-provisioning §0) — test/dev only.
// It is NOT part of the production bundle: nothing under src/ imports it; it is a
// separate Node process (no dependencies) that serves the contract's shapes.
//
//   node mock/server.mjs [--port 8099] [--dist dist]
//     --dist DIR   also serve the built SPA from DIR (SPA fallback to index.html)
//   dev UI against it:  GS_API_TARGET=http://localhost:8099 npm run dev
//   env MOCK_RUN_SECONDS (default 20): how long a run takes.
//
// Behaviour (pick it with the Add-device fields):
//   user "wrong"        -> 422 auth          address "10.0.0.250" or "*unreach*" -> 422 unreachable
//   address "10.0.0.66" -> 422 host-key-changed   user "nosudo" -> 422 no-sudo
//   name containing "fail" -> the run FAILS at "network : join the tailnet"
//   name starting "sk"     -> an opkg rig (no Mender: named skips, accepted "not-a-mender-rig")
//   a second POST for a rig with a pending/running run -> 409 {detail, run_id}
//   POST /__mock/reset restores the seed state.
// App rollouts (spec hil-app-ota G2/G3), env MOCK_ROLLOUT_SECONDS (default 12) = one rollout:
//   wc-0.1.0-*  -> success      wc-0.1.1-* -> failure, rolled back to wc-0.1.0-trixie-arm64
//   an unsigned artifact -> failure at download (signature)   wc-0.2.0-bookworm-arm64 -> 422 on a trixie rig
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'

const args = process.argv.slice(2)
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d }
const PORT = Number(opt('--port', process.env.MOCK_PORT || 8099))
const DIST = opt('--dist', null)
const RUN_MS = Number(process.env.MOCK_RUN_SECONDS || 20) * 1000
const PENDING_MS = 1500
const ROLLOUT_MS = Number(process.env.MOCK_ROLLOUT_SECONDS || 12) * 1000

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const USER_RE = /^[a-z_][a-z0-9_-]{0,31}$/
const PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockMockMockMockMockMockMockMockMockMock0 gs-fleet@mock'

const PROVISION = [
  ['Gathering Facts', 'ok'],
  ['base : hold kernel and bootloader packages', 'ok'],
  ['base : kernel version is the pinned one', 'ok'],
  ['base : extlinux.conf hash unchanged', 'ok', 'deb'],
  ['network : install tailscale', 'changed'],
  ['network : join the tailnet', 'changed'],
  ['pki : create the device key and CSR', 'changed'],
  ['pki : sign the device certificate (CN = name)', 'changed'],
  ['mender : install the mender client', 'ok', 'deb'],
  ['mender : set identity device_id = name', 'changed', 'deb'],
  ['runtime : install theia-runtime', 'changed'],
  ['com : enable the mTLS listener', 'changed'],
  ['com : restart the supervisor', 'changed'],
  ['verify : com answers ListMachines over mTLS', 'ok'],
]
const DEPLOY = [
  ['Gathering Facts', 'ok'],
  ['runtime : fetch theia-runtime from the runtime plane', 'changed'],
  ['runtime : verify sha256', 'ok'],
  ['runtime : install theia-runtime', 'changed'],
  ['services : install theia-services', 'changed'],
  ['com : restart the supervisor', 'changed'],
  ['verify : com answers ListMachines over mTLS', 'ok'],
]
const FAIL_AT = 'network : join the tailnet'
const FAIL_MSG = 'tailscale up: timed out after 60 s waiting for the coordination server '
  + '(no route to the login server). Check that the board has internet access, then add it again.'

let state
let seq = 0
const newId = () => `run-${String(++seq).padStart(4, '0')}-${Math.random().toString(16).slice(2, 8)}`
const isOpkg = (name) => name.startsWith('sk')

function makeRun(rig, kind, opts = {}) {
  const opkg = isOpkg(rig)
  const tasks = (kind === 'provision' ? PROVISION : DEPLOY).map(([name, result, only]) => ({
    name, result: only === 'deb' && opkg ? 'skipped' : result }))
  const fail = rig.includes('fail') && kind === 'provision'
  const cut = fail ? tasks.findIndex((t) => t.name === FAIL_AT) : -1
  return { run_id: newId(), rig, kind, created: opts.created ?? Date.now(), tasks, cut,
           version: opts.version || null }
}

function view(run) {
  const now = Date.now()
  const t = now - run.created
  const n = run.tasks.length
  const per = (RUN_MS - PENDING_MS) / n
  let done = t < PENDING_MS ? 0 : Math.min(n, Math.floor((t - PENDING_MS) / per))
  let status = t < PENDING_MS ? 'pending' : 'running'
  let tasks = run.tasks.slice(0, done)
  let error = null
  if (run.cut >= 0 && done > run.cut) {
    tasks = [...run.tasks.slice(0, run.cut), { name: run.tasks[run.cut].name, result: 'failed' }]
    status = 'failure'
    error = { task: run.tasks[run.cut].name, message: FAIL_MSG }
    done = run.cut + 1
  } else if (done >= n) status = 'success'
  const final = status === 'success' || status === 'failure'
  const opkg = isOpkg(run.rig)
  const out = {
    run_id: run.run_id, rig: run.rig, kind: run.kind, status,
    started: new Date(run.created).toISOString(),
    finished: final ? new Date(run.created + PENDING_MS + done * per).toISOString() : null,
    step: { index: Math.min(done + 1, n), total_estimate: n,
            name: final ? null : (status === 'pending' ? 'preflight ok, waiting for colony' : run.tasks[done]?.name) },
    tasks, error,
    vpn_ip: null, pki: null, rig_class: opkg ? 'opkg' : 'deb', skipped_by_design: [],
  }
  if (status === 'success') {
    const r = state.rigs[run.rig]
    out.vpn_ip = r?.vpn_ip || '100.64.0.99'
    if (run.kind === 'provision') {
      out.pki = { serial: '4f3a9c21e07d', not_after: new Date(run.created + 365 * 86400e3).toISOString() }
      out.accepted = opkg ? 'not-a-mender-rig' : true
      out.skipped_by_design = opkg ? run.tasks.filter((x) => x.result === 'skipped').map((x) => x.name) : []
    }
  }
  return out
}

function seed() {
  seq = 0
  const past = Date.now() - 3600e3
  state = { rigs: {}, runs: [] }
  const add = (r) => { state.rigs[r.name] = { ...r, _applied: true } }
  add({ name: 'ai64-proto', mender_id: 'mock-94b200f0', auth_status: 'accepted', role: 'master',
        address: '10.0.0.144', vpn_ip: '100.64.0.10', base_version: '0.3.1-trixie-arm64',
        artifact: 'base-0.3.0-trixie-arm64', com: { state: 'mtls', detail: 'ListMachines over mTLS: 6 machines' } })
  add({ name: 'sk-1', mender_id: null, auth_status: null, role: 'zonal',
        address: '10.0.0.31', vpn_ip: '100.64.0.11', base_version: '0.3.0-trixie-arm64',
        artifact: null, com: { state: 'plaintext', detail: 'TLS refused; the rig serves plaintext gRPC' } })
  add({ name: 'ai64-bench', mender_id: 'mock-71de9864', auth_status: 'pending', role: 'zonal',
        address: '10.0.0.145', vpn_ip: null, base_version: null, artifact: null,
        com: { state: 'down', detail: 'connection refused on :7700' } })
  add({ name: 'ai64-lab', mender_id: 'mock-5531c375', auth_status: 'accepted', role: 'master',
        address: '10.0.0.146', vpn_ip: '100.64.0.12', base_version: '0.2.1-bookworm-arm64', artifact: null,
        com: { state: 'unknown', detail: 'not probed yet' } })
  state.artifacts = [
    artifact('wc-0.2.0-bookworm-arm64', true, past + 5000),
    artifact('wc-0.1.2-trixie-arm64', false, past + 4000),
    artifact('wc-0.1.1-trixie-arm64', true, past + 3000),
    artifact('wc-0.1.0-trixie-arm64', true, past + 2000),
    artifact('base-0.1.0-trixie-arm64', true, past),
  ]
  state.deps = []
  state.runs.push(makeRun('ai64-proto', 'provision', { created: past }))
  state.runs.push(makeRun('sk-1', 'provision', { created: past + 1000 }))
  state.runs.push(makeRun('sk-1', 'deploy', { created: past + 2000, version: '0.3.0' }))
}

// ── app rollouts: the /api/artifacts summaries + time-driven Mender deployments ──
const ABI_RE = /^(?:[a-z][a-z0-9]*-)?(?:arm64|amd64|armv7|armhf|aarch64|x86_64|riscv64)$/
function artifact(name, signed, created) {
  const m = name.match(/^(.+?)-(\d+\.\d+(?:\.\d+)?)(?:-(.+))?$/) || []
  const abi = m[3] && ABI_RE.test(m[3]) ? m[3] : null
  return { id: `art-${name}`, name, app: m[1] || null, version: m[2] || null, abi, kind: 'theia-swp',
    types: ['theia-swp'], device_types: ['theia-rig'], signed, size: 7680 + name.length * 64,
    created: new Date(created).toISOString(), description: 'mock', requires_runtime: null,
    requirement: abi ? { runtime: null, version: null, abi, source: 'name' } : null }
}
function runtimeRefusal(art, dev) {
  const r = art.requirement
  if (!r) return null
  if (!dev.base_version) return `${dev.name} has no base runtime recorded (base_version is empty): ${art.name} needs ${r.abi}`
  const abi = dev.base_version.split('-').slice(1).join('-')
  if (abi !== r.abi) return `${dev.name} runs base ${dev.base_version}, ${art.name} needs the ${r.abi} runtime: abi mismatch; deploy a ${r.abi} runtime first (Fleet, Deploy runtime)`
  return null
}
const ROLLBACK_REASON = [
  'error: theia-swp: COMMIT FAILED — process wc_mavlink restarted 4 times within 10s on releases/wc-0.1.1-trixie-arm64 — Mender will roll back',
  'error: Process returned non-zero exit status: ArtifactCommit: Process exited with status 1',
  'theia-swp: ROLLBACK current → releases/wc-0.1.0-trixie-arm64',
]
const UNSIGNED_REASON = [
  'error: Signature verification Error: expecting signed artifact, but no signature file found',
  'error: Operation canceled: GET https://s3.mock/mender/x?X-Amz-Credential=***&X-Amz-Signature=*** HTTP request cancelled',
]
// one device of a deployment at time t (ms since created)
function depDevice(dep, d, now = Date.now()) {
  const t = now - dep.created
  const art = state.artifacts.find((a) => a.name === dep.artifact_name) || {}
  const fin = new Date(dep.created + ROLLOUT_MS).toISOString()
  const row = { id: d.id, name: d.name, status: 'pending', outcome: 'in_progress', rolled_back_to: null, reason: [],
                started: t >= PENDING_MS ? new Date(dep.created + PENDING_MS).toISOString() : null, finished: null }
  if (dep.aborted && dep.aborted <= now) return { ...row, status: 'aborted', outcome: 'aborted', finished: new Date(dep.aborted).toISOString() }
  if (t < PENDING_MS) return row
  if (!art.signed) {
    if (t < PENDING_MS + 1500) return { ...row, status: 'downloading' }
    return { ...row, status: 'failure', outcome: 'failure', reason: UNSIGNED_REASON, finished: fin }
  }
  if (t < ROLLOUT_MS * 0.35) return { ...row, status: 'downloading' }
  if (t < ROLLOUT_MS) return { ...row, status: 'installing' }
  if (/^wc-0\.1\.1-/.test(dep.artifact_name))
    return { ...row, status: 'failure', outcome: 'rolled_back', rolled_back_to: 'wc-0.1.0-trixie-arm64', reason: ROLLBACK_REASON, finished: fin }
  return { ...row, status: 'success', outcome: 'success', finished: fin }
}
function depView(dep) {
  const devs = dep.devices.map((d) => depDevice(dep, d))
  const outs = devs.map((d) => d.outcome)
  const done = !outs.includes('in_progress')
  const overall = !done ? 'in_progress' : outs.every((o) => o === 'success') ? 'success'
    : outs.includes('failure') ? 'failure' : outs.includes('rolled_back') ? 'rolled_back' : outs[0]
  const stats = {}
  for (const d of devs) stats[d.status] = (stats[d.status] || 0) + 1
  const t = Date.now() - dep.created
  return { id: dep.id, name: dep.name, artifact_name: dep.artifact_name,
    status: done ? (dep.aborted ? 'aborted' : 'finished') : t < PENDING_MS ? 'pending' : 'inprogress',
    created: new Date(dep.created).toISOString(), finished: done ? devs[0]?.finished : null,
    statistics: stats, outcome: overall, final: done, devices: devs }
}
const UCM = { pending: [0, 'IDLE'], downloading: [1, 'DOWNLOADED'], installing: [4, 'INSTALLING'], success: [7, 'ACTIVE'] }

seed()

const runsOf = (rig) => state.runs.filter((r) => r.rig === rig).sort((a, b) => b.created - a.created)
const activeOf = (rig) => runsOf(rig).find((r) => ['pending', 'running'].includes(view(r).status))

function fleetRow(r) {
  const runs = runsOf(r.name)
  const last = runs[0] ? view(runs[0]) : null
  const opkg = isOpkg(r.name)
  // a rig being provisioned: its outcome lands in the row once the run ends
  if (last && last.kind === 'provision' && last.status === 'success' && !r._applied) {
    Object.assign(r, { vpn_ip: last.vpn_ip, base_version: r.base_version || '0.3.0',
      com: { state: opkg ? 'plaintext' : 'mtls', detail: opkg ? 'the rig serves plaintext gRPC' : 'ListMachines over mTLS' } })
    if (!opkg) Object.assign(r, { mender_id: `mock-${r.name}`, auth_status: 'accepted' })
    r._applied = true
  }
  if (last && last.kind === 'deploy' && last.status === 'success' && r._deployed !== runs[0].run_id) {
    r.base_version = runs[0].version || r.base_version
    r._deployed = runs[0].run_id
  }
  return {
    name: r.name, source: r.mender_id ? 'mender+colony' : 'colony', mender_id: r.mender_id,
    auth_status: r.auth_status, rig_class: opkg ? 'opkg' : 'deb', role: r.role, address: r.address,
    vpn_ip: r.vpn_ip, base_version: r.base_version,
    last_run: last ? { run_id: last.run_id, kind: last.kind, status: last.status, finished: last.finished } : null,
    com: r.com, artifact: r.artifact,
  }
}

function menderDevices() {
  return Object.values(state.rigs).filter((r) => r.mender_id).map((r) => ({
    id: r.mender_id, name: r.name, fleet: 'theia-rig', group: r.name === 'ai64-proto' ? 'lab' : null,
    base_version: r.base_version, artifact: r.artifact, health: r.name === 'ai64-proto' ? 'ok' : null,
    sm_state: r.name === 'ai64-proto' ? 'Running' : null, ucm_version: null, auth_status: r.auth_status,
    attributes: { os: 'Debian GNU/Linux 13 (trixie)', kernel: 'Linux 6.12 aarch64' }, connected: 'mender+com',
  }))
}

// the gs-api 422 body: {detail, reason[, pubkey]} (pubkey only for auth)
function preflight(address, user) {
  if (address === '10.0.0.250' || address.includes('unreach'))
    return { reason: 'unreachable', detail: `${address} is unreachable over ssh: connect timed out after 8 s` }
  if (address === '10.0.0.66')
    return { reason: 'host-key-changed', detail: `the ssh host key of ${address} CHANGED since colony first met it` }
  if (user === 'wrong')
    return { reason: 'auth', pubkey: PUBKEY,
             detail: `ssh as ${user}@${address} was refused (Permission denied): add the GS key to ${user}@${address}:~/.ssh/authorized_keys` }
  if (user === 'nosudo')
    return { reason: 'no-sudo', detail: `${user}@${address} cannot run sudo without a password` }
  return null
}

function send(res, code, body) {
  const s = JSON.stringify(body)
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(s)
}

async function readJson(req) {
  let b = ''
  for await (const c of req) b += c
  try { return b ? JSON.parse(b) : {} } catch { return null }
}

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' }
function serveStatic(req, res, pathname) {
  if (!DIST) return send(res, 404, { detail: 'not found' })
  const root = path.resolve(DIST)
  let f = path.resolve(root, '.' + decodeURIComponent(pathname))
  if (!f.startsWith(root) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(root, 'index.html')
  res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' })
  fs.createReadStream(f).pipe(res)
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://mock')
  const p = url.pathname
  const m = req.method
  if (p === '/__mock/reset' && m === 'POST') { seed(); return send(res, 200, { ok: true }) }
  if (!p.startsWith('/api/')) return serveStatic(req, res, p)
  let mm
  // ── the provisioning contract ──
  if (p === '/api/fleet' && m === 'GET') return send(res, 200, Object.values(state.rigs).map(fleetRow))
  if (p === '/api/rigs' && m === 'POST') {
    const b = await readJson(req)
    if (!b) return send(res, 422, { detail: 'body: not JSON' })
    const name = b.name || '', address = (b.address || '').trim(), user = b.user || 'debian'
    const role = b.role || 'master'
    if (!NAME_RE.test(name)) return send(res, 422, { detail: 'name: ^[a-z0-9][a-z0-9-]{0,62}$', reason: 'invalid' })
    if (!address) return send(res, 422, { detail: 'address: an IPv4 address or a host name', reason: 'invalid' })
    if (!USER_RE.test(user)) return send(res, 422, { detail: 'user: not a valid ssh user name', reason: 'invalid' })
    if (!['master', 'zonal'].includes(role)) return send(res, 422, { detail: 'role: master or zonal', reason: 'invalid' })
    const act = activeOf(name)
    if (act) return send(res, 409, { detail: `a run for ${name} is already in progress`, run_id: act.run_id })
    const pf = preflight(address, user)
    if (pf) {
      await new Promise((r) => setTimeout(r, 400))
      return send(res, 422, pf)
    }
    if (!state.rigs[name]) {
      state.rigs[name] = { name, mender_id: null, auth_status: null, role, address, vpn_ip: null,
        base_version: null, artifact: null, com: { state: 'unknown', detail: 'provisioning' } }
    } else Object.assign(state.rigs[name], { role, address, _applied: false })
    const run = makeRun(name, 'provision')
    state.runs.push(run)
    return send(res, 202, { run_id: run.run_id, rig: name, kind: 'provision' })
  }
  if ((mm = p.match(/^\/api\/rigs\/([^/]+)\/deploy$/)) && m === 'POST') {
    const name = decodeURIComponent(mm[1])
    const b = await readJson(req)
    if (!state.rigs[name]) return send(res, 404, { detail: `no rig ${name}` })
    if (!b || !b.runtime_version) return send(res, 422, { detail: 'runtime_version: required' })
    const act = activeOf(name)
    if (act) return send(res, 409, { detail: `a run for ${name} is already in progress`, run_id: act.run_id })
    const run = makeRun(name, 'deploy', { version: b.runtime_version })
    state.runs.push(run)
    return send(res, 202, { run_id: run.run_id })
  }
  // ── app rollouts ──
  if (p === '/api/artifacts' && m === 'GET') return send(res, 200, { artifacts: state.artifacts })
  if (p === '/api/deployments/app' && m === 'POST') {
    const b = await readJson(req)
    if (!b) return send(res, 422, { detail: 'body: not JSON' })
    const art = state.artifacts.find((a) => a.name === b.artifact_name)
    if (!art) return send(res, 404, { detail: `no artifact '${b.artifact_name}' on the Mender server` })
    const want = [...new Set((b.devices || []).filter(Boolean))]
    if (!want.length) return send(res, 422, { detail: 'devices: pick at least one target device' })
    const rigs = Object.values(state.rigs).filter((r) => r.mender_id)
    const targets = [], refused = []
    for (const w of want) {
      const r = rigs.find((x) => x.mender_id === w || x.name === w)
      if (!r) return send(res, 422, { detail: `device '${w}': not a Mender device in the fleet` })
      if (r.auth_status !== 'accepted') return send(res, 422, { detail: `device ${r.name}: not accepted in Mender (${r.auth_status})` })
      const why = runtimeRefusal(art, r)
      if (why) refused.push(why)
      targets.push(r)
    }
    if (refused.length) return send(res, 422, { detail: 'runtime mismatch: ' + refused.join('; ') })
    const dep = { id: `dep-${String(state.deps.length + 1).padStart(3, '0')}-${Math.random().toString(16).slice(2, 10)}`,
      name: b.name || `gs-ui:${art.name}:${targets.map((r) => r.name).join('+')}`, artifact_name: art.name,
      created: Date.now(), devices: targets.map((r) => ({ id: r.mender_id, name: r.name })) }
    state.deps.push(dep)
    return send(res, 201, { id: dep.id, name: dep.name, artifact_name: art.name, kind: art.kind, signed: art.signed,
      devices: dep.devices })
  }
  if ((mm = p.match(/^\/api\/deployments\/([^/]+)\/(outcome|devices|rollout|abort)$/))) {
    const dep = state.deps.find((d) => d.id === decodeURIComponent(mm[1]))
    if (!dep) return send(res, 404, { detail: `mock: no deployment ${mm[1]}` })
    const v = depView(dep)
    if (mm[2] === 'abort' && m === 'POST') { if (!v.final) dep.aborted = Date.now(); return send(res, 200, { id: dep.id, aborted: true }) }
    if (m !== 'GET') return send(res, 405, { detail: 'mock: method' })
    if (mm[2] === 'outcome') return send(res, 200, v)
    if (mm[2] === 'devices') return send(res, 200, { devices: v.devices.map((d) => ({ id: d.id, status: d.status,
      rolled_back_to: d.rolled_back_to, reason: d.reason })) })
    return send(res, 200, { transport: { deployment: { id: v.id, name: v.name, artifact_name: v.artifact_name, status: v.status },
      statistics: { status: v.statistics } },
      ecu: v.devices.map((d) => d.outcome === 'rolled_back'
        ? { device: d.id, target: '100.64.0.10:7700', progress: { ok: true, state: 0, state_name: 'ROLLBACK', sm_ok: true, sm_state_name: 'RUNNING', detail: `rolled back to ${d.rolled_back_to}` } }
        : { device: d.id, target: '100.64.0.10:7700', progress: { ok: true, state: (UCM[d.status] || [0])[0],
            state_name: (UCM[d.status] || [0, 'IDLE'])[1], sm_ok: true, sm_state_name: d.status === 'installing' ? 'UPDATE' : 'RUNNING' } }) })
  }
  if ((mm = p.match(/^\/api\/runs\/([^/]+)$/)) && m === 'GET') {
    const run = state.runs.find((r) => r.run_id === decodeURIComponent(mm[1]))
    return run ? send(res, 200, view(run)) : send(res, 404, { detail: 'no such run' })
  }
  if (p === '/api/runs' && m === 'GET') {
    const rig = url.searchParams.get('rig')
    return send(res, 200, (rig ? runsOf(rig) : [...state.runs].sort((a, b) => b.created - a.created)).map(view))
  }
  if (p === '/api/devices/our-pubkey' && m === 'GET') return send(res, 200, { pubkey: PUBKEY })
  // ── just enough of the rest for the views to render ──
  if (m === 'GET') {
    if (p === '/api/config') return send(res, 200, { mender_server: 'mock', mender_flavor: 'oss', token_set: true, auth_required: true })
    if (p === '/api/health') return send(res, 200, { ok: true })
    if (p === '/api/devices') {
      const st = url.searchParams.get('status')
      const devs = menderDevices().filter((d) => !st || st === 'any' || d.auth_status === st)
      return send(res, 200, { devices: devs, count: devs.length })
    }
    if (p === '/api/devices/groups/list') return send(res, 200, { groups: [{ name: 'lab' }] })
    if (p === '/api/devices/types') return send(res, 200, { types: ['theia-rig'] })
    if (p === '/api/devices/pending') return send(res, 200, { devices: [] })
    if (/^\/api\/devices\/[^/]+\/timeline$/.test(p)) return send(res, 200, { events: [] })
    if (p === '/api/deployments') return send(res, 200, { deployments: [...state.deps].reverse().map((d) => {
      const v = depView(d)
      return { id: v.id, name: v.name, artifact_name: v.artifact_name, status: v.status, created: v.created,
               finished: v.finished, statistics: { status: v.statistics }, authority: 'app' }
    }) })
    if (p === '/api/deployments/rollouts') return send(res, 200, { rollouts: [] })
    if (p === '/api/deployments/artifacts/list') return send(res, 200, { artifacts: [] })
    if (p === '/api/planes/runtime') return send(res, 200, { plane: 'runtime', releases: [
      { plane: 'runtime', version: '0.3.0', distro: 'trixie-arm64', key: '0.3.0-trixie-arm64' },
      { plane: 'runtime', version: '0.3.0', distro: 'kirkstone-armv7', key: '0.3.0-kirkstone-armv7' },
      { plane: 'runtime', version: '0.2.1', distro: 'trixie-arm64', key: '0.2.1-trixie-arm64' },
    ] })
    if (p === '/api/planes/apps') return send(res, 200, { tree: {} })
    if (p === '/api/planes/distributions') return send(res, 200, { distributions: [] })
  }
  return send(res, 404, { detail: `mock: no ${m} ${p}` })
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`gs mock api on http://127.0.0.1:${PORT}${DIST ? ` (serving ${DIST})` : ''}; run ${RUN_MS / 1000} s`)
})
