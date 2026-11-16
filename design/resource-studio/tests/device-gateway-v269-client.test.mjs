// Public synthetic API boundaries only. These tests do not contact a gateway,
// establish an IAM grant, or prove that a business/device action was executed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {DeviceGatewayClientV268} from '../src/device-gateway-client-v268.mjs';
import {DEVICE_GATEWAY_BASE_V268 as BASE} from '../src/device-gateway-ui-v268.mjs';

const id = n => '26900000-0000-4000-8000-' + String(n).padStart(12, '0');
const copy = value => JSON.parse(JSON.stringify(value));
const sortedKeys = value => Object.keys(value).sort();
const operation = 'register_tool_v269';
class MemoryStorage {
  constructor() { this.rows = new Map(); }
  getItem(key) { return this.rows.get(key) ?? null; }
  setItem(key, value) { this.rows.set(key, String(value)); }
  removeItem(key) { this.rows.delete(key); }
}
function fixture() {
  const account = {user_id: id(1), workspace_id: id(2), role: 'admin', username: 'synthetic-v269-admin'};
  const discovery = {id: id(20), connection_id: id(10), status: 'succeeded', connection_version: 7,
    credential_revision: 3, created_at: '2026-11-01T00:00:00Z', completed_at: '2026-11-01T00:00:01Z',
    error_code: null, catalogue_digest: 'd'.repeat(64), effective: true, integrity_error: null,
    devices: [{external_id: 'synthetic-weather-269', name: '合成天气设备', kind: 'weather_station',
      actions: ['get_status', 'read_telemetry'], simulation: true, origin_kind: 'simulated_equipment',
      field_verified: false, physical_device_connected: false}]};
  const grant = {id: id(30), connection_id: id(10), device_external_id: 'synthetic-weather-269',
    sensor_device_id: id(31), actions: ['get_status', 'read_telemetry'], discovery_job_id: discovery.id,
    catalogue_digest: discovery.catalogue_digest, connection_version: 7, credential_revision: 3,
    version: 4, effective: true, integrity_error: null, created_at: discovery.created_at};
  const connection = {id: id(10), name: '合成持久天气连接', origin: 'http://127.0.0.1:19680',
    protocol: 'procurement-simulator-v1', status: 'enabled', version: 7, credential_revision: 3,
    credential_configured: true, created_at: discovery.created_at, latest_discovery: discovery, grants: [grant]};
  const capability = {id: id(40), hash: 'a'.repeat(64), version: 3, release_version: '1.0.0', status: 'published'};
  const storage = new MemoryStorage(), state = {me: copy(account), connection: copy(connection),
    receipts: new Map(), posts: [], reads: [], tools: [], loseResponse: false, hideLookup: false,
    toolsFault: null};
  let sequence = 100;
  const extra = {grant_id: grant.id, grant_expected_version: grant.version, action: 'read_telemetry'};
  const api = async (path, options = {}) => {
    const method = options.method || 'GET';
    assert.equal(options.headers?.['X-Workspace-ID'], account.workspace_id);
    if (method === 'POST') {
      // Count attempted transport before the fixture checks its contract, so a
      // rejected/invalid POST cannot make the zero-mutation assertions pass.
      state.posts.push({path, raw: options.body});
      assert.equal(path, BASE + '/' + connection.id + '/tools', 'only the registered-tool POST is permitted');
      const body = JSON.parse(options.body);
      assert.deepEqual(sortedKeys(body), ['action', 'expected_version', 'grant_expected_version', 'grant_id', 'reason', 'request_id']);
      assert.equal(body.expected_version, connection.version); assert.equal(body.grant_id, grant.id);
      assert.equal(body.grant_expected_version, grant.version); assert.equal(body.action, 'read_telemetry');
      assert.equal(body.reason, '核对获批天气读取能力，保留原申请。');
      const entries = [...storage.rows].filter(([key]) => key.endsWith(':pending'));
      assert.equal(entries.length, 1); const pending = JSON.parse(entries[0][1]);
      assert.equal(pending.operation, operation); assert.equal(pending.attempted, true);
      assert.equal(pending.raw, options.body); assert.deepEqual(pending.meta, body);
      let receipt = state.receipts.get(body.request_id);
      if (!receipt) {
        receipt = {request_id: body.request_id, operation, connection_id: connection.id,
          connection: copy(state.connection), discovery: null, grant: copy(grant), capability: copy(capability)};
        state.receipts.set(body.request_id, copy(receipt));
        state.tools = [{...copy(capability), connection_id: connection.id, device_external_id: grant.device_external_id,
          action: body.action, integrity_valid: true, configuration_current: true, availability_reason: 'current'}];
      }
      if (state.loseResponse) { state.loseResponse = false; throw Error('synthetic response lost after durable server registration'); }
      return copy(receipt);
    }
    assert.equal(method, 'GET'); state.reads.push(path);
    if (path === '/me') return copy(state.me);
    if (path === BASE) return {connections: [copy(state.connection)], has_more: false};
    if (path === BASE + '/' + connection.id) return copy(state.connection);
    if (path === BASE + '/' + connection.id + '/discoveries/' + discovery.id) return copy(discovery);
    if (path === BASE + '/' + connection.id + '/tools') {
      const value = {tools: copy(state.tools), has_more: false};
      return state.toolsFault ? state.toolsFault(value) : value;
    }
    if (path.startsWith(BASE + '/requests/')) {
      const request_id = path.slice((BASE + '/requests/').length);
      assert.ok(state.receipts.has(request_id), 'only this actor\'s actual saved nonce may be queried');
      return {request_id, found: !state.hideLookup, result: state.hideLookup ? null : copy(state.receipts.get(request_id))};
    }
    assert.fail('unexpected synthetic API path');
  };
  function mount({locks = true} = {}) {
    const sessionStorage = new MemoryStorage();
    sessionStorage.setItem('ga-setup-csrf-user', account.user_id);
    sessionStorage.setItem('ga-setup-csrf-workspace', account.workspace_id);
    return new DeviceGatewayClientV268({account, api, storage, sessionStorage,
      locks: locks ? {request: async (_key, _options, work) => work()} : null,
      randomUUID: () => id(sequence++)});
  }
  return {account, state, storage, extra, mount, originalGrant: copy(grant)};
}
async function prepared(f) {
  const client = f.mount(); await client.start(); await client.select(id(10));
  client.edit({reason: '核对获批天气读取能力，保留原申请。'}); return client;
}

test('V269 registration has one server mutation and independently reads the published capability', async () => {
  const f = fixture(), client = await prepared(f); await client.command(operation, f.extra);
  assert.equal(f.state.posts.length, 1); assert.equal(client.pendingRecord, null);
  assert.equal(client.toolReceipt.capability.id, id(40));
  assert.ok(f.state.reads.includes(BASE + '/' + id(10) + '/tools'));
  assert.deepEqual(f.state.connection.grants, [f.originalGrant], 'capability registration must not replace the source grant');
});

test('V269 lost response survives disposal and same-actor remount; GET recovery never repeats POST', async () => {
  const f = fixture(), client = await prepared(f); f.state.loseResponse = true;
  await assert.rejects(client.command(operation, f.extra));
  const pending = copy(client.pendingRecord), raw = f.storage.getItem(client.key + 'pending'); client.dispose();
  const remounted = f.mount(); await remounted.start();
  assert.equal(remounted.pendingRecord.operation, operation); assert.deepEqual(remounted.pendingRecord, pending);
  assert.equal(f.storage.getItem(remounted.key + 'pending'), raw); assert.equal(f.state.posts.length, 1);
  await remounted.recover();
  assert.equal(f.state.posts.length, 1); assert.equal(remounted.pendingRecord, null);
  assert.ok(f.state.reads.includes(BASE + '/requests/' + pending.request_id));
  assert.equal(remounted.toolReceipt.request_id, pending.request_id);
  assert.equal(JSON.parse(f.storage.getItem(remounted.key + 'receipt:' + pending.request_id)).capability.id, id(40));
});

test('V269 explicit retry after unknown lookup preserves every metadata field and the exact original nonce/body', async () => {
  const f = fixture(), client = await prepared(f); f.state.loseResponse = true;
  await assert.rejects(client.command(operation, f.extra)); const original = copy(client.pendingRecord);
  f.state.hideLookup = true; await client.recover(); assert.equal(f.state.posts.length, 1);
  client.dispose(); const remounted = f.mount(); await remounted.start(); await remounted.retry();
  assert.equal(f.state.posts.length, 2); assert.equal(f.state.posts[1].raw, original.raw);
  assert.deepEqual(JSON.parse(f.state.posts[1].raw), original.meta); assert.equal(remounted.pendingRecord, null);
  assert.equal(f.state.receipts.size, 1, 'same nonce must refer to one server registration');
});

for (const mutation of ['connection_version', 'credential_revision', 'grant_version', 'grant_inactive', 'action_revoked']) {
  test('V269 configuration_changed blocks a new registration before POST: ' + mutation, async () => {
    const f = fixture(), client = await prepared(f), current = f.state.connection;
    if (mutation === 'connection_version') current.version++;
    if (mutation === 'credential_revision') current.credential_revision++;
    if (mutation === 'grant_version') current.grants[0].version++;
    if (mutation === 'grant_inactive') current.grants[0].effective = false;
    if (mutation === 'action_revoked') current.grants[0].actions = ['get_status'];
    await assert.rejects(client.command(operation, f.extra));
    assert.equal(f.state.posts.length, 0); assert.equal(client.pending(), null);
  });
}

const corruptions = {
  missing: value => ({...value, tools: []}),
  missing_inventory: () => ({}),
  hash: value => ({...value, tools: [{...value.tools[0], hash: 'b'.repeat(64)}]}),
  release: value => ({...value, tools: [{...value.tools[0], release_version: '2.0.0'}]}),
  integrity: value => ({...value, tools: [{...value.tools[0], integrity_valid: false}]}),
  action: value => ({...value, tools: [{...value.tools[0], action: 'get_status'}]}),
  device: value => ({...value, tools: [{...value.tools[0], device_external_id: 'another-synthetic-device'}]}),
  connection: value => ({...value, tools: [{...value.tools[0], connection_id: id(99)}]}),
  unpublished: value => ({...value, tools: [{...value.tools[0], status: 'draft'}]}),
  malformed_current: value => ({...value, tools: [{...value.tools[0], configuration_current: 'false'}]}),
};
for (const [name, mutate] of Object.entries(corruptions)) {
  test('V269 independent tool-list ' + name + ' cannot settle the durable original', async () => {
    const f = fixture(), client = await prepared(f); f.state.toolsFault = mutate;
    await assert.rejects(client.command(operation, f.extra)); const pending = copy(client.pendingRecord);
    assert.ok(pending?.attempted); assert.equal(client.toolReceipt, null);
    assert.equal(f.storage.getItem(client.key + 'receipt:' + pending.request_id), null);
    await assert.rejects(client.recover()); assert.deepEqual(client.pendingRecord, pending);
    assert.equal(f.state.posts.length, 1);
    f.state.toolsFault = null; await client.recover();
    assert.equal(client.pendingRecord, null); assert.equal(f.state.posts.length, 1);
  });
}

test('V269 a saved registration can be acknowledged as history after authorization changes without a new POST', async () => {
  const f = fixture(), client = await prepared(f); f.state.loseResponse = true;
  await assert.rejects(client.command(operation, f.extra));
  const pending = copy(client.pendingRecord); client.dispose();
  f.state.connection.grants[0].effective = false;
  f.state.toolsFault = value => ({...value, tools: [{...value.tools[0],
    configuration_current: false, availability_reason: 'authorization_changed'}]});
  const remounted = f.mount(); await remounted.start(); await remounted.recover();
  assert.equal(remounted.pendingRecord, null); assert.equal(f.state.posts.length, 1);
  assert.equal(remounted.toolReceipt.request_id, pending.request_id);
  assert.match(remounted.notice, /已变化|过期|历史|不可用|不能/);
  assert.doesNotMatch(remounted.notice, /可供业务流程选用/);
  assert.equal(JSON.parse(f.storage.getItem(remounted.key + 'receipt:' + pending.request_id)).capability.id, id(40));
});

test('V269 no-lock read-only recovery checks the same saved registration without clearing or archiving its ticket', async () => {
  const f = fixture(), client = await prepared(f); f.state.loseResponse = true;
  await assert.rejects(client.command(operation, f.extra));
  const pending = copy(client.pendingRecord), before = [...f.storage.rows]; client.dispose();
  const readonly = f.mount({locks: false}); await readonly.start(); await readonly.recover();
  assert.equal(readonly.writable(), false); assert.deepEqual([...f.storage.rows], before);
  assert.deepEqual(readonly.pendingRecord, pending); assert.match(readonly.notice, /只读核对/);
  assert.equal(f.state.posts.length, 1); assert.equal(readonly.detail.id, id(10));
  readonly.dispose(); const writable = f.mount(); await writable.start(); await writable.recover();
  assert.equal(writable.pendingRecord, null); assert.equal(f.state.posts.length, 1);
});

test('V269 another actor cannot adopt a late registration response or settle the original', async () => {
  const f = fixture(), client = await prepared(f); f.state.loseResponse = true;
  await assert.rejects(client.command(operation, f.extra)); const original = [...f.storage.rows];
  f.state.me.user_id = id(99); await assert.rejects(client.recover());
  assert.equal(client.blocked, true); assert.deepEqual([...f.storage.rows], original);
  assert.equal(f.state.posts.length, 1); assert.equal(client.toolReceipt, null);
});
