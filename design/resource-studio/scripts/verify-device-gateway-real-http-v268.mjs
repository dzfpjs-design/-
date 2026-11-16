// Root-only: formal ReactDOM component + real same-origin JSON HTTP.
// This is NOT a native browser, browser WebLocks, layout, or field-device check.
// NODE_ENV=development node scripts/verify-device-gateway-real-http-v268.mjs /absolute/0600-config.json
// Config: {origin,cookie,csrf_token,account:{user_id,workspace_id,role,username},
// device_origin,device_token,local_state_path,output_path,
// phase:'create-discover'|'grant'|'read',connection_id?,discovery_id?,timeout_ms?}
// Never copy credentials from that config into an evidence report or console.
import React, {act} from 'react';
import {build} from 'vite';
import {JSDOM, ResourceLoader, VirtualConsole} from 'jsdom';
import {lstat, readFile, open, mkdtemp, writeFile, rm} from 'node:fs/promises';
import {openSync, closeSync, fsyncSync, writeFileSync, readFileSync, lstatSync, renameSync, unlinkSync} from 'node:fs';
import {dirname, resolve, join, isAbsolute} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto, randomUUID} from 'node:crypto';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(project, 'package.json')), reactPath = require.resolve('react'), reactURL = pathToFileURL(reactPath).href;
const nativeFetch = globalThis.fetch, nativeInterval = globalThis.setInterval, nativeClear = globalThis.clearInterval;
const BASE = '/workspace/device-gateways', baseWire = '/api' + BASE;
const copy = value => JSON.parse(JSON.stringify(value));
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const canonical = value => JSON.stringify((function sort(item) { return Array.isArray(item) ? item.map(sort) : plain(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, sort(item[key])])) : item; })(value));
const exact = (value, fields) => plain(value) && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key));
const sleep = milliseconds => new Promise(resolveSleep => setTimeout(resolveSleep, milliseconds));
function need(value, safeCode) { if (!value) throw Object.assign(Error(safeCode), {safeCode}); }
const report = {schema: 'device-gateway-real-http-verification-v268', verification_id: randomUUID(), ok: false, phase: null, mounts: [], counts: {get: 0, post: 0, create: 0, discover: 0, grant: 0, original_lookup: 0, intentional_response_loss: 0, denied: 0, xhr: 0, navigation: 0, media: 0}, checkpoints: [], errors: [], native_browser: false, native_web_locks_verified: false, native_storage_verified: false, file_storage_adapter: true, node_lock_adapter: true, physical_device_verified: false, device_execution_verified: false};
let config, configPath, storage, scope, code, temporary, mountNumber = 0, inflight = 0;
const observed = new Map(), allowedConnections = new Set(), allowedDiscoveries = new Set(), generatedRequests = new Set(), actualReceipts = new Map();
function checkpoint(name) { report.checkpoints.push(name); process.stdout.write(JSON.stringify({checkpoint: name, gets: report.counts.get, posts: report.counts.post}) + '\n'); }
function containsCredential(value) { const raw = typeof value === 'string' ? value : JSON.stringify(value); return [config?.cookie, config?.csrf_token, config?.device_token].some(secret => typeof secret === 'string' && secret.length > 0 && raw.includes(secret)); }
function safeConnection(row) {
  return {id: row.id, status: row.status, version: row.version, credential_revision: row.credential_revision, credential_configured: row.credential_configured === true,
    discovery: row.latest_discovery ? safeDiscovery(row.latest_discovery) : null,
    grants: (row.grants || []).map(grant => ({id: grant.id, device_external_id: grant.device_external_id, actions: [...grant.actions], discovery_job_id: grant.discovery_job_id, connection_version: grant.connection_version, credential_revision: grant.credential_revision, effective: grant.effective}))};
}
function safeDiscovery(job) { return {id: job.id, connection_id: job.connection_id, status: job.status, connection_version: job.connection_version, credential_revision: job.credential_revision, effective: job.effective, integrity_error: job.integrity_error ?? null, devices: job.devices.map(device => ({external_id: device.external_id, kind: device.kind, actions: [...device.actions], simulation: device.simulation, field_verified: device.field_verified, physical_device_connected: device.physical_device_connected}))}; }
class Memory {
  constructor() { this.rows = new Map(); }
  get length() { return this.rows.size; } key(index) { return [...this.rows.keys()][index] ?? null; }
  getItem(key) { return this.rows.get(key) ?? null; } setItem(key, value) { this.rows.set(key, String(value)); } removeItem(key) { this.rows.delete(key); }
}
class FileStorage extends Memory {
  constructor(path, owner, {create = false} = {}) {
    super(); this.path = path; this.scope = copy(owner);
    if (create) { const fd = openSync(path, 'wx', 0o600); try { writeFileSync(fd, JSON.stringify({schema: 'device-gateway-ui-file-storage-v268', scope: this.scope, entries: []}) + '\n'); fsyncSync(fd); } finally { closeSync(fd); } }
    this.read();
  }
  read() {
    const stat = lstatSync(this.path); need(stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.size <= 8 * 1024 * 1024, 'file_storage_private_file_invalid');
    const raw = readFileSync(this.path, 'utf8'); need(!containsCredential(raw), 'credential_found_in_file_storage'); const record = JSON.parse(raw);
    need(exact(record, ['schema', 'scope', 'entries']) && record.schema === 'device-gateway-ui-file-storage-v268' && canonical(record.scope) === canonical(this.scope) && Array.isArray(record.entries) && record.entries.every(pair => Array.isArray(pair) && pair.length === 2 && pair.every(value => typeof value === 'string')) && new Set(record.entries.map(pair => pair[0])).size === record.entries.length, 'file_storage_scope_or_entries_invalid'); this.rows = new Map(record.entries);
  }
  commit(next) {
    const raw = JSON.stringify({schema: 'device-gateway-ui-file-storage-v268', scope: this.scope, entries: [...next]}) + '\n'; need(!containsCredential(raw), 'credential_write_to_file_storage_blocked');
    const part = this.path + '.' + randomUUID() + '.part', fd = openSync(part, 'wx', 0o600);
    try { writeFileSync(fd, raw); fsyncSync(fd); } finally { closeSync(fd); }
    try { renameSync(part, this.path); const directory = openSync(dirname(this.path), 'r'); try { fsyncSync(directory); } finally { closeSync(directory); } need(readFileSync(this.path, 'utf8') === raw, 'file_storage_readback_failed'); this.rows = new Map(next); }
    finally { try { unlinkSync(part); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
  }
  get length() { this.read(); return this.rows.size; } key(index) { this.read(); return [...this.rows.keys()][index] ?? null; }
  getItem(key) { this.read(); return this.rows.get(key) ?? null; }
  setItem(key, value) { this.read(); const next = new Map(this.rows); next.set(key, String(value)); this.commit(next); }
  removeItem(key) { this.read(); const next = new Map(this.rows); next.delete(key); this.commit(next); }
}
// An in-process Node adapter, deliberately not represented as native WebLocks.
function lockAdapter() { const tails = new Map(); return {request(key, _options, work) { const next = (tails.get(key) || Promise.resolve()).catch(() => {}).then(work); tails.set(key, next); return next; }}; }
async function loadConfig() {
  configPath = process.argv[2]; need(typeof configPath === 'string' && isAbsolute(configPath), 'absolute_private_config_required');
  const stat = await lstat(configPath); need(stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.size <= 128 * 1024, 'private_config_0600_required');
  const value = JSON.parse(await readFile(configPath, 'utf8'));
  const required = ['origin', 'cookie', 'csrf_token', 'account', 'device_origin', 'device_token', 'local_state_path', 'output_path', 'phase'], optional = ['connection_id', 'discovery_id', 'timeout_ms'];
  need(plain(value) && required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => required.includes(key) || optional.includes(key)), 'config_shape_invalid');
  const origin = new URL(value.origin); need(origin.origin === value.origin && ['http:', 'https:'].includes(origin.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) && !origin.username && !origin.password, 'existing_loopback_server_required');
  need(exact(value.account, ['user_id', 'workspace_id', 'role', 'username']) && uuid(value.account.user_id) && uuid(value.account.workspace_id) && ['admin', 'operator', 'viewer'].includes(value.account.role) && typeof value.account.username === 'string', 'account_scope_invalid');
  need(['cookie', 'csrf_token'].every(key => typeof value[key] === 'string' && value[key].length > 0 && !/[\r\n\x00]/.test(value[key])), 'session_invalid');
  need(['create-discover', 'grant', 'read'].includes(value.phase) && (value.phase === 'read' || value.account.role === 'admin'), 'phase_or_write_role_invalid');
  need(typeof value.device_token === 'string' && /^[!-~]{8,4096}$/.test(value.device_token), 'device_credential_shape_invalid');
  const deviceOrigin = new URL(value.device_origin); need(deviceOrigin.origin === value.device_origin && ['http:', 'https:'].includes(deviceOrigin.protocol) && !deviceOrigin.username && !deviceOrigin.password && !deviceOrigin.search && !deviceOrigin.hash, 'exact_device_origin_required');
  for (const key of ['connection_id', 'discovery_id']) need(value[key] === undefined || uuid(value[key]), 'configured_record_id_invalid');
  need(['local_state_path', 'output_path'].every(key => typeof value[key] === 'string' && isAbsolute(value[key])) && new Set([configPath, value.local_state_path, value.output_path].map(path => resolve(path))).size === 3, 'independent_private_paths_required');
  try { await lstat(value.output_path); need(false, 'evidence_output_already_exists'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  need(value.timeout_ms === undefined || Number.isSafeInteger(value.timeout_ms) && value.timeout_ms >= 10000 && value.timeout_ms <= 120000, 'timeout_invalid');
  return {...value, timeout_ms: value.timeout_ms ?? 45000};
}
function checkedRoute(path, method) {
  need(typeof path === 'string' && !/[?#\r\n\x00]/.test(path), 'relative_exact_api_path_required');
  if (path === '/me') { need(method === 'GET', 'identity_write_forbidden'); return {wire: '/api/me', operation: 'identity'}; }
  if (path === BASE) return {wire: baseWire, operation: method === 'POST' ? 'create' : 'list'};
  const request = path.match(/^\/workspace\/device-gateways\/requests\/([0-9a-f-]+)$/i);
  if (request) { need(method === 'GET' && uuid(request[1]) && generatedRequests.has(request[1]), 'unapproved_original_lookup'); return {wire: '/api' + path, operation: 'original_lookup', request_id: request[1]}; }
  const route = path.match(/^\/workspace\/device-gateways\/([0-9a-f-]+)(?:\/(discover|grant|status|credential)|\/discoveries\/([0-9a-f-]+))?$/i);
  need(route && uuid(route[1]) && allowedConnections.has(route[1]), 'unapproved_connection_route');
  if (route[3]) need(method === 'GET' && uuid(route[3]) && allowedDiscoveries.has(route[3]), 'discovery_get_only');
  else if (route[2]) need(method === 'POST', 'command_post_only');
  else need(method === 'GET', 'connection_get_only');
  return {wire: '/api' + path, operation: route[2] || (route[3] ? 'discovery_read' : 'connection_read'), connection_id: route[1], discovery_id: route[3] || null};
}
function pendingOriginal(body, operation, wireBody) {
  storage.read(); const prefix = code.deviceGatewayPrefixV268(scope), raw = storage.getItem(prefix + 'pending'); need(raw !== null, 'prepost_original_missing');
  const original = JSON.parse(raw); code.assertSafeGatewayDataV268(original);
  need(original.schema === 'device-gateway-original-v268' && canonical(original.scope) === canonical(scope) && original.operation === operation && original.request_id === body.request_id && original.attempted === true && !Object.hasOwn(original.meta, 'token'), 'prepost_safe_original_mismatch');
  const metadata = {...body}; delete metadata.token; need(canonical(metadata) === canonical(original.meta), 'prepost_metadata_does_not_match_wire');
  need(['create', 'credential'].includes(operation) ? original.raw === null : original.raw === wireBody, 'prepost_exact_original_bytes_mismatch');
  need(storage.getItem(prefix + 'pending') === raw, 'prepost_original_readback_changed'); generatedRequests.add(body.request_id); return original;
}
async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase(); need(['GET', 'POST'].includes(method), 'http_method_forbidden'); const route = checkedRoute(path, method);
  const headers = new Headers(options.headers); need(!headers.has('Cookie') && (!headers.has('X-Workspace-ID') || headers.get('X-Workspace-ID') === config.account.workspace_id) && (!headers.has('X-CSRF-Token') || headers.get('X-CSRF-Token') === config.csrf_token), 'component_identity_headers_changed');
  let body = null;
  if (method === 'POST') {
    const maxPosts = config.phase === 'create-discover' ? 2 : config.phase === 'grant' ? 1 : 0;
    need(report.counts.post < maxPosts && typeof options.body === 'string', 'phase_post_budget_exceeded'); body = JSON.parse(options.body); need(uuid(body.request_id), 'component_request_id_invalid');
    const marker = 'harness:post:' + body.request_id; need(storage.getItem(marker) === null, 'duplicate_component_post_blocked');
    if (route.operation === 'create') need(config.phase === 'create-discover' && mountNumber === 1 && report.counts.create === 0 && exact(body, ['request_id', 'name', 'origin', 'token']) && body.origin === config.device_origin && body.token === config.device_token, 'create_post_not_authorized');
    else if (route.operation === 'discover') need(config.phase === 'create-discover' && mountNumber === 2 && report.counts.create === 1 && report.counts.discover === 0 && exact(body, ['request_id', 'expected_version', 'reason']), 'discover_post_not_authorized');
    else if (route.operation === 'grant') need(config.phase === 'grant' && report.counts.grant === 0 && exact(body, ['request_id', 'expected_version', 'discovery_job_id', 'device_external_id', 'actions', 'reason']) && body.discovery_job_id === targetDiscoveryID() && body.device_external_id === 'sim-weather-01' && canonical([...body.actions].sort()) === canonical(['get_status', 'read_telemetry']), 'grant_post_not_authorized');
    else { report.counts.denied++; need(false, 'unapproved_gateway_write'); }
    pendingOriginal(body, route.operation, options.body); storage.setItem(marker, JSON.stringify({request_id: body.request_id, operation: route.operation, consumed: true})); report.counts.post++; report.counts[route.operation]++;
    headers.set('Content-Type', 'application/json');
  } else { need(options.body === undefined || options.body === null, 'get_body_forbidden'); report.counts.get++; if (route.operation === 'original_lookup') report.counts.original_lookup++; }
  headers.set('Cookie', config.cookie); headers.set('X-CSRF-Token', config.csrf_token); headers.set('X-Workspace-ID', config.account.workspace_id); headers.set('Accept', 'application/json');
  const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(config.timeout_ms)]) : AbortSignal.timeout(config.timeout_ms); inflight++;
  try {
    const response = await nativeFetch(new URL(route.wire, config.origin), {...options, method, headers, credentials: 'same-origin', redirect: 'error', signal});
    need(response.headers.get('Content-Type')?.split(';')[0] === 'application/json', 'non_json_response_blocked');
    const value = await response.json(); need(!containsCredential(value), 'credential_in_server_response_blocked');
    if (!response.ok) { const error = Error('actual_json_http_failed'); error.status = response.status; throw error; }
    if (route.operation === 'identity') need(value.user_id === config.account.user_id && value.workspace_id === config.account.workspace_id && value.role === config.account.role, 'actual_identity_changed');
    if (method === 'POST') {
      need(response.status === 200 && value.request_id === body.request_id && value.operation === route.operation && uuid(value.connection_id), 'actual_post_receipt_invalid'); code.assertSafeGatewayDataV268(value); code.checkedConnectionV268(value.connection); allowedConnections.add(value.connection_id); actualReceipts.set(route.operation, copy(value));
      storage.setItem('harness:connection_id', value.connection_id);
      if (value.discovery) { code.checkedDiscoveryV268(value.discovery); allowedDiscoveries.add(value.discovery.id); storage.setItem('harness:discovery_id', value.discovery.id); }
      if (route.operation === 'create') { report.counts.intentional_response_loss++; throw Object.assign(Error('actual_saved_response_intentionally_withheld'), {safeCode: 'actual_saved_response_intentionally_withheld'}); }
    }
    if (route.operation === 'connection_read' && value.latest_discovery) { const expected = targetDiscoveryID(); need(!expected || value.latest_discovery.id === expected, 'actual_latest_discovery_differs_from_target'); allowedDiscoveries.add(value.latest_discovery.id); }
    observed.set(path, copy(value)); return value;
  } finally { inflight--; }
}
const targetConnectionID = () => config.connection_id || storage.getItem('harness:connection_id');
const targetDiscoveryID = () => config.discovery_id || storage.getItem('harness:discovery_id');
async function compile() {
  if (code) return code; temporary = await mkdtemp(join(tmpdir(), 'device-gateway-real-http-v268-')); const entry = join(temporary, 'entry.jsx');
  await writeFile(entry, 'export {default as Studio} from ' + JSON.stringify(join(project, 'src/DeviceGatewayStudioV268.jsx')) + ';export * from ' + JSON.stringify(join(project, 'src/device-gateway-ui-v268.mjs')) + ';');
  const result = await build({root: project, configFile: false, envDir: temporary, cacheDir: join(temporary, 'cache'), logLevel: 'silent', define: {'process.env.NODE_ENV': '"development"'}, build: {ssr: entry, write: false, minify: false, rollupOptions: {external: name => ['react', reactPath, reactURL].includes(name), output: {format: 'es', inlineDynamicImports: true, paths: name => ['react', reactPath, reactURL].includes(name) ? reactURL : name}}}});
  const chunk = result.output.find(row => row.type === 'chunk' && row.isEntry); need(chunk, 'formal_component_bundle_missing'); const file = join(temporary, 'formal-component.mjs'); await writeFile(file, chunk.code); code = await import(pathToFileURL(file).href); return code;
}
async function observe(work) { let result; await act(async () => { result = await work(); }); return result; }
async function until(predicate, safeCode) { const end = Date.now() + config.timeout_ms; while (Date.now() < end) { if (await observe(async () => { await sleep(25); return predicate(); })) return; } need(await observe(async () => predicate()), safeCode); }
async function mount(number, action) {
  mountNumber = number; const old = new Map(), intervals = new Set(), phase = {number, verified: false, polling_stopped: false, react_errors: 0, console_warnings: 0}; report.mounts.push(phase);
  const vc = new VirtualConsole(); vc.on('jsdomError', () => phase.react_errors++); class Offline extends ResourceLoader { fetch() { report.counts.denied++; throw Error('external_resource_forbidden'); } }
  const window = new JSDOM('<div id="root"></div>', {url: config.origin + '/setup', resources: new Offline(), virtualConsole: vc}).window, session = new Memory(), adapter = lockAdapter();
  session.setItem('ga-setup-csrf-user', config.account.user_id); session.setItem('ga-setup-csrf-workspace', config.account.workspace_id);
  let tracking = false;
  const timer = (fn, milliseconds, ...args) => { const handle = nativeInterval(fn, milliseconds, ...args); if (tracking) intervals.add(handle); return handle; }, clear = handle => { intervals.delete(handle); nativeClear(handle); };
  const denyFetch = () => { report.counts.denied++; throw Error('uninjected_fetch_forbidden'); };
  window.setInterval = timer; window.clearInterval = clear; window.fetch = denyFetch; window.confirm = () => { report.counts.denied++; throw Error('native_confirmation_forbidden'); }; window.open = () => { report.counts.navigation++; throw Error('navigation_forbidden'); };
  for (const method of ['pushState', 'replaceState', 'go', 'back', 'forward']) window.history[method] = () => { report.counts.navigation++; throw Error('navigation_forbidden'); };
  window.HTMLAnchorElement.prototype.click = function () { report.counts.navigation++; throw Error('navigation_forbidden'); }; window.HTMLMediaElement.prototype.play = function () { report.counts.media++; throw Error('media_forbidden'); };
  class NoXHR { constructor() { report.counts.xhr++; throw Error('xhr_forbidden'); } } window.XMLHttpRequest = NoXHR;
  class NoChannel { constructor() { report.counts.denied++; throw Error('unapproved_network_channel_forbidden'); } }
  window.WebSocket = NoChannel; window.EventSource = NoChannel; window.Worker = NoChannel; Object.defineProperty(window.navigator, 'sendBeacon', {value: denyFetch, configurable: true});
  Object.defineProperty(window.navigator, 'locks', {value: adapter, configurable: true});
  for (const [key, value] of Object.entries({window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, Event: window.Event, localStorage: storage, sessionStorage: session, crypto: globalThis.crypto || webcrypto, fetch: denyFetch, XMLHttpRequest: NoXHR, WebSocket: NoChannel, EventSource: NoChannel, Worker: NoChannel, IS_REACT_ACT_ENVIRONMENT: true, setInterval: timer, clearInterval: clear})) { old.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, {value, writable: true, configurable: true}); }
  const oldError = console.error, oldWarn = console.warn; console.error = () => phase.console_warnings++; console.warn = () => phase.console_warnings++;
  let root, container, guard;
  try {
    const actual = await compile(), {createRoot} = await import('react-dom/client'); container = document.getElementById('root'); root = createRoot(container, {onUncaughtError: () => phase.react_errors++, onCaughtError: () => phase.react_errors++, onRecoverableError: () => phase.react_errors++});
    const Btn = ({children, primary, ghost, icon, ...rest}) => React.createElement('button', {type: 'button', ...rest}, children);
    tracking = true; await act(async () => root.render(React.createElement(actual.Studio, {account: config.account, api, Btn, onRegisterGuard: fn => { guard = fn; }})));
    const button = label => [...container.querySelectorAll('button')].find(node => node.textContent === label);
    const field = label => { const row = [...container.querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label); return row?.querySelector('input,select,textarea'); };
    const fill = async (label, value) => { await until(() => field(label) && !field(label).matches(':disabled'), 'actual_field_unavailable'); await act(async () => { const node = field(label), prototype = node.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value); node.dispatchEvent(new window.Event(node.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); if (node.tagName !== 'SELECT') node.dispatchEvent(new window.Event('change', {bubbles: true})); }); };
    const click = async label => { await until(() => button(label) && !button(label).matches(':disabled'), 'actual_button_unavailable'); await act(async () => button(label).click()); };
    await until(() => field('已保存的设备连接') && button('刷新连接清单') && !button('刷新连接清单').matches(':disabled') && inflight === 0, 'formal_component_connection_list_not_ready');
    await action({window, container, button, field, fill, click, guard: () => guard});
    need(!container.querySelector('pre') && !container.querySelector('[contenteditable="true"]'), 'raw_debug_editor_present');
    need(container.textContent.includes('采购参数测试设备') && container.textContent.includes('没有经过现场身份核验'), 'simulation_boundary_missing');
    need(typeof guard === 'function' && guard() === true, 'durable_component_leave_guard_failed');
    need(phase.react_errors === 0 && phase.console_warnings === 0, 'reactdom_errors_or_warnings'); phase.verified = true;
  } finally {
    try {
      if (root) await act(async () => root.unmount()); await until(() => inflight === 0, 'unmount_http_not_drained'); need(intervals.size === 0, 'component_poll_interval_not_cleared'); const calls = report.counts.get + report.counts.post; await sleep(3200); need(intervals.size === 0 && inflight === 0 && report.counts.get + report.counts.post === calls, 'component_polled_after_unmount'); phase.polling_stopped = true;
    } finally { for (const handle of intervals) nativeClear(handle); console.error = oldError; console.warn = oldWarn; window.close(); for (const [key, descriptor] of old) if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
}
async function selectConnection(ui) { const id = targetConnectionID(); need(uuid(id), 'target_connection_id_missing'); if (ui.field('已保存的设备连接').value !== id) await ui.fill('已保存的设备连接', id); await until(() => observed.get(BASE + '/' + id)?.id === id && ui.field('本次操作说明') && inflight === 0, 'actual_saved_connection_not_displayed'); return observed.get(BASE + '/' + id); }
async function runCreateDiscover() {
  await mount(1, async ui => {
    await ui.fill('连接名称', '采购参数测试接入 V268'); await ui.fill('服务地址', config.device_origin); await ui.fill('访问令牌', config.device_token); await ui.click('保存测试连接');
    await until(() => report.counts.intentional_response_loss === 1 && ui.container.querySelector('[aria-label="核对原申请"]') && ui.button('核对原申请') && !ui.button('核对原申请').matches(':disabled') && inflight === 0, 'actual_200_loss_not_retained_by_component');
    need(report.counts.create === 1 && report.counts.post === 1 && [...ui.container.querySelectorAll('input[type="password"]')].every(input => input.value === ''), 'create_budget_or_memory_credential_not_cleared');
    const raw = storage.getItem(code.deviceGatewayPrefixV268(scope) + 'pending'); need(raw && JSON.parse(raw).request_id === actualReceipts.get('create').request_id, 'actual_create_original_not_on_disk'); report.create_request_id = JSON.parse(raw).request_id; checkpoint('actual_create_200_discarded_original_on_disk');
  });
  storage = new FileStorage(config.local_state_path, scope);
  await mount(2, async ui => {
    await until(() => ui.button('核对原申请') && !ui.button('核对原申请').matches(':disabled'), 'remounted_original_recovery_missing'); await ui.click('核对原申请');
    await until(() => !ui.container.querySelector('[aria-label="核对原申请"]') && ui.field('本次操作说明') && inflight === 0, 'actual_original_get_recovery_not_settled');
    need(report.counts.create === 1 && report.counts.post === 1 && report.counts.original_lookup >= 1 && storage.getItem(code.deviceGatewayPrefixV268(scope) + 'pending') === null, 'recovery_repeated_create_or_lost_original');
    await ui.fill('本次操作说明', '核对采购参数测试设备声明，保留现场未核验边界。'); await ui.click('测试连接并读取设备声明');
    await until(() => actualReceipts.get('discover')?.discovery && !ui.container.querySelector('[aria-label="核对原申请"]') && inflight === 0, 'actual_discovery_receipt_not_readback');
    const receipt = actualReceipts.get('discover'); report.connection_id = receipt.connection_id; report.discovery_id = receipt.discovery.id; report.discovery_request_id = receipt.request_id; report.discovery_enqueue_status = receipt.discovery.status; report.connection = safeConnection(observed.get(BASE + '/' + receipt.connection_id));
    need(report.counts.post === 2 && report.counts.create === 1 && report.counts.discover === 1 && report.counts.grant === 0, 'create_discover_exact_post_counts_failed'); checkpoint('formal_ui_original_recovered_then_discovery_queued');
  });
}
async function runGrant() {
  await mount(1, async ui => {
    const row = await selectConnection(ui), job = row.latest_discovery; need(job?.id === targetDiscoveryID() && job.status === 'succeeded' && code.discoveryIsCurrentV268(row, job) && job.devices.some(device => device.external_id === 'sim-weather-01'), 'actual_target_weather_discovery_not_current');
    await ui.fill('本次操作说明', '允许采购参数气象测试设备读取状态和测量数据，不批准设备执行。'); await ui.fill('选择设备声明', 'sim-weather-01');
    for (const label of ['读取状态', '读取测量数据']) {
      const checkbox = () => [...ui.container.querySelectorAll('label')].find(node => node.textContent === label)?.querySelector('input[type="checkbox"]'); await until(() => checkbox() && !checkbox().matches(':disabled'), 'actual_weather_read_checkbox_missing'); if (!checkbox().checked) await act(async () => checkbox().click());
    }
    need(![...ui.container.querySelectorAll('legend')].some(node => node.textContent === '允许控制能力'), 'weather_control_authorization_exposed'); await ui.click('保存此设备的网关授权');
    await until(() => actualReceipts.get('grant')?.grant && !ui.container.querySelector('[aria-label="核对原申请"]') && inflight === 0, 'actual_weather_grant_not_readback');
    const receipt = actualReceipts.get('grant'), current = observed.get(BASE + '/' + receipt.connection_id); need(current.grants.some(grant => grant.id === receipt.grant.id && grant.effective === true) && ui.container.textContent.includes('当前网关授权'), 'actual_weather_grant_not_in_connection_ui');
    report.connection_id = receipt.connection_id; report.discovery_id = receipt.grant.discovery_job_id; report.grant_id = receipt.grant.id; report.grant_request_id = receipt.request_id; report.connection = safeConnection(current); need(report.counts.post === 1 && report.counts.grant === 1, 'grant_exact_post_count_failed'); checkpoint('formal_ui_weather_read_grant_saved_and_readback');
  });
}
async function runRead() {
  await mount(1, async ui => {
    if (ui.button('核对原申请')) { await ui.click('核对原申请'); await until(() => !ui.container.querySelector('[aria-label="核对原申请"]') || ui.container.textContent.includes('只读核对到'), 'read_phase_original_not_checked'); }
    const row = await selectConnection(ui); if (targetDiscoveryID()) need(row.latest_discovery?.id === targetDiscoveryID(), 'persisted_discovery_id_changed');
    code.checkedConnectionV268(row); report.connection_id = row.id; report.discovery_id = row.latest_discovery?.id ?? null; report.connection = safeConnection(row); need(report.counts.post === 0, 'read_phase_attempted_post'); checkpoint('formal_ui_persisted_connection_read_get_only');
  });
}
try {
  need(process.env.NODE_ENV === 'development', 'development_react_required'); config = await loadConfig(); report.phase = config.phase; scope = {actor_id: config.account.user_id, workspace_id: config.account.workspace_id};
  let createFile = false; try { await lstat(config.local_state_path); } catch (error) { if (error.code !== 'ENOENT') throw error; need(config.phase === 'create-discover', 'existing_file_journal_required'); createFile = true; }
  storage = new FileStorage(config.local_state_path, scope, {create: createFile});
  const consumed = 'harness:consumed:' + config.phase;
  if (config.phase !== 'read') { need(storage.getItem(consumed) === null, 'write_phase_already_consumed_do_not_rerun'); storage.setItem(consumed, JSON.stringify({phase: config.phase, consumed: true, started_at: new Date().toISOString()})); }
  else if (storage.getItem(consumed) === null) storage.setItem(consumed, JSON.stringify({phase: config.phase, started_at: new Date().toISOString(), maximum_posts: 0}));
  for (const id of [config.connection_id, storage.getItem('harness:connection_id')]) if (id) { need(uuid(id), 'disk_connection_id_invalid'); allowedConnections.add(id); }
  for (const id of [config.discovery_id, storage.getItem('harness:discovery_id')]) if (id) { need(uuid(id), 'disk_discovery_id_invalid'); allowedDiscoveries.add(id); }
  for (const [key, raw] of storage.rows) if (key.endsWith(':pending')) { const original = JSON.parse(raw); need(uuid(original.request_id) && canonical(original.scope) === canonical(scope), 'disk_original_scope_invalid'); generatedRequests.add(original.request_id); }
  if (config.phase === 'create-discover') await runCreateDiscover(); else if (config.phase === 'grant') await runGrant(); else await runRead();
  need(report.mounts.every(mount => mount.verified && mount.polling_stopped && mount.react_errors === 0 && mount.console_warnings === 0) && report.counts.denied === 0 && report.counts.xhr === 0 && report.counts.navigation === 0 && report.counts.media === 0, 'verification_boundary_failed');
  need(!containsCredential(report) && !JSON.stringify(report).includes(config.origin) && !JSON.stringify(report).includes(config.device_origin), 'report_sensitive_value_blocked'); report.ok = true;
} catch (error) { report.errors.push({code: error.safeCode || 'verification_failed'}); process.exitCode = 1; }
finally {
  if (temporary) await rm(temporary, {recursive: true, force: true}).catch(() => { report.ok = false; report.errors.push({code: 'temporary_bundle_cleanup_failed'}); process.exitCode = 1; });
  if (config?.output_path) try { need(!containsCredential(report) && !JSON.stringify(report).includes(config.origin) && !JSON.stringify(report).includes(config.device_origin), 'report_sensitive_value_blocked'); const output = await open(config.output_path, 'wx', 0o600); try { await output.writeFile(JSON.stringify(report, null, 2) + '\n'); await output.sync(); } finally { await output.close(); } } catch { report.ok = false; report.errors.push({code: 'private_safe_report_write_failed'}); process.exitCode = 1; }
  process.stdout.write(JSON.stringify({ok: report.ok, phase: report.phase, mounts: report.mounts.length, verified_mounts: report.mounts.filter(mount => mount.verified).length, counts: report.counts, connection_id: report.connection_id ?? null, discovery_id: report.discovery_id ?? null, grant_id: report.grant_id ?? null, native_browser: false, node_lock_adapter: true, file_storage_adapter: true, physical_device_verified: false, failure_code: report.ok ? null : report.errors[0]?.code || 'verification_failed'}) + '\n');
}
