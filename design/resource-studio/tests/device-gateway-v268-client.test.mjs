// Synthetic contract fixtures. Root runs these tests; they do not prove field-device connectivity.
import test from 'node:test';
import assert from 'node:assert/strict';
import {DeviceGatewayClientV268} from '../src/device-gateway-client-v268.mjs';
import {DEVICE_GATEWAY_BASE_V268 as BASE, checkedDiscoveryV268, discoveryIsCurrentV268, deviceFamilyViewV268, isReadActionV268, safeServiceOriginV268, assertSafeGatewayDataV268} from '../src/device-gateway-ui-v268.mjs';
const id = number => '00000000-0000-4000-8000-' + String(number).padStart(12, '0');
const copy = value => JSON.parse(JSON.stringify(value));
class MemoryStorage { constructor() { this.rows = new Map(); } getItem(key) { return this.rows.has(key) ? this.rows.get(key) : null; } setItem(key, value) { this.rows.set(key, String(value)); } removeItem(key) { this.rows.delete(key); } }
function device(kind = 'weather_station') { return {external_id: 'test-weather-1', namespace: 'procurement-test', profile_id: 'weather-basic', kind, name: '合成气象测试设备', revision: 1, actions: ['get_status', 'read_telemetry'], simulation: true, origin_kind: 'simulated_equipment', field_verified: false, physical_device_connected: false}; }
function connection() { return {id: id(10), name: '工程合成连接', origin: 'http://127.0.0.1:19680', protocol: 'procurement-simulator-v1', status: 'enabled', version: 1, credential_revision: 1, credential_configured: true, created_at: '2026-11-01T00:00:00Z', latest_discovery: null, grants: []}; }
function discovery(row) { return {id: id(20), connection_id: row.id, status: 'succeeded', connection_version: row.version, credential_revision: row.credential_revision, created_at: row.created_at, completed_at: row.created_at, error_code: null, catalogue_digest: 'a'.repeat(64), devices: [device()], effective: true, integrity_error: null}; }
function fixture({existing = false, role = 'admin', locks = true, storage = new MemoryStorage(), session = new MemoryStorage()} = {}) {
  const account = {user_id: id(1), workspace_id: id(2), role}; session.setItem('ga-setup-csrf-user', account.user_id); session.setItem('ga-setup-csrf-workspace', account.workspace_id);
  const state = {me: copy(account), connection: existing ? connection() : null, receipts: new Map(), wire: [], drop: false, failReadback: false, postSwap: false, discovered: null}; let next = 100;
  const api = async (path, options = {}) => {
    if (path === '/me') return copy(state.me);
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); state.wire.push({path, raw: options.body});
      const localKey = [...storage.rows.keys()].find(key => key.endsWith(':pending')); assert.ok(localKey); const local = JSON.parse(storage.getItem(localKey)); assert.equal(local.request_id, body.request_id); assert.equal(local.attempted, true); assert.equal(Object.hasOwn(local.meta, 'token'), false); if (local.raw !== null) assert.equal(local.raw, options.body);
      const cached = state.receipts.get(body.request_id); if (cached) return copy(cached);
      let operation, job = null, grant = null;
      if (path === BASE) { operation = 'create'; state.connection = {...connection(), name: body.name, origin: body.origin}; }
      else {
        operation = path.split('/').at(-1); assert.equal(body.expected_version, state.connection.version);
        if (operation === 'discover') { job = discovery(state.connection); state.connection.latest_discovery = copy(job); state.discovered = job; }
        if (operation === 'grant') { grant = {id: id(30), connection_id: state.connection.id, device_external_id: body.device_external_id, sensor_device_id: id(31), actions: body.actions, discovery_job_id: body.discovery_job_id, catalogue_digest: state.discovered.catalogue_digest, connection_version: state.connection.version, credential_revision: state.connection.credential_revision, version: 1, effective: true, created_at: state.connection.created_at}; state.connection.grants.push(copy(grant)); }
        if (operation === 'status') { state.connection.status = body.status; state.connection.version++; if (state.connection.latest_discovery) state.connection.latest_discovery.effective = false; for (const item of state.connection.grants) item.effective = false; }
        if (operation === 'credential') { state.connection.credential_revision++; state.connection.version++; if (state.connection.latest_discovery) state.connection.latest_discovery.effective = false; for (const item of state.connection.grants) item.effective = false; }
      }
      const receipt = {request_id: body.request_id, operation, connection_id: state.connection.id, connection: copy(state.connection), discovery: job, grant}; state.receipts.set(body.request_id, copy(receipt));
      if (state.postSwap) state.me = {...state.me, user_id: id(99)};
      if (state.drop) { state.drop = false; throw Error('synthetic response loss'); }
      return receipt;
    }
    if (path === BASE) return {connections: state.connection ? [copy(state.connection)] : [], has_more: false};
    if (path.startsWith(BASE + '/requests/')) { const request_id = path.split('/').at(-1); return {found: state.receipts.has(request_id), request_id, result: copy(state.receipts.get(request_id) || null)}; }
    if (path.includes('/discoveries/')) return copy(state.discovered);
    if (path === BASE + '/' + id(10)) { if (state.failReadback) throw Error('synthetic readback loss'); return copy(state.connection); }
    throw Error('unexpected synthetic path');
  };
  const lock = locks ? {request: async (_key, _options, callback) => callback()} : null;
  const client = new DeviceGatewayClientV268({account, api, storage, sessionStorage: session, locks: lock, randomUUID: () => id(next++)});
  return {account, state, storage, session, client, api, lock};
}
async function prepareCreate(f) { await f.client.start(); f.client.edit({name: '工程合成连接', origin: 'http://127.0.0.1:19680'}); }
async function prepareExisting(f) { await f.client.start(); await f.client.select(id(10)); f.client.edit({reason: '工程合成：核对本批设备'}); }

test('V268 service addresses exclude credentials, query fragments, paths and normalization', () => {
  assert.equal(safeServiceOriginV268('http://127.0.0.1:19680'), 'http://127.0.0.1:19680');
  for (const origin of ['http://user:pass@127.0.0.1:19680', 'http://127.0.0.1:19680/?token=synthetic', 'http://127.0.0.1:19680/', 'https://example.invalid/service']) assert.throws(() => safeServiceOriginV268(origin));
  assert.throws(() => assertSafeGatewayDataV268({nested: {access_token: 'synthetic'}}));
});
test('V268 create journals only safe metadata before POST and readback confirms server persistence', async () => {
  const f = fixture(); await prepareCreate(f); await f.client.create('synthetic-ephemeral-secret');
  const saved = [...f.storage.rows.values()].join('\n'); assert.equal(saved.includes('synthetic-ephemeral-secret'), false); assert.equal(/"token"|token_hash|secret_fingerprint/.test(saved), false);
  assert.equal(f.client.pendingRecord, null); assert.equal(f.client.detail.id, id(10)); assert.equal(f.state.wire.length, 1); assert.equal(f.client.canLeave(), true);
});
test('V268 lost create response restores same safe nonce and GET recovery never repeats POST', async () => {
  const f = fixture(); await prepareCreate(f); f.state.drop = true; await assert.rejects(f.client.create('synthetic-ephemeral-secret'));
  const original = copy(f.client.pendingRecord); assert.equal(original.raw, null); assert.equal(f.client.canLeave(), true);
  await f.client.recover(); assert.equal(f.state.wire.length, 1); assert.equal(f.client.pendingRecord, null); assert.equal(f.client.detail.name, original.meta.name);
});
test('V268 unknown secret operation requires explicit retry and a newly typed secret, preserving original nonce', async () => {
  const f = fixture(); await prepareCreate(f); f.state.drop = true; await assert.rejects(f.client.create('synthetic-secret')); const pending = copy(f.client.pendingRecord); f.state.receipts.clear();
  await f.client.recover(); assert.equal(f.state.wire.length, 1); await assert.rejects(f.client.retry('')); assert.equal(f.state.wire.length, 1);
  await f.client.retry('synthetic-secret'); assert.equal(f.state.wire.length, 2); assert.equal(JSON.parse(f.state.wire[1].raw).request_id, pending.request_id); assert.equal(f.client.pendingRecord, null);
});
test('V268 nonsecret discovery unknown retry uses the exact original bytes', async () => {
  const f = fixture({existing: true}); await prepareExisting(f); f.state.drop = true; await assert.rejects(f.client.command('discover')); const raw = f.client.pendingRecord.raw; f.state.receipts.clear();
  await f.client.recover(); assert.equal(f.state.wire.length, 1); await f.client.retry(); assert.equal(f.state.wire[1].raw, raw); assert.equal(f.client.discovery.devices[0].physical_device_connected, false);
});
test('V268 durable readback failure preserves original, successful lookup later settles it', async () => {
  const f = fixture(); await prepareCreate(f); f.state.failReadback = true; await assert.rejects(f.client.create('synthetic-secret')); assert.ok(f.client.pendingRecord);
  f.state.failReadback = false; await f.client.recover(); assert.equal(f.state.wire.length, 1); assert.equal(f.client.pendingRecord, null);
});
test('V268 fresh identity after mutation blocks cross-account late response and preserves original', async () => {
  const f = fixture(); await prepareCreate(f); f.state.postSwap = true; await assert.rejects(f.client.create('synthetic-secret'));
  assert.equal(f.client.detail, null); assert.deepEqual(f.client.connections, []); assert.ok(f.client.pendingRecord); assert.equal(f.client.writable(), false); assert.equal(f.state.wire.length, 1);
});
test('V268 unavailable locks and nonadmin membership are genuinely read-only', async () => {
  for (const options of [{existing: true, locks: false}, {existing: true, role: 'operator'}]) { const f = fixture(options); await f.client.start(); assert.equal(f.client.connections.length, 1); assert.equal(f.client.writable(), false); assert.throws(() => f.client.edit({reason: '合成'})); await assert.rejects(f.client.create('synthetic')); assert.equal(f.state.wire.length, 0); }
});
test('V268 unreadable local journal blocks writes rather than discarding the original', async () => {
  const f = fixture(); f.storage.setItem(f.client.key + 'pending', '{corrupt'); await assert.rejects(f.client.start()); assert.equal(f.client.writable(), false); assert.equal(f.storage.getItem(f.client.key + 'pending'), '{corrupt'); assert.equal(f.state.wire.length, 0);
});
test('V268 storage failure prevents network mutations and keeps visible unsaved input', async () => {
  const f = fixture(); await prepareCreate(f); f.storage.setItem = () => { throw Error('synthetic storage limit'); }; assert.throws(() => f.client.edit({name: '仍保留的合成人稿'})); assert.equal(f.client.form.name, '仍保留的合成人稿'); assert.equal(f.client.formSaved, false); await assert.rejects(f.client.create('synthetic')); assert.equal(f.state.wire.length, 0);
});
test('V268 grant is pinned to declared device actions and current discovery/credential revision', async () => {
  const f = fixture({existing: true}); await prepareExisting(f); await f.client.command('discover'); const job = f.client.discovery;
  await assert.rejects(f.client.command('grant', {discovery_job_id: job.id, device_external_id: device().external_id, actions: ['start_mission']})); assert.equal(f.state.wire.length, 1);
  await f.client.command('grant', {discovery_job_id: job.id, device_external_id: device().external_id, actions: ['read_telemetry']}); assert.equal(f.client.detail.grants.length, 1);
  await f.client.command('credential', {}, 'synthetic-rotated-secret'); assert.equal(discoveryIsCurrentV268(f.client.detail, f.client.discovery), false); assert.equal(f.client.detail.grants[0].effective, false); assert.equal([...f.storage.rows.values()].join('').includes('synthetic-rotated-secret'), false);
});
test('V268 paused connection cannot discover and history is visibly stale', async () => {
  const f = fixture({existing: true}); await prepareExisting(f); await f.client.command('discover'); await f.client.command('status', {status: 'paused'}); assert.equal(f.client.detail.status, 'paused'); assert.equal(discoveryIsCurrentV268(f.client.detail, f.client.discovery), false); await assert.rejects(f.client.command('discover')); assert.equal(f.state.wire.length, 2);
});
test('V268 simulation identity is mandatory and three families keep distinct boundaries', () => {
  const row = connection(), job = discovery(row); assert.equal(discoveryIsCurrentV268(row, job), true); assert.throws(() => checkedDiscoveryV268({...job, devices: [{...device(), physical_device_connected: true}]}));
  assert.match(deviceFamilyViewV268(device()).note, /只读取/); assert.match(deviceFamilyViewV268({...device(), kind: 'ptz_camera'}).note, /不要求执行时段/); assert.match(deviceFamilyViewV268({...device(), kind: 'drone_dock'}).note, /已确认航线/); assert.equal(isReadActionV268('read_telemetry'), true); assert.equal(isReadActionV268('set_ptz'), false);
});
test('V268 independently invalid discovery is readable as blocked evidence and cannot authorize', () => {
  const row = connection(), invalid = {...discovery(row), status: 'invalid', effective: false, integrity_error: 'discovery_integrity_invalid', devices: []}; assert.equal(checkedDiscoveryV268(invalid).status, 'invalid'); assert.equal(discoveryIsCurrentV268(row, invalid), false); assert.throws(() => checkedDiscoveryV268({...invalid, effective: true})); assert.throws(() => checkedDiscoveryV268({...invalid, devices: [device()]}));
});
test('V268 independent tab forms do not overwrite another tab draft', async () => {
  const storage = new MemoryStorage(), one = fixture({storage}), two = fixture({storage}); await one.client.start(); await two.client.start(); one.client.edit({name: '第一页人稿'}); two.client.formOwner = id(999); two.client.edit({name: '第二页人稿'});
  assert.equal(JSON.parse(storage.getItem(one.client.key + 'form:' + one.client.formOwner)).form.name, '第一页人稿'); assert.equal(JSON.parse(storage.getItem(two.client.key + 'form:' + two.client.formOwner)).form.name, '第二页人稿');
});
test('V268 no-lock and demoted members can GET their original safe receipt without settling or POST', async () => {
  for (const mode of ['no-lock', 'demoted']) {
    const f = fixture(); await prepareCreate(f); f.state.drop = true; await assert.rejects(f.client.create('synthetic-secret')); const pendingRaw = f.storage.getItem(f.client.key + 'pending');
    if (mode === 'no-lock') f.client.locks = null; else { f.client.account.role = 'operator'; f.state.me.role = 'operator'; }
    await f.client.recover(); assert.equal(f.state.wire.length, 1); assert.equal(f.storage.getItem(f.client.key + 'pending'), pendingRaw); assert.equal(f.storage.getItem(f.client.key + 'receipt:' + f.client.pendingRecord.request_id), null); assert.equal(f.client.detail.id, id(10)); assert.match(f.client.notice, /只读核对/); assert.equal(f.client.canLeave(), true);
  }
});
test('V268 reliable original may be left; missing, changed or unreadable original blocks leaving', async () => {
  const f = fixture(); await prepareCreate(f); f.state.drop = true; await assert.rejects(f.client.create('synthetic-secret')); const key = f.client.key + 'pending', original = f.storage.getItem(key); assert.equal(f.client.canLeave(), true);
  f.storage.removeItem(key); assert.equal(f.client.canLeave(), false); f.storage.setItem(key, original); const changed = JSON.parse(original); changed.meta.name = '另一份合成申请'; f.storage.setItem(key, JSON.stringify(changed)); assert.equal(f.client.canLeave(), false); f.storage.setItem(key, '{broken'); assert.equal(f.client.canLeave(), false);
});
test('V268 credentials follow backend visible ASCII limits and invalid secrets never create a journal or POST', async () => {
  for (const token of ['short', 'has space value', '含中文字符的令牌', 'a'.repeat(4097), 'line\nbreak']) { const f = fixture(); await prepareCreate(f); await assert.rejects(f.client.create(token)); assert.equal(f.state.wire.length, 0); assert.equal(f.client.pending(), null); }
  for (const token of ['12345678', 'a'.repeat(4096)]) { const f = fixture(); await prepareCreate(f); await f.client.create(token); assert.equal(f.state.wire.length, 1); assert.equal([...f.storage.rows.values()].join('').includes(token), false); }
});
