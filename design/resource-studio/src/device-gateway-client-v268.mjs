import {DEVICE_GATEWAY_BASE_V268 as BASE, needV268 as need, isUUIDV268 as uuid, copyV268 as copy, sameV268 as same, deviceGatewayScopeV268, deviceGatewayPrefixV268, blankDeviceGatewayFormV268, checkedDeviceGatewayFormV268, safeServiceOriginV268, assertSafeGatewayDataV268, checkedConnectionV268, checkedDiscoveryV268, discoveryIsCurrentV268, gatewayErrorV268} from './device-gateway-ui-v268.mjs';

const operations = ['create', 'discover', 'grant', 'status', 'credential', 'register_tool_v269'];
const secretOperation = operation => ['create', 'credential'].includes(operation);
const operationPath = (operation, id) => operation === 'create' ? BASE : BASE + '/' + id + '/' + (operation === 'register_tool_v269' ? 'tools' : operation);
const checkToken = token => need(typeof token === 'string' && /^[!-~]{8,4096}$/.test(token), '访问令牌须为 8 至 4096 个可见 ASCII 字符，不含空格；请重新输入原令牌。');
function checkPending(value, scope) {
  assertSafeGatewayDataV268(value);
  need(value && Object.keys(value).sort().join('|') === 'attempted|connection_id|meta|operation|path|raw|request_id|schema|scope');
  need(value?.schema === 'device-gateway-original-v268' && same(value.scope, scope) && uuid(value.request_id) && operations.includes(value.operation) && typeof value.attempted === 'boolean');
  need(value.operation === 'create' ? value.connection_id === null : uuid(value.connection_id));
  need(value.path === operationPath(value.operation, value.connection_id) && value.meta?.request_id === value.request_id);
  const fields = {create: 'name|origin|request_id', discover: 'expected_version|reason|request_id', credential: 'expected_version|reason|request_id', status: 'expected_version|reason|request_id|status', grant: 'actions|device_external_id|discovery_job_id|expected_version|reason|request_id', register_tool_v269: 'action|expected_version|grant_expected_version|grant_id|reason|request_id'};
  need(Object.keys(value.meta).sort().join('|') === fields[value.operation]);
  need(secretOperation(value.operation) ? value.raw === null : typeof value.raw === 'string' && value.raw === JSON.stringify(value.meta));
  if (value.operation === 'create') { need(typeof value.meta.name === 'string' && !!value.meta.name.trim()); safeServiceOriginV268(value.meta.origin); }
  else need(Number.isSafeInteger(value.meta.expected_version) && value.meta.expected_version > 0 && typeof value.meta.reason === 'string' && !!value.meta.reason.trim());
  if (value.operation === 'status') need(['enabled', 'paused'].includes(value.meta.status));
  if (value.operation === 'grant') need(uuid(value.meta.discovery_job_id) && typeof value.meta.device_external_id === 'string' && !!value.meta.device_external_id && Array.isArray(value.meta.actions) && value.meta.actions.length > 0 && value.meta.actions.every(action => typeof action === 'string') && new Set(value.meta.actions).size === value.meta.actions.length);
  if (value.operation === 'register_tool_v269') need(uuid(value.meta.grant_id) && Number.isSafeInteger(value.meta.grant_expected_version) && value.meta.grant_expected_version > 0 && ['get_status', 'read_telemetry'].includes(value.meta.action));
  return copy(value);
}
function checkedReceipt(value, pending) {
  assertSafeGatewayDataV268(value);
  need(value?.request_id === pending.request_id && value.operation === pending.operation && uuid(value.connection_id));
  need(pending.connection_id === null || value.connection_id === pending.connection_id);
  const connection = checkedConnectionV268(value.connection); need(connection.id === value.connection_id);
  if (pending.operation === 'create') need(connection.name === pending.meta.name && connection.origin === pending.meta.origin);
  if (value.discovery) { checkedDiscoveryV268(value.discovery); need(value.discovery.connection_id === connection.id); }
  if (pending.operation === 'discover') need(!!value.discovery);
  if (pending.operation === 'grant') need(value.grant && value.grant.connection_id === connection.id && value.grant.device_external_id === pending.meta.device_external_id && value.grant.discovery_job_id === pending.meta.discovery_job_id && same([...value.grant.actions].sort(), [...pending.meta.actions].sort()));
  if (pending.operation === 'register_tool_v269') { const cap = value.capability; need(cap && uuid(cap.id) && /^[0-9a-f]{64}$/.test(cap.hash) && typeof cap.release_version === 'string' && cap.status === 'published' && value.grant?.id === pending.meta.grant_id && value.grant.version === pending.meta.grant_expected_version && value.grant.actions.includes(pending.meta.action)); }
  return copy(value);
}

export class DeviceGatewayClientV268 {
  constructor({account, api, storage = globalThis.localStorage, sessionStorage = globalThis.sessionStorage, locks = globalThis.navigator?.locks, randomUUID = () => crypto.randomUUID(), onChange = () => {}, isCurrent = () => true}) {
    this.account = copy(account); this.scope = deviceGatewayScopeV268(account); this.key = deviceGatewayPrefixV268(this.scope);
    Object.assign(this, {api, storage, sessionStorage, locks, randomUUID, onChange, isCurrent}); this.formOwner = randomUUID(); this.controllers = new Set();
    this.alive = true; this.busy = false; this.loading = true; this.pendingRecord = null; this.form = blankDeviceGatewayFormV268(); this.formSaved = true;
    this.toolReceipt = null; this.connections = []; this.detail = null; this.discovery = null; this.selectedID = ''; this.error = ''; this.notice = ''; this.hasMore = false; this.blocked = false;
  }
  writable() { return this.alive && !this.blocked && this.account.role === 'admin' && typeof this.locks?.request === 'function'; }
  fence() {
    const session = this.sessionStorage;
    const changed = !this.alive || !this.isCurrent() || (session && (session.getItem('ga-setup-csrf-user') !== this.scope.actor_id || session.getItem('ga-setup-csrf-workspace') !== this.scope.workspace_id));
    if (changed) { const error = Error('会话已变化'); error.code = 'DEVICE_SCOPE_CHANGED'; throw error; }
  }
  emit() { if (this.alive && this.isCurrent()) this.onChange(this.snapshot()); }
  snapshot() { return copy({connections: this.connections, detail: this.detail, discovery: this.discovery, selectedID: this.selectedID, form: this.form, pending: this.pendingRecord, writable: this.writable(), busy: this.busy, loading: this.loading, error: this.error, notice: this.notice, hasMore: this.hasMore, blocked: this.blocked, formSaved: this.formSaved, toolReceipt: this.toolReceipt}); }
  async identity(write = false) {
    this.fence(); const me = await this.transport('/me', {cache: 'no-store', headers: {'X-Workspace-ID': this.scope.workspace_id}}); this.fence();
    if (me?.user_id !== this.scope.actor_id || me?.workspace_id !== this.scope.workspace_id || me?.role !== this.account.role) { this.blocked = true; const error = Error('会话已变化'); error.code = 'DEVICE_SCOPE_CHANGED'; throw error; }
    if (write) need(this.writable(), '当前为只读。设备连接修改需要管理员权限和浏览器跨页面锁支持。');
  }
  async transport(path, options) { const controller = new AbortController(); this.controllers.add(controller); try { return await this.api(path, {...options, signal: controller.signal}); } finally { this.controllers.delete(controller); } }
  async request(path, options = {}) { await this.identity(options.method === 'POST'); const value = await this.transport(path, {...options, cache: 'no-store', headers: {...options.headers, 'X-Workspace-ID': this.scope.workspace_id}}); await this.identity(options.method === 'POST'); return value; }
  read(key) { const raw = this.storage.getItem(this.key + key); return raw === null ? null : JSON.parse(raw); }
  write(key, value) { assertSafeGatewayDataV268(value); const raw = JSON.stringify(value); this.storage.setItem(this.key + key, raw); need(this.storage.getItem(this.key + key) === raw, '原申请或编辑尚未可靠保存，未提交。'); }
  pending() { const value = this.read('pending'); return value === null ? null : checkPending(value, this.scope); }
  async locked(fn) { need(typeof this.locks?.request === 'function', '浏览器不支持跨页面锁，当前仅可读取；请在支持该能力的浏览器中修改。'); return this.locks.request(this.key + 'operation', {mode: 'exclusive'}, async () => { await this.identity(true); return fn(); }); }
  async act(fn, lock = false) {
    if (this.busy || !this.alive) return; this.busy = true; this.error = ''; this.notice = ''; this.emit();
    try { return await (lock ? this.locked(fn) : fn()); }
    catch (error) { if (this.alive && this.isCurrent()) { if (error.code === 'DEVICE_SCOPE_CHANGED') { this.blocked = true; this.connections = []; this.detail = null; this.discovery = null; } this.error = gatewayErrorV268(error); } throw error; }
    finally { this.busy = false; this.loading = false; this.emit(); }
  }
  async start() {
    return this.act(async () => {
      this.fence();
      try { const owner = this.sessionStorage?.getItem(this.key + 'form-owner'); if (owner) need(uuid(owner)); const saved = owner ? this.read('form:' + owner) : null; if (saved) { need(same(saved.scope, this.scope)); this.form = checkedDeviceGatewayFormV268(saved.form); } this.pendingRecord = this.pending(); }
      catch (error) { this.blocked = true; throw error; }
      await this.refreshList();
      if (this.pendingRecord) this.notice = '有一份原申请尚待核对。可先读取原申请结果，不会自动重发。';
    });
  }
  async refreshList() {
    const value = await this.request(BASE); assertSafeGatewayDataV268(value); need(Array.isArray(value?.connections));
    this.connections = value.connections.map(checkedConnectionV268); this.hasMore = value.has_more === true; this.pendingRecord = this.pending();
    if (this.selectedID && this.connections.some(row => row.id === this.selectedID)) await this.readDetail(this.selectedID);
  }
  refresh() { return this.act(() => this.refreshList()); }
  async readDetail(id) {
    need(uuid(id)); const connection = checkedConnectionV268(await this.request(BASE + '/' + id)); need(connection.id === id);
    this.selectedID = id; this.detail = connection; this.discovery = connection.latest_discovery ? checkedDiscoveryV268(connection.latest_discovery) : null;
  }
  select(id) { return this.act(async () => { need(!this.pendingRecord, '请先核对原申请，当前连接暂不能切换。'); if (!id) { this.selectedID = ''; this.detail = null; this.discovery = null; } else await this.readDetail(id); }); }
  edit(patch) {
    this.fence(); need(this.writable() && !this.busy && !this.pendingRecord, '请先核对原申请，或等待当前操作结束。');
    const form = checkedDeviceGatewayFormV268({...this.form, ...patch}); this.form = form; this.formSaved = false;
    try { need(uuid(this.formOwner) && !!this.sessionStorage, '当前页面无法保存独立编辑，未提交。'); this.write('form:' + this.formOwner, {scope: this.scope, form}); this.sessionStorage.setItem(this.key + 'form-owner', this.formOwner); need(this.sessionStorage.getItem(this.key + 'form-owner') === this.formOwner); this.formSaved = true; this.error = ''; } catch (error) { this.error = gatewayErrorV268(error); throw error; } finally { this.emit(); }
  }
  async stage(operation, meta, id = null) {
    need(!this.pending(), '另一页面已有原申请，请先重新读取并核对。'); this.fence();
    const request_id = this.randomUUID(), body = {request_id, ...copy(meta)};
    const pending = {schema: 'device-gateway-original-v268', scope: copy(this.scope), operation, connection_id: id, request_id, path: operationPath(operation, id), meta: body, raw: secretOperation(operation) ? null : JSON.stringify(body), attempted: false};
    checkPending(pending, this.scope); this.write('pending', pending); this.pendingRecord = pending; this.emit(); return pending;
  }
  async accept(pending, rawReceipt, {settle = true} = {}) {
    const receipt = checkedReceipt(rawReceipt, pending); let toolConfigurationCurrent = false;
    const current = checkedConnectionV268(await this.request(BASE + '/' + receipt.connection_id));
    need(current.id === receipt.connection_id && current.version >= receipt.connection.version, '保存结果尚未读回确认，原申请保留。');
    if (receipt.grant) need(current.grants.some(grant => grant.id === receipt.grant.id), '授权尚未从连接记录读回，原申请保留。');
    if (receipt.discovery) {
      const job = checkedDiscoveryV268(await this.request(BASE + '/' + receipt.connection_id + '/discoveries/' + receipt.discovery.id));
      need(job.id === receipt.discovery.id && job.connection_id === receipt.connection_id); this.discovery = job;
    } else this.discovery = current.latest_discovery ? checkedDiscoveryV268(current.latest_discovery) : null;
    if (pending.operation === 'register_tool_v269') { const tools = await this.request(BASE + '/' + receipt.connection_id + '/tools'); assertSafeGatewayDataV268(tools); const registered = tools?.tools?.find(row => row.id === receipt.capability.id); need(registered && registered.connection_id === receipt.connection_id && registered.hash === receipt.capability.hash && registered.release_version === receipt.capability.release_version && registered.integrity_valid === true && registered.action === pending.meta.action && registered.device_external_id === receipt.grant.device_external_id && typeof registered.configuration_current === 'boolean' && ['published','revoked'].includes(registered.status) && (!registered.configuration_current || registered.status === 'published'), '登记结果尚未独立读回，原申请保留。'); toolConfigurationCurrent = registered.configuration_current && registered.status === 'published'; }
    this.fence(); need(same(this.pending(), pending));
    if (!settle) {
      this.detail = current; this.selectedID = current.id;
      this.connections = this.connections.some(row => row.id === current.id) ? this.connections.map(row => row.id === current.id ? current : row) : [current, ...this.connections];
      this.notice = '已只读核对到原申请的保存回执和当前连接。原票继续保留；当前没有写权限或跨页面锁，待具备条件后再核对结清。';
      return receipt;
    }
    const archive = this.read('receipt:' + pending.request_id); if (archive) need(same(archive, receipt)); else this.write('receipt:' + pending.request_id, receipt);
    this.storage.removeItem(this.key + 'pending'); need(this.storage.getItem(this.key + 'pending') === null, '结果已保存，但本页原申请尚未结清，请再次核对。');
    this.pendingRecord = null; this.detail = current; this.selectedID = current.id;
    const old = this.connections.findIndex(row => row.id === current.id); if (old < 0) this.connections = [current, ...this.connections]; else this.connections = this.connections.map(row => row.id === current.id ? current : row);
    this.notice = pending.operation === 'create' ? '测试连接已保存并读回确认。连接已启用，尚未测试服务连通；请填写说明后读取设备声明。' : '操作已保存并读回确认。测试设备、现场身份与执行批准仍分别管理。';
    if (pending.operation === 'register_tool_v269') { this.toolReceipt = copy(receipt); this.notice = toolConfigurationCurrent ? '采样能力已登记并独立读回，可供业务流程选用；每次请求仍需业务审批。' : '原登记已保存并独立读回，当前授权已变化；请核对下方能力清单后重新登记。'; }
    return receipt;
  }
  async lookup(pending, {settle = true} = {}) {
    const value = await this.request(BASE + '/requests/' + pending.request_id); assertSafeGatewayDataV268(value);
    need(value && typeof value.found === 'boolean' && value.request_id === pending.request_id);
    if (!value.found) { need(value.result === null || value.result === undefined); this.notice = '尚未找到此原申请的保存回执；这不表示原操作一定没有发生。可稍后再次核对，或明确按同一原申请重试。'; return false; }
    await this.accept(pending, value.result, {settle}); return true;
  }
  recover() { return this.act(async () => {
    const read = async settle => { const pending = this.pending(); need(pending, '没有待核对的原申请。'); this.pendingRecord = pending; await this.lookup(pending, {settle}); };
    if (this.writable()) return this.locked(() => read(true));
    return read(false);
  }); }
  async send(pending, token = '') {
    this.fence(); need(same(this.pending(), pending));
    if (secretOperation(pending.operation)) checkToken(token);
    const attempted = {...pending, attempted: true}; this.write('pending', attempted); this.pendingRecord = attempted;
    const body = secretOperation(pending.operation) ? JSON.stringify({...pending.meta, token}) : pending.raw;
    // Never repeat a POST after an uncertain response. Recovery is GET-only until the person explicitly retries.
    const receipt = await this.request(pending.path, {method: 'POST', body});
    return this.accept(attempted, receipt);
  }
  retry(token = '') { return this.act(() => this.locked(async () => { const pending = this.pending(); need(pending); this.pendingRecord = pending; if (await this.lookup(pending)) return; await this.send(pending, token); })); }
  create(token) { return this.act(() => this.locked(async () => { need(this.formSaved, '编辑尚未可靠保存，请重试保存后继续。'); need(!!this.form.name.trim(), '请填写连接名称。'); safeServiceOriginV268(this.form.origin); checkToken(token); const pending = await this.stage('create', {name: this.form.name.trim(), origin: this.form.origin}); return this.send(pending, token); })); }
  command(operation, extra = {}, token = '') { return this.act(() => this.locked(async () => {
    need(['discover', 'grant', 'status', 'credential', 'register_tool_v269'].includes(operation) && this.detail && !this.pendingRecord); need(this.formSaved && !!this.form.reason.trim(), '请填写并保存本次操作说明。');
    const original = this.detail; const current = checkedConnectionV268(await this.request(BASE + '/' + original.id)); need(current.version === original.version && current.credential_revision === original.credential_revision, '连接已被其他页面更新，请刷新后重新核对。');
    if (['discover', 'grant'].includes(operation)) need(current.status === 'enabled', '连接已暂停，请先启用后再测试或授权。');
    if (operation === 'grant') { need(discoveryIsCurrentV268(current, this.discovery), '当前发现已过期，请重新测试并读取设备声明。'); const device = this.discovery.devices.find(row => row.external_id === extra.device_external_id); need(device && extra.actions?.length > 0 && extra.actions.every(action => device.actions.includes(action)) && new Set(extra.actions).size === extra.actions.length, '请选择此设备声明中允许的能力。'); need(extra.discovery_job_id === this.discovery.id); }
    if (operation === 'register_tool_v269') { const grant = current.grants.find(row => row.id === extra.grant_id); need(current.status === 'enabled' && grant?.effective === true && !grant.integrity_error && grant.version === extra.grant_expected_version && ['get_status', 'read_telemetry'].includes(extra.action) && grant.actions.includes(extra.action), '设备授权已变化，请刷新后核对可登记的采样能力。'); }
    if (operation === 'status') need(['enabled', 'paused'].includes(extra.status));
    if (operation === 'credential') checkToken(token);
    const pending = await this.stage(operation, {expected_version: current.version, ...copy(extra), reason: this.form.reason}, current.id); return this.send(pending, token);
  })); }
  pollDiscovery() { return this.act(async () => { const current = this.discovery, id = this.selectedID; if (!current || !['queued', 'running'].includes(current.status) || this.detail?.status !== 'enabled') return; const next = checkedDiscoveryV268(await this.request(BASE + '/' + id + '/discoveries/' + current.id)); if (this.selectedID === id && this.discovery?.id === current.id) { need(next.connection_id === id); this.discovery = next; } }); }
  async readTools() { const id = this.selectedID; need(uuid(id)); const value = await this.request(BASE + '/' + id + '/tools'); this.fence(); need(this.selectedID === id && Array.isArray(value?.tools) && typeof value.has_more === 'boolean'); assertSafeGatewayDataV268(value); return value; }
  canLeave() { if (this.busy || !this.formSaved) return false; try { this.fence(); const saved = this.pending(); return !this.pendingRecord || same(saved, this.pendingRecord); } catch { return false; } }
  dispose() { this.alive = false; this.onChange = () => {}; for (const controller of this.controllers) controller.abort(); this.controllers.clear(); }
}
