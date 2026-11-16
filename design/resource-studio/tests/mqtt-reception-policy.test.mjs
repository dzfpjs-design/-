import test from 'node:test';
import assert from 'node:assert/strict';
import {RECEPTION_REPORT_MAX_AGE_MS, receptionView, receptionEpoch, receptionHistory,
  receptionErrorMessage, olderReceptionReport, receptionTime} from '../src/mqtt-reception-policy.mjs';

const now = Date.parse('2026-10-10T06:00:00Z');
const gateway = {id: 'source-one', version: 4, config_hash: 'current-config', status: 'active'};
const report = (patch = {}) => ({gateway_id: gateway.id, gateway_version: gateway.version,
  configuration_hash: gateway.config_hash, authorization_state: 'active', state: 'receiving',
  heartbeat_fresh: true, last_reported_at: new Date(now - 3000).toISOString(),
  last_connected_at: new Date(now - 10000).toISOString(), last_received_at: null,
  next_retry_at: null, error_code: null, attempts: 0, history: [], history_limit: 20, ...patch});
const view = (value, options = {}, source = gateway) => receptionView(source, value, {now, ...options});

test('only a fresh, matching authorized receiving report produces the current receiving claim', () => {
  const current = view(report());
  assert.equal(current.label, '后台正在接收');
  assert.equal(current.receiving, true);
  assert.equal(current.tone, 'good');
  // Subscription success does not require any received message or claim a device is online.
  assert.match(current.detail, /已订阅/);
  assert.equal(view(report({last_received_at: null})).receiving, true);
  assert.doesNotMatch(current.label + current.detail, /在线|安全/);
  for (const patch of [{state: 'connecting'}, {heartbeat_fresh: false}, {heartbeat_fresh: 'true'},
    {gateway_id: 'another-source'}, {gateway_version: 3}, {configuration_hash: 'previous-config'},
    {configuration_hash: ''}, {authorization_state: 'unknown'}, {error_code: 'connection_lost'}]) {
    const result = view(report(patch));
    assert.equal(result.receiving, false, JSON.stringify(patch));
    assert.notEqual(result.tone, 'good', JSON.stringify(patch));
  }
});

test('stored originals and historical receiving states cannot prove a current connection', () => {
  const original = new Date(now - 1000).toISOString();
  for (const state of ['waiting_receiver', 'connecting', 'subscribing', 'backoff', 'blocked', 'stopped', 'stale', 'standby']) {
    const result = view(report({state, last_received_at: original,
      history: [{at: original, state: 'receiving', gateway_version: 4}]}));
    assert.equal(result.receiving, false, state);
    assert.equal(result.history[0].label, '后台曾确认接收');
  }
  assert.equal(view(report({state: 'waiting_receiver', last_reported_at: null, heartbeat_fresh: false})).state, 'waiting_receiver');
});

test('request failure and refresh loading invalidate an earlier green report while retaining history', () => {
  const r = report({history: [{at: new Date(now - 5000).toISOString(), state: 'receiving', gateway_version: 4}]});
  for (const requestState of ['error', 'loading', 'outdated']) {
    const result = view(r, {requestState});
    assert.equal(result.state, 'unknown');
    assert.equal(result.receiving, false);
    assert.equal(result.history.length, 1);
  }
  assert.equal(view(r, {}, {...gateway, version: 5}).receiving, false);
  assert.equal(view(r, {}, {...gateway, version: 5}).history.length, 1);
  assert.equal(view(r, {}, {...gateway, id: 'another-source'}).history.length, 0);
});

test('the local freshness deadline expires without waiting for another successful request', () => {
  assert.equal(view(report({last_reported_at: new Date(now - RECEPTION_REPORT_MAX_AGE_MS).toISOString()})).receiving, true);
  for (const last_reported_at of [null, '', 'invalid', new Date(now - RECEPTION_REPORT_MAX_AGE_MS - 1).toISOString(), new Date(now + 10001).toISOString()]) {
    assert.equal(view(report({last_reported_at})).state, 'unknown');
  }
  assert.equal(view(report({last_reported_at: new Date(now + 10000).toISOString()})).receiving, true);
});

test('the server heartbeat deadline and server time govern freshness despite a different local clock', () => {
  assert.equal(view(report({heartbeat_ttl_seconds: 15, last_reported_at: new Date(now - 15001).toISOString()})).state, 'unknown');
  assert.equal(view(report({heartbeat_ttl_seconds: 120, last_reported_at: new Date(now - 45001).toISOString()})).state, 'unknown');
  const localClock = now + 24 * 60 * 60 * 1000;
  const r = report({heartbeat_ttl_seconds: 15, server_time: new Date(now).toISOString()});
  assert.equal(view(r, {now: localClock, receivedAt: localClock}).receiving, true);
  assert.equal(view(r, {now: localClock + 12001, receivedAt: localClock}).state, 'unknown');
  assert.equal(view(report({server_time: 'invalid'})).state, 'unknown');
  for (const heartbeat_ttl_seconds of [0, -1, '15', false]) {
    assert.equal(view(report({heartbeat_ttl_seconds})).state, 'unknown');
  }
});

test('a fresh authoritative API block is readable without a receiver heartbeat but cannot grant receiving', () => {
  const r = report({state: 'blocked', authoritative_block: true, heartbeat_fresh: false,
    last_reported_at: null, server_time: new Date(now).toISOString(), heartbeat_ttl_seconds: 15,
    error_code: 'permission_changed'});
  const blocked = view(r, {receivedAt: now});
  assert.equal(blocked.state, 'blocked');
  assert.equal(blocked.receiving, false);
  assert.match(blocked.detail, /接收权限已变化/);
  for (const options of [{requestState: 'error', receivedAt: now}, {requestState: 'loading', receivedAt: now},
    {receivedAt: now - 15001}, {}]) assert.equal(view(r, options).state, 'unknown');
  assert.equal(view({...r, server_time: 'invalid'}, {receivedAt: now}).state, 'unknown');
  assert.equal(view({...r, authoritative_block: 'true'}, {receivedAt: now}).state, 'unknown');
  assert.equal(view({...r, state: 'receiving'}, {receivedAt: now}).receiving, false);
});

test('expired receiver reports preserve safe configuration or authorization explanations beside unknown status', () => {
  const r = report({last_reported_at: new Date(now - 60000).toISOString(), error_code: 'configuration_changed'});
  const result = view(r);
  assert.equal(result.state, 'unknown');
  assert.equal(result.receiving, false);
  assert.match(result.detail, /来源约定已变化/);
  assert.match(view({...r, error_code: 'secret-raw-error'}).detail, /接收需要核查/);
  assert.doesNotMatch(view({...r, error_code: 'secret-raw-error'}).detail, /secret-raw-error/);
});

test('paused authorization is visible immediately, including when the last report or request claimed receiving', () => {
  for (const requestState of ['ready', 'loading', 'error']) {
    const result = view(report(), {requestState}, {...gateway, status: 'paused', version: 5});
    assert.equal(result.state, 'paused');
    assert.equal(result.receiving, false);
  }
  assert.equal(view(report({authorization_state: 'paused'})).state, 'paused');
  assert.equal(view(report(), {}, {...gateway, status: 'unrecognized'}).state, 'unknown');
});

test('epoch changes on gateway, configuration, version or authorization changes', () => {
  for (const patch of [{id: 'other'}, {version: 5}, {config_hash: 'new'}, {status: 'paused'}]) {
    assert.notEqual(receptionEpoch({...gateway, ...patch}), receptionEpoch(gateway));
  }
  assert.equal(olderReceptionReport(report(), report({last_reported_at: new Date(now - 5000).toISOString()})), true);
  assert.equal(olderReceptionReport(report(), report({last_reported_at: null})), true);
  assert.equal(olderReceptionReport(report(), report({last_reported_at: new Date(now).toISOString()})), false);
  assert.equal(olderReceptionReport(report(), report({gateway_version: 5, last_reported_at: null})), false);
});

test('history stays bounded and human readable without reflecting raw exception or unknown codes', () => {
  const secret = 'mqtt://name:password@broker -- raw-secret';
  const history = Array.from({length: 25}, (_, index) => ({at: new Date(now - index * 1000).toISOString(),
    state: index === 0 ? secret : 'receiving', error_code: index === 0 ? secret : null, gateway_version: 4}));
  const sanitized = receptionHistory(report({history}));
  assert.equal(sanitized.length, 20);
  assert.equal(sanitized[0].label, '接收状态待核查');
  assert.match(sanitized[0].error, /接收需要核查/);
  assert.equal(JSON.stringify(sanitized).includes(secret), false);
  assert.equal(receptionErrorMessage(secret).includes(secret), false);
  assert.equal(typeof receptionErrorMessage('__proto__'), 'string');
  assert.equal(typeof receptionErrorMessage('constructor'), 'string');
  assert.equal(typeof receptionErrorMessage({toString: null}), 'string');
  assert.match(view(report({state: secret})).detail, /接收需要核查/);
  assert.equal(view(report({state: {toString: null}})).state, 'unknown');
  assert.match(view(report({state: 'blocked', error_code: 'credentials_missing'})).detail, /认证尚未登记/);
  assert.deepEqual(receptionHistory({history: [null, {at: secret, state: 'stopped', gateway_version: secret}]}),
    [{at: null, time: null, index: 0, label: '后台接收已停止', error: '', version: null}]);
  assert.equal(receptionTime(secret, '时间未知'), '时间未知');
});

test('a new or missing source cannot appear authorized or currently receiving', () => {
  assert.equal(receptionView(null, report(), {now}).state, 'unsaved');
  assert.equal(view(null).receiving, false);
  assert.equal(view(report(), {}, {...gateway, config_hash: ''}).receiving, false);
});
