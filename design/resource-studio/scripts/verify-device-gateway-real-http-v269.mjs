// Root-only formal ReactDOM + real JSON HTTP verification, not a native browser.
// NODE_ENV=development node scripts/verify-device-gateway-real-http-v269.mjs /absolute/0600-config.json
// Config retains the V268 shape. Only phase:'register-tool-v269'|'read' is allowed.
// connection_id and discovery_id must identify the existing, explicitly selected
// connection. No create/discover/grant/status/credential or device call is allowed.
// FileStorage and NodeLocks are adapters; this does not establish native browser,
// hardware, business-approval, or device-execution acceptance.
import React, {act} from 'react';
import {build} from 'vite';
import {JSDOM, ResourceLoader, VirtualConsole} from 'jsdom';
import {lstat, readFile, open, mkdtemp, writeFile, rm} from 'node:fs/promises';
import {openSync, closeSync, fsyncSync, writeFileSync, readFileSync, lstatSync, renameSync, unlinkSync} from 'node:fs';
import {dirname, resolve, join, isAbsolute} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto, randomUUID, createHash} from 'node:crypto';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(project, 'package.json'));
const reactPath = require.resolve('react'), reactURL = pathToFileURL(reactPath).href;
const nativeFetch = globalThis.fetch, nativeInterval = globalThis.setInterval, nativeClear = globalThis.clearInterval;
const BASE = '/workspace/device-gateways', OP = 'register_tool_v269';
const CONSUMED = 'harness:consumed:register-tool-v269', ANCHOR = 'harness:v269:registration';
const copy = value => JSON.parse(JSON.stringify(value));
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const hash = value => createHash('sha256').update(typeof value === 'string' ? value : canonical(value)).digest('hex');
const canonical = value => JSON.stringify((function sort(item) { return Array.isArray(item) ? item.map(sort) : plain(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, sort(item[key])])) : item; })(value));
const exact = (value, fields) => plain(value) && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key));
const sleep = milliseconds => new Promise(done => setTimeout(done, milliseconds));
function need(value, safeCode) { if (!value) throw Object.assign(Error(safeCode), {safeCode}); }
const report = {schema: 'device-gateway-real-http-verification-v269', verification_id: randomUUID(), ok: false,
  phase: null, mounts: [], counts: {get: 0, post: 0, register_tool_v269: 0, original_lookup: 0,
    tools_read: 0, intentional_response_loss: 0, denied: 0, xhr: 0, navigation: 0, media: 0},
  checkpoints: [], errors: [], native_browser: false, native_web_locks_verified: false,
  native_storage_verified: false, file_storage_adapter: true, node_lock_adapter: true,
  physical_device_verified: false, business_approval_verified: false, device_execution_verified: false};
let config, storage, scope, code, temporary, configPath, mountNumber = 0, inflight = 0, firstReceipt = null, baseline = null;
const observed = new Map(), ownNonces = new Set();
const prefix = () => 'device-gateway-v268:' + scope.workspace_id + ':' + scope.actor_id + ':';
function checkpoint(name) { report.checkpoints.push(name); process.stdout.write(JSON.stringify({checkpoint: name, gets: report.counts.get, posts: report.counts.post}) + '\n'); }
function containsCredential(value) { const raw = typeof value === 'string' ? value : JSON.stringify(value); return [config?.cookie, config?.csrf_token, config?.device_token].some(secret => typeof secret === 'string' && secret.length > 0 && raw.includes(secret)); }
function safeReport() { const raw = JSON.stringify(report); need(!containsCredential(raw) && !raw.includes(config.origin) && !raw.includes(config.device_origin), 'report_sensitive_value_blocked'); }
class Memory {
  constructor() { this.rows = new Map(); }
  get length() { return this.rows.size; } key(index) { return [...this.rows.keys()][index] ?? null; }
  getItem(key) { return this.rows.get(key) ?? null; } setItem(key, value) { this.rows.set(key, String(value)); }
  removeItem(key) { this.rows.delete(key); }
}
// Same envelope as V268. Existing V268 entries/consumed markers are preserved.
class FileStorage extends Memory {
  constructor(path, owner) { super(); this.path = path; this.scope = copy(owner); this.read(); }
  read() {
    const stat = lstatSync(this.path);
    need(stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.size <= 8 * 1024 * 1024, 'file_storage_private_file_invalid');
    const raw = readFileSync(this.path, 'utf8'); need(!containsCredential(raw), 'credential_found_in_file_storage'); const value = JSON.parse(raw);
    need(exact(value, ['schema', 'scope', 'entries']) && value.schema === 'device-gateway-ui-file-storage-v268'
      && canonical(value.scope) === canonical(this.scope) && Array.isArray(value.entries)
      && value.entries.every(pair => Array.isArray(pair) && pair.length === 2 && pair.every(item => typeof item === 'string'))
      && new Set(value.entries.map(pair => pair[0])).size === value.entries.length, 'file_storage_scope_or_entries_invalid');
    this.rows = new Map(value.entries);
  }
  commit(next) {
    const raw = JSON.stringify({schema: 'device-gateway-ui-file-storage-v268', scope: this.scope, entries: [...next]}) + '\n';
    need(!containsCredential(raw), 'credential_write_to_file_storage_blocked');
    const part = this.path + '.' + randomUUID() + '.part', fd = openSync(part, 'wx', 0o600);
    try { writeFileSync(fd, raw); fsyncSync(fd); } finally { closeSync(fd); }
    try {
      renameSync(part, this.path); const folder = openSync(dirname(this.path), 'r');
      try { fsyncSync(folder); } finally { closeSync(folder); }
      need(readFileSync(this.path, 'utf8') === raw, 'file_storage_readback_failed'); this.rows = new Map(next);
    } finally { try { unlinkSync(part); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
  }
  get length() { this.read(); return this.rows.size; } key(index) { this.read(); return [...this.rows.keys()][index] ?? null; }
  getItem(key) { this.read(); return this.rows.get(key) ?? null; }
  setItem(key, value) { this.read(); const next = new Map(this.rows); next.set(key, String(value)); this.commit(next); }
  removeItem(key) { this.read(); const next = new Map(this.rows); next.delete(key); this.commit(next); }
}
function nodeLocks() { const tails = new Map(); return {request(key, _options, work) { const next = (tails.get(key) || Promise.resolve()).catch(() => {}).then(work); tails.set(key, next); return next; }}; }
async function loadConfig() {
  configPath = process.argv[2]; need(typeof configPath === 'string' && isAbsolute(configPath), 'absolute_private_config_required');
  const stat = await lstat(configPath);
  need(stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.size <= 128 * 1024, 'private_config_0600_required');
  const value = JSON.parse(await readFile(configPath, 'utf8'));
  const required = ['origin', 'cookie', 'csrf_token', 'account', 'device_origin', 'device_token', 'local_state_path', 'output_path', 'phase', 'connection_id', 'discovery_id'];
  need(plain(value) && required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => required.includes(key) || key === 'timeout_ms'), 'config_shape_invalid');
  const origin = new URL(value.origin);
  need(origin.origin === value.origin && ['http:', 'https:'].includes(origin.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) && !origin.username && !origin.password, 'existing_loopback_server_required');
  need(exact(value.account, ['user_id', 'workspace_id', 'role', 'username']) && uuid(value.account.user_id) && uuid(value.account.workspace_id) && ['admin', 'operator', 'viewer'].includes(value.account.role) && typeof value.account.username === 'string', 'account_scope_invalid');
  need(['cookie', 'csrf_token'].every(key => typeof value[key] === 'string' && value[key].length > 0 && !/[\r\n\x00]/.test(value[key])), 'session_invalid');
  need(['register-tool-v269', 'read'].includes(value.phase) && (value.phase === 'read' || value.account.role === 'admin'), 'phase_or_write_role_invalid');
  need(typeof value.device_token === 'string' && /^[!-~]{8,4096}$/.test(value.device_token), 'device_credential_shape_invalid');
  const device = new URL(value.device_origin);
  need(device.origin === value.device_origin && ['http:', 'https:'].includes(device.protocol) && !device.username && !device.password && !device.search && !device.hash, 'exact_device_origin_required');
  need(uuid(value.connection_id) && uuid(value.discovery_id), 'explicit_existing_connection_and_discovery_required');
  need(['local_state_path', 'output_path'].every(key => typeof value[key] === 'string' && isAbsolute(value[key])) && new Set([configPath, value.local_state_path, value.output_path].map(path => resolve(path))).size === 3, 'independent_private_paths_required');
  try { await lstat(value.output_path); need(false, 'evidence_output_already_exists'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  need(value.timeout_ms === undefined || Number.isSafeInteger(value.timeout_ms) && value.timeout_ms >= 10000 && value.timeout_ms <= 120000, 'timeout_invalid');
  return {...value, timeout_ms: value.timeout_ms ?? 45000};
}
function checkedPending(raw) {
  const pending = JSON.parse(raw); code?.assertSafeGatewayDataV268(pending);
  need(pending.schema === 'device-gateway-original-v268' && canonical(pending.scope) === canonical(scope)
    && pending.operation === OP && pending.connection_id === config.connection_id && uuid(pending.request_id)
    && pending.path === BASE + '/' + config.connection_id + '/tools' && pending.meta?.request_id === pending.request_id
    && pending.raw === JSON.stringify(pending.meta) && typeof pending.attempted === 'boolean', 'original_registration_scope_invalid');
  need(exact(pending.meta, ['request_id', 'expected_version', 'grant_id', 'grant_expected_version', 'action', 'reason'])
    && pending.meta.action === 'read_telemetry' && uuid(pending.meta.grant_id), 'original_registration_metadata_invalid');
  ownNonces.add(pending.request_id); return pending;
}
function checkedRoute(path, method) {
  need(typeof path === 'string' && !/[?#\r\n\x00]/.test(path), 'relative_exact_api_path_required');
  if (path === '/me') { need(method === 'GET', 'identity_write_forbidden'); return {wire: '/api/me', operation: 'identity'}; }
  // The formal component obtains its selector through this collection GET. It
  // never reads details/tools/discoveries of any connection except the fixed ID.
  if (path === BASE) { need(method === 'GET', 'connection_collection_write_forbidden'); return {wire: '/api' + path, operation: 'list'}; }
  const request = path.match(/^\/workspace\/device-gateways\/requests\/([0-9a-f-]+)$/i);
  if (request) { need(method === 'GET' && uuid(request[1]) && ownNonces.has(request[1]), 'unapproved_original_lookup'); return {wire: '/api' + path, operation: 'original_lookup', request_id: request[1]}; }
  const conn = BASE + '/' + config.connection_id;
  if (path === conn) { need(method === 'GET', 'connection_get_only'); return {wire: '/api' + path, operation: 'connection_read'}; }
  if (path === conn + '/tools') return {wire: '/api' + path, operation: method === 'POST' ? OP : 'tools_read'};
  if (path === conn + '/discoveries/' + config.discovery_id) { need(method === 'GET', 'discovery_get_only'); return {wire: '/api' + path, operation: 'discovery_read'}; }
  report.counts.denied++; need(false, 'unapproved_gateway_route');
}
function capability(value) {
  need(plain(value) && uuid(value.id) && /^[0-9a-f]{64}$/.test(value.hash) && Number.isSafeInteger(value.version) && value.version > 0 && typeof value.release_version === 'string' && value.release_version.length > 0 && typeof value.status === 'string', 'actual_capability_metadata_invalid');
  return {id: value.id, hash: value.hash, version: value.version, release_version: value.release_version};
}
function checkedReceipt(value, requestID) {
  code.assertSafeGatewayDataV268(value); code.checkedConnectionV268(value.connection);
  need(value.request_id === requestID && value.operation === OP && value.connection_id === config.connection_id
    && value.connection.id === config.connection_id && value.discovery === null && value.grant?.connection_id === config.connection_id
    && value.grant.discovery_job_id === config.discovery_id && value.grant.device_external_id === 'sim-weather-01'
    && value.grant.actions.includes('read_telemetry') && value.capability.status === 'published', 'actual_registration_receipt_invalid');
  capability(value.capability); return value;
}
function configurationPin(row) { return {connection_id: row.id, version: row.version, credential_revision: row.credential_revision, status: row.status, discovery_id: row.latest_discovery?.id ?? null, grants: copy(row.grants)}; }
function saveAnchor(receipt, raw) {
  const value = {schema: 'device-gateway-registration-anchor-v269', connection_id: config.connection_id,
    discovery_id: config.discovery_id, grant_id: receipt.grant.id, request_id: receipt.request_id,
    capability: capability(receipt.capability), receipt_hash: hash(receipt), wire_hash: hash(raw)};
  const prior = storage.getItem(ANCHOR); need(prior === null || canonical(JSON.parse(prior)) === canonical(value), 'registration_anchor_changed');
  storage.setItem(ANCHOR, JSON.stringify(value));
}
async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase(); need(['GET', 'POST'].includes(method), 'http_method_forbidden');
  const route = checkedRoute(path, method), headers = new Headers(options.headers);
  need(!headers.has('Cookie') && (!headers.has('X-Workspace-ID') || headers.get('X-Workspace-ID') === config.account.workspace_id) && (!headers.has('X-CSRF-Token') || headers.get('X-CSRF-Token') === config.csrf_token), 'component_identity_headers_changed');
  let body;
  if (method === 'POST') {
    need(config.phase === 'register-tool-v269' && route.operation === OP && mountNumber === 1 && report.counts.post === 0 && typeof options.body === 'string', 'phase_post_budget_exceeded');
    body = JSON.parse(options.body);
    need(exact(body, ['request_id', 'expected_version', 'grant_id', 'grant_expected_version', 'action', 'reason']) && uuid(body.request_id)
      && body.action === 'read_telemetry' && body.expected_version === baseline.connection.version
      && body.grant_id === baseline.grant.id && body.grant_expected_version === baseline.grant.version
      && typeof body.reason === 'string' && body.reason.trim().length > 0, 'registration_post_not_authorized');
    const pendingRaw = storage.getItem(prefix() + 'pending'); need(pendingRaw !== null, 'prepost_original_missing');
    const pending = checkedPending(pendingRaw);
    need(pending.attempted === true && pending.raw === options.body && canonical(pending.meta) === canonical(body), 'prepost_exact_original_mismatch');
    const marker = 'harness:v269:post:' + body.request_id; need(storage.getItem(marker) === null, 'duplicate_registration_post_blocked');
    storage.setItem(marker, JSON.stringify({request_id: body.request_id, consumed: true}));
    report.counts.post++; report.counts.register_tool_v269++; headers.set('Content-Type', 'application/json');
  } else {
    need(options.body === undefined || options.body === null, 'get_body_forbidden'); report.counts.get++;
    if (route.operation === 'original_lookup') report.counts.original_lookup++;
    if (route.operation === 'tools_read') report.counts.tools_read++;
  }
  headers.set('Cookie', config.cookie); headers.set('X-CSRF-Token', config.csrf_token);
  headers.set('X-Workspace-ID', config.account.workspace_id); headers.set('Accept', 'application/json');
  const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(config.timeout_ms)]) : AbortSignal.timeout(config.timeout_ms);
  inflight++;
  try {
    const response = await nativeFetch(new URL(route.wire, config.origin), {...options, method, headers, credentials: 'same-origin', redirect: 'error', signal});
    need(response.headers.get('Content-Type')?.split(';')[0] === 'application/json', 'non_json_response_blocked');
    const value = await response.json(); need(!containsCredential(value), 'credential_in_server_response_blocked');
    if (!response.ok) throw Object.assign(Error('actual_json_http_failed'), {status: response.status, safeCode: 'actual_json_http_failed'});
    code.assertSafeGatewayDataV268(value);
    if (route.operation === 'identity') need(value.user_id === config.account.user_id && value.workspace_id === config.account.workspace_id && value.role === config.account.role, 'actual_identity_changed');
    if (route.operation === 'connection_read') {
      code.checkedConnectionV268(value); need(value.id === config.connection_id && value.origin === config.device_origin && value.latest_discovery?.id === config.discovery_id, 'actual_target_connection_changed');
    }
    if (route.operation === 'discovery_read') { code.checkedDiscoveryV268(value); need(value.id === config.discovery_id && value.connection_id === config.connection_id, 'actual_discovery_scope_changed'); }
    if (route.operation === 'tools_read') need(Array.isArray(value.tools) && typeof value.has_more === 'boolean' && value.tools.every(tool => tool.connection_id === config.connection_id), 'actual_tool_inventory_invalid');
    if (route.operation === 'original_lookup') {
      need(value.request_id === route.request_id && typeof value.found === 'boolean', 'actual_original_lookup_invalid');
      if (value.found) checkedReceipt(value.result, route.request_id);
      else need(value.result === null || value.result === undefined, 'unknown_original_contains_result');
    }
    if (method === 'POST') {
      need(response.status === 200, 'actual_registration_not_200'); firstReceipt = copy(checkedReceipt(value, body.request_id));
      need(canonical(firstReceipt.grant) === canonical(baseline.grant), 'source_grant_changed_during_registration');
      saveAnchor(firstReceipt, options.body); report.counts.intentional_response_loss++;
      throw Object.assign(Error('actual_saved_response_intentionally_withheld'), {safeCode: 'actual_saved_response_intentionally_withheld'});
    }
    observed.set(path, copy(value)); return value;
  } finally { inflight--; }
}
async function compile() {
  if (code) return code;
  temporary = await mkdtemp(join(tmpdir(), 'device-gateway-real-http-v269-')); const entry = join(temporary, 'entry.jsx');
  await writeFile(entry, 'export {default as Studio} from ' + JSON.stringify(join(project, 'src/DeviceGatewayStudioV268.jsx')) + ';export * from ' + JSON.stringify(join(project, 'src/device-gateway-ui-v268.mjs')) + ';');
  const result = await build({root: project, configFile: false, envDir: temporary, cacheDir: join(temporary, 'cache'), logLevel: 'silent', define: {'process.env.NODE_ENV': '"development"'}, build: {ssr: entry, write: false, minify: false, rollupOptions: {external: name => ['react', reactPath, reactURL].includes(name), output: {format: 'es', inlineDynamicImports: true, paths: name => ['react', reactPath, reactURL].includes(name) ? reactURL : name}}}});
  const chunk = result.output.find(row => row.type === 'chunk' && row.isEntry); need(chunk, 'formal_component_bundle_missing');
  const file = join(temporary, 'formal-component.mjs'); await writeFile(file, chunk.code); code = await import(pathToFileURL(file).href); return code;
}
async function observe(work) { let value; await act(async () => { value = await work(); }); return value; }
async function until(predicate, safeCode) { const end = Date.now() + config.timeout_ms; while (Date.now() < end) { if (await observe(async () => { await sleep(25); return predicate(); })) return; } need(await observe(async () => predicate()), safeCode); }
async function mount(number, action) {
  mountNumber = number; const old = new Map(), intervals = new Set();
  const phase = {number, verified: false, polling_stopped: false, react_errors: 0, console_warnings: 0}; report.mounts.push(phase);
  const vc = new VirtualConsole(); vc.on('jsdomError', () => phase.react_errors++);
  class Offline extends ResourceLoader { fetch() { report.counts.denied++; throw Error('external_resource_forbidden'); } }
  const window = new JSDOM('<div id="root"></div>', {url: config.origin + '/setup', resources: new Offline(), virtualConsole: vc}).window;
  const session = new Memory(); session.setItem('ga-setup-csrf-user', config.account.user_id); session.setItem('ga-setup-csrf-workspace', config.account.workspace_id);
  let tracking = false;
  const timer = (fn, ms, ...args) => { const handle = nativeInterval(fn, ms, ...args); if (tracking) intervals.add(handle); return handle; };
  const clear = handle => { intervals.delete(handle); nativeClear(handle); };
  const denyFetch = () => { report.counts.denied++; throw Error('uninjected_fetch_forbidden'); };
  window.setInterval = timer; window.clearInterval = clear; window.fetch = denyFetch;
  window.confirm = () => { report.counts.denied++; throw Error('native_confirmation_forbidden'); };
  window.open = () => { report.counts.navigation++; throw Error('navigation_forbidden'); };
  for (const method of ['pushState', 'replaceState', 'go', 'back', 'forward']) window.history[method] = () => { report.counts.navigation++; throw Error('navigation_forbidden'); };
  window.HTMLAnchorElement.prototype.click = function () { report.counts.navigation++; throw Error('navigation_forbidden'); };
  window.HTMLMediaElement.prototype.play = function () { report.counts.media++; throw Error('media_forbidden'); };
  class NoXHR { constructor() { report.counts.xhr++; throw Error('xhr_forbidden'); } }
  class NoChannel { constructor() { report.counts.denied++; throw Error('unapproved_network_channel_forbidden'); } }
  window.XMLHttpRequest = NoXHR; window.WebSocket = NoChannel; window.EventSource = NoChannel; window.Worker = NoChannel;
  Object.defineProperty(window.navigator, 'sendBeacon', {value: denyFetch, configurable: true});
  Object.defineProperty(window.navigator, 'locks', {value: config.phase === 'read' ? undefined : nodeLocks(), configurable: true});
  for (const [key, value] of Object.entries({window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, Event: window.Event, localStorage: storage, sessionStorage: session, crypto: globalThis.crypto || webcrypto, fetch: denyFetch, XMLHttpRequest: NoXHR, WebSocket: NoChannel, EventSource: NoChannel, Worker: NoChannel, IS_REACT_ACT_ENVIRONMENT: true, setInterval: timer, clearInterval: clear})) { old.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, {value, writable: true, configurable: true}); }
  const oldError = console.error, oldWarn = console.warn; console.error = () => phase.console_warnings++; console.warn = () => phase.console_warnings++;
  let root, container, guard;
  try {
    const actual = await compile(), {createRoot} = await import('react-dom/client'); container = document.getElementById('root');
    root = createRoot(container, {onUncaughtError: () => phase.react_errors++, onCaughtError: () => phase.react_errors++, onRecoverableError: () => phase.react_errors++});
    const Btn = ({children, primary, ghost, icon, ...rest}) => React.createElement('button', {type: 'button', ...rest}, children);
    tracking = true; await act(async () => root.render(React.createElement(actual.Studio, {account: config.account, api, Btn, onRegisterGuard: fn => { guard = fn; }})));
    const button = label => [...container.querySelectorAll('button')].find(node => node.textContent === label);
    const field = label => [...container.querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label)?.querySelector('input,select,textarea');
    const fill = async (label, value) => { await until(() => field(label) && !field(label).matches(':disabled'), 'actual_field_unavailable'); await act(async () => { const node = field(label), prototype = node.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value); node.dispatchEvent(new window.Event(node.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); if (node.tagName !== 'SELECT') node.dispatchEvent(new window.Event('change', {bubbles: true})); }); };
    const click = async label => { await until(() => button(label) && !button(label).matches(':disabled'), 'actual_button_unavailable'); await act(async () => button(label).click()); };
    await until(() => field('已保存的设备连接') && button('刷新连接清单') && !button('刷新连接清单').matches(':disabled') && inflight === 0, 'formal_component_connection_list_not_ready');
    await action({container, button, field, fill, click});
    need(!container.querySelector('pre') && !container.querySelector('[contenteditable="true"]'), 'raw_debug_editor_present');
    need(container.textContent.includes('采购参数测试设备') && container.textContent.includes('没有经过现场身份核验'), 'simulation_boundary_missing');
    need(typeof guard === 'function' && guard() === true, 'durable_component_leave_guard_failed');
    need(phase.react_errors === 0 && phase.console_warnings === 0, 'reactdom_errors_or_warnings'); phase.verified = true;
  } finally {
    try {
      if (root) await act(async () => root.unmount()); await until(() => inflight === 0, 'unmount_http_not_drained');
      need(intervals.size === 0, 'component_poll_interval_not_cleared'); const requests = report.counts.get + report.counts.post;
      await sleep(3200); need(intervals.size === 0 && inflight === 0 && report.counts.get + report.counts.post === requests, 'component_polled_after_unmount'); phase.polling_stopped = true;
    } finally { for (const handle of intervals) nativeClear(handle); console.error = oldError; console.warn = oldWarn; window.close(); for (const [key, descriptor] of old) if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
}
async function selectConnection(ui) {
  if (ui.field('已保存的设备连接').value !== config.connection_id) await ui.fill('已保存的设备连接', config.connection_id);
  await until(() => observed.get(BASE + '/' + config.connection_id)?.id === config.connection_id && ui.field('本次操作说明') && inflight === 0, 'actual_fixed_connection_not_displayed');
  await until(() => ui.container.querySelector('[aria-label="设备业务能力登记"]') && observed.has(BASE + '/' + config.connection_id + '/tools') && inflight === 0, 'actual_tool_panel_not_read');
  return observed.get(BASE + '/' + config.connection_id);
}
function verifyIssued(receipt, {requireCurrent}) {
  const current = observed.get(BASE + '/' + config.connection_id), inventory = observed.get(BASE + '/' + config.connection_id + '/tools');
  need(current && Array.isArray(inventory?.tools), 'fresh_configuration_or_tools_missing');
  const tool = inventory.tools.find(row => row.id === receipt.capability.id);
  need(tool && canonical(capability(tool)) === canonical(capability(receipt.capability)) && tool.connection_id === config.connection_id
    && tool.integrity_valid === true && typeof tool.configuration_current === 'boolean'
    && tool.action === 'read_telemetry' && tool.device_external_id === receipt.grant.device_external_id, 'actual_independent_issuance_mismatch');
  if (requireCurrent) need(tool.configuration_current === true && tool.status === 'published' && tool.availability_reason === 'current', 'new_registration_not_current');
  report.connection_id = config.connection_id; report.discovery_id = config.discovery_id; report.grant_id = receipt.grant.id;
  report.request_id = receipt.request_id; report.capability_id = tool.id; report.capability_hash = tool.hash;
  report.configuration_hash = hash(configurationPin(current)); report.tool_inventory_hash = hash(inventory); report.receipt_hash = hash(receipt);
  return tool;
}
async function runRegistration() {
  await mount(1, async ui => {
    const row = await selectConnection(ui), job = row.latest_discovery;
    need(code.discoveryIsCurrentV268(row, job) && job.id === config.discovery_id, 'actual_discovery_not_current');
    const matches = row.grants.filter(grant => grant.device_external_id === 'sim-weather-01' && grant.effective === true && !grant.integrity_error && grant.actions.includes('read_telemetry'));
    need(matches.length === 1, 'one_actual_weather_read_grant_required'); baseline = {connection: copy(row), grant: copy(matches[0])};
    need(!observed.get(BASE + '/' + config.connection_id + '/tools').tools.some(tool => tool.configuration_current && tool.integrity_valid && tool.status === 'published' && tool.device_external_id === 'sim-weather-01' && tool.action === 'read_telemetry'), 'weather_read_tool_already_registered');
    await ui.fill('本次操作说明', '将当前已授权气象读取加入业务能力，保留原申请及独立登记依据。');
    await ui.click('加入业务能力：读取测量数据');
    await until(() => report.counts.intentional_response_loss === 1 && ui.button('核对原申请') && !ui.button('核对原申请').matches(':disabled') && inflight === 0, 'actual_200_loss_not_retained_by_component');
    const pending = checkedPending(storage.getItem(prefix() + 'pending'));
    need(firstReceipt && pending.request_id === firstReceipt.request_id && pending.attempted && report.counts.post === 1, 'actual_original_not_durable_after_loss');
    checkpoint('actual_registration_200_withheld_original_on_disk');
  });
  // New React tree/session/client with the SAME durable FileStorage envelope.
  storage = new FileStorage(config.local_state_path, scope);
  await mount(2, async ui => {
    await until(() => ui.button('核对原申请') && !ui.button('核对原申请').matches(':disabled'), 'remounted_original_recovery_missing');
    await ui.click('核对原申请');
    await until(() => !ui.container.querySelector('[aria-label="核对原申请"]') && ui.field('本次操作说明') && inflight === 0, 'original_get_recovery_not_settled');
    await selectConnection(ui);
    const archiveRaw = storage.getItem(prefix() + 'receipt:' + firstReceipt.request_id);
    need(archiveRaw && canonical(JSON.parse(archiveRaw)) === canonical(firstReceipt) && storage.getItem(prefix() + 'pending') === null, 'actual_original_archive_not_readback');
    const lookup = observed.get(BASE + '/requests/' + firstReceipt.request_id);
    need(lookup?.found === true && canonical(lookup.result) === canonical(firstReceipt), 'actual_original_lookup_not_same_receipt');
    verifyIssued(firstReceipt, {requireCurrent: true});
    need(canonical(configurationPin(observed.get(BASE + '/' + config.connection_id))) === canonical(configurationPin(baseline.connection)), 'source_configuration_or_grant_changed');
    need(ui.container.textContent.includes('已加入业务能力') && report.counts.post === 1 && report.counts.register_tool_v269 === 1 && report.counts.original_lookup >= 1, 'formal_tool_registration_not_independently_confirmed');
    checkpoint('formal_component_get_recovery_and_independent_tool_inventory');
  });
}
async function runRead() {
  await mount(1, async ui => {
    const pendingRaw = storage.getItem(prefix() + 'pending');
    if (pendingRaw !== null) {
      checkedPending(pendingRaw); await ui.click('核对原申请');
      await until(() => ui.container.textContent.includes('只读核对到') && inflight === 0, 'readonly_original_not_checked');
      need(storage.getItem(prefix() + 'pending') === pendingRaw, 'readonly_original_was_cleared');
    }
    const row = await selectConnection(ui); code.checkedConnectionV268(row);
    // An absent V269 anchor means only current actual GETs are reported. It
    // never creates an imaginary nonce from a missing marker or old V268 POST.
    const anchorRaw = storage.getItem(ANCHOR);
    if (anchorRaw !== null) {
      const anchor = JSON.parse(anchorRaw); need(anchor.schema === 'device-gateway-registration-anchor-v269' && anchor.connection_id === config.connection_id && anchor.discovery_id === config.discovery_id && uuid(anchor.request_id), 'saved_registration_anchor_invalid');
      ownNonces.add(anchor.request_id); const lookup = await api(BASE + '/requests/' + anchor.request_id);
      need(lookup.found === true && hash(lookup.result) === anchor.receipt_hash && canonical(capability(lookup.result.capability)) === canonical(anchor.capability), 'saved_registration_not_independently_recovered');
      verifyIssued(lookup.result, {requireCurrent: false});
      const archive = storage.getItem(prefix() + 'receipt:' + anchor.request_id);
      if (archive !== null) need(canonical(JSON.parse(archive)) === canonical(lookup.result), 'saved_registration_archive_changed');
    } else {
      report.connection_id = config.connection_id; report.discovery_id = config.discovery_id;
      report.configuration_hash = hash(configurationPin(row)); report.tool_inventory_hash = hash(observed.get(BASE + '/' + config.connection_id + '/tools'));
    }
    need(report.counts.post === 0, 'read_phase_attempted_post'); checkpoint('formal_component_fresh_configuration_and_tools_get_only');
  });
}
try {
  need(process.env.NODE_ENV === 'development', 'development_react_required'); config = await loadConfig(); report.phase = config.phase;
  scope = {actor_id: config.account.user_id, workspace_id: config.account.workspace_id};
  storage = new FileStorage(config.local_state_path, scope); // Existing V268 journal only; never fabricate one.
  const pendingRaw = storage.getItem(prefix() + 'pending');
  if (pendingRaw !== null) checkedPending(pendingRaw);
  if (config.phase === 'register-tool-v269') {
    need(storage.getItem(CONSUMED) === null && pendingRaw === null && storage.getItem(ANCHOR) === null, 'registration_write_phase_consumed_or_original_pending');
    storage.setItem(CONSUMED, JSON.stringify({phase: 'register-tool-v269', consumed: true, verification_id: report.verification_id, maximum_posts: 1}));
    await runRegistration();
  } else await runRead();
  need(report.mounts.every(row => row.verified && row.polling_stopped && row.react_errors === 0 && row.console_warnings === 0)
    && report.counts.denied === 0 && report.counts.xhr === 0 && report.counts.navigation === 0 && report.counts.media === 0, 'verification_boundary_failed');
  need(config.phase === 'read' ? report.counts.post === 0 : report.counts.post === 1 && report.counts.intentional_response_loss === 1, 'phase_final_post_budget_failed');
  safeReport(); report.ok = true;
} catch (error) { report.errors.push({code: error.safeCode || 'verification_failed'}); process.exitCode = 1; }
finally {
  if (temporary) await rm(temporary, {recursive: true, force: true}).catch(() => { report.ok = false; report.errors.push({code: 'temporary_bundle_cleanup_failed'}); process.exitCode = 1; });
  if (config?.output_path) try { safeReport(); const output = await open(config.output_path, 'wx', 0o600); try { await output.writeFile(JSON.stringify(report, null, 2) + '\n'); await output.sync(); } finally { await output.close(); } } catch { report.ok = false; report.errors.push({code: 'private_safe_report_write_failed'}); process.exitCode = 1; }
  process.stdout.write(JSON.stringify({ok: report.ok, phase: report.phase, counts: report.counts, connection_id: report.connection_id ?? null,
    discovery_id: report.discovery_id ?? null, grant_id: report.grant_id ?? null, request_id: report.request_id ?? null,
    capability_id: report.capability_id ?? null, capability_hash: report.capability_hash ?? null,
    native_browser: false, node_lock_adapter: true, file_storage_adapter: true,
    failure_code: report.ok ? null : report.errors[0]?.code || 'verification_failed'}) + '\n');
}
