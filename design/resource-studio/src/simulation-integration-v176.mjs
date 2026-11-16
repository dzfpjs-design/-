// Display and exact-request recovery only. This module grants no equipment or
// workflow authority; every save is checked against the current server session.
export const SIMULATION_PREFIX = '/workspace/simulation-v176';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA = /^[a-f0-9]{64}$/;
const memories = new Map();
const need = (ok, message = '接入记录无法核对，请保留原请求并刷新。') => { if (!ok) throw Error(message); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const id = value => typeof value === 'string' && UUID.test(value);
const version = value => Number.isSafeInteger(value) && value >= 1;
const nullableID = value => value === null || id(value);
const nullableText = value => value === null || typeof value === 'string';
const copy = value => Array.isArray(value) ? value.map(copy) : object(value) ? Object.fromEntries(Object.entries(value).map(([key,child]) => [key,copy(child)])) : value;
const keys = (value, expected) => object(value) && Object.keys(value).length === expected.length && expected.every(key => own(value, key));
export function simulationMarkV176(value) {
  need(object(value) && value.simulation === true && value.origin_kind === 'simulated_equipment' && value.field_verified === false, '记录没有真实的模拟来源标记，未作为现场数据显示。');
  return value;
}
export function simulationScopeV176(value) {
  need(keys(value, ['actor_id','workspace_id','role','instance_id']) && id(value.actor_id) && id(value.workspace_id) && id(value.instance_id) && ['admin','operator','viewer'].includes(value.role), '当前账号、工作区或实例尚未明确。');
  return copy(value);
}
export function simulationScopeKeyV176(value) {
  const scope = simulationScopeV176(value);
  return 'simulation-integration-v176:' + [scope.workspace_id, scope.actor_id, scope.instance_id].join(':');
}
export function simulationIdentityKeyV176(value) { return simulationScopeKeyV176(value) + ':' + value.role; }
function environment(value) {
  simulationMarkV176(value);
  need(id(value.id) && version(value.version) && value.schema === 'simulation-environment-v176' && id(value.instance_id) && id(value.release_id) && typeof value.note === 'string');
  return value;
}
function definition(value) {
  need(object(value) && id(value.id) && version(value.version) && value.schema === 'simulation-definition-v176' && id(value.environment_id) && id(value.instance_id) && id(value.release_id) && typeof value.device_id === 'string' && Array.isArray(value.fields) && value.fields.length >= 1 && value.fields.length <= 4 && value.fields.every(x => typeof x === 'string') && typeof value.status === 'string' && nullableText(value.next_due) && nullableText(value.valid_until) && Number.isSafeInteger(value.interval_seconds) && value.interval_seconds >= 60);
  return value;
}
export function checkSimulationSampleV176(value) {
  simulationMarkV176(value);
  need(id(value.id) && version(value.version) && id(value.actor_id) && id(value.definition_id) && typeof value.status === 'string' && typeof value.created_at === 'string' && nullableText(value.received_at) && nullableText(value.issue) && nullableText(value.recovery_status) && nullableID(value.recovery_job_id));
  need((value.recovery_status === null) === (value.recovery_job_id === null), '原回执核对任务状态和编号不一致，请刷新原记录。');
  for (const key of ['invocation_id','observation_id','task_id','signal_id','receipt_evidence_id']) need(nullableID(value[key]));
  return value;
}
export function canRecoverSimulationSampleV176(sample, sourceScope) {
  checkSimulationSampleV176(sample); const scope = simulationScopeV176(sourceScope);
  return ['admin','operator'].includes(scope.role) && sample.actor_id === scope.actor_id
    && sample.status === 'awaiting_receipt' && sample.observation_id === null
    && (sample.recovery_status === null || ['succeeded','failed','cancelled'].includes(sample.recovery_status));
}
export function simulationRecoveryLabelV176(value) {
  return value === null ? '尚未发起原回执核对' : ({queued:'原回执核对已排队',running:'正在查询供应商原回执',succeeded:'后台核对已结束，请以原取样记录为准',failed:'后台核对未完成，原取样仍保留',cancelled:'本次后台核对已取消，原取样仍保留'})[value] || '原回执核对状态待核';
}
function daily(value) {
  simulationMarkV176(value);
  need(id(value.id) && version(value.version) && value.schema === 'http-sim-daily-v176' && id(value.environment_id) && /^\d{4}-\d{2}-\d{2}$/.test(value.business_day) && value.day_basis === 'platform_reception_day_Asia_Shanghai' && Array.isArray(value.records) && value.cloud_calls === 0 && value.physical_state_verified === false && value.entity_state_updated === false);
  for (const row of value.records) {
    need(object(row) && id(row.sample_id) && typeof row.data_received === 'boolean' && typeof row.sample_status === 'string' && nullableText(row.processing_status));
    for (const key of ['invocation_id','observation_id','receipt_evidence_id','task_id']) need(nullableID(row[key]));
    need(row.receipt_sha256 === null || typeof row.receipt_sha256 === 'string' && SHA.test(row.receipt_sha256));
  }
  return value;
}
export function checkSimulationIndexV176(value, sourceScope) {
  const scope = simulationScopeV176(sourceScope); simulationMarkV176(value);
  need(keys(value, ['simulation','origin_kind','field_verified','environments','definitions','samples','daily']));
  for (const key of ['environments','definitions','samples','daily']) need(Array.isArray(value[key]) && value[key].length <= 100);
  // These are four independently limited server lists. Missing old parents do
  // not prove corruption or absence; only the current instance's linked rows
  // are published here. No workspace-wide data is reused as write authority.
  const environments = value.environments.map(environment).filter(row => row.instance_id === scope.instance_id);
  const envIDs = new Set(environments.map(row => row.id));
  const definitions = value.definitions.map(definition).filter(row => envIDs.has(row.environment_id) && row.instance_id === scope.instance_id);
  const defIDs = new Set(definitions.map(row => row.id));
  return copy({...value, environments, definitions, samples:value.samples.map(checkSimulationSampleV176).filter(row => defIDs.has(row.definition_id)), daily:value.daily.map(daily).filter(row => envIDs.has(row.environment_id))});
}
export function checkSimulationDetailV176(value, expected, sourceScope = null) {
  checkSimulationSampleV176(value);
  need(value.id === expected.id && value.definition_id === expected.definition_id, '样本范围已变化，未显示另一个样本。');
  if (own(value, 'attributes')) {
    need(value.observation_id !== null && object(value.origin) && object(value.attributes) && object(value.field_units) && value.time_semantics === 'simulator_sample_time' && value.source_time_verified === false && nullableText(value.observed_at) && typeof value.processing_status === 'string');
    simulationMarkV176(value.origin);
    if (sourceScope) need(value.origin.instance_id === simulationScopeV176(sourceScope).instance_id, '原样本来源不属于当前实例，未显示机器数值。');
    for (const [key, number] of Object.entries(value.attributes)) need(typeof number === 'number' && Number.isFinite(number) && typeof value.field_units[key] === 'string');
  }
  return copy(value);
}
export const simulationFieldLabelsV176 = Object.freeze({temperature_c:'温度',humidity_percent:'相对湿度',wind_m_s:'风速',pressure_hpa:'气压'});
export function simulationSampleLabelV176(status) {
  return ({queued:'等待取样',running:'正在读取模拟设备',awaiting_receipt:'原调用尚待核对',received:'已取回并登记',failed:'取样未完成',stopped:'已停止'})[status] || '状态待核对';
}
export function simulationTimelineV176(sample, dailyRecords = []) {
  checkSimulationSampleV176(sample);
  const received = dailyRecords.some(row => row.sample_id === sample.id && row.data_received === true);
  return [
    {label:'读取模拟设备',state:simulationSampleLabelV176(sample.status)},
    {label:'模拟数据登记',state:sample.observation_id ? '已有原登记记录' : '尚无登记记录'},
    {label:'原数据处理',state:sample.processing_status === 'processed' ? '已处理；仍须分别核对接收' : sample.processing_status ? '处理状态：' + sample.processing_status : '尚未读取处理结果'},
    {label:'数据管理接收',state:received ? '每日记录显示已接收' : '尚未核实接收；不从登记或处理状态推断'},
    {label:'业务核查',state:sample.task_id ? '已关联原业务任务；审批与验收在原任务办理' : '尚无关联任务，不代表无需核查'},
  ];
}
export async function simulationHashV176(raw) {
  need(typeof raw === 'string' && new TextEncoder().encode(raw).byteLength <= 65536, '原请求超过保存范围。');
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
  return Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2,'0')).join('');
}
function commandShape(record, scope) {
  need(keys(record, ['schema','scope','path','operation','request_id','raw','sha256']) && record.schema === 'simulation-original-request-v176');
  need(simulationScopeKeyV176(record.scope) === simulationScopeKeyV176(scope) && id(record.request_id) && typeof record.raw === 'string' && (record.sha256 === null || SHA.test(record.sha256)));
  let body; try { body = JSON.parse(record.raw); } catch { throw Error('原请求正文无法核对，请保留本机记录。'); }
  need(body.request_id === record.request_id && JSON.stringify(body) === record.raw);
  const tail = record.path.slice(SIMULATION_PREFIX.length), match = /^\/definitions\/([a-f0-9-]{36})\/(enable|pause|sample)$/.exec(tail), recovery = /^\/samples\/([a-f0-9-]{36})\/recover$/.exec(tail);
  if (match) need(id(match[1]) && keys(body,['request_id','expected_version']) && version(body.expected_version) && record.operation === 'definition.' + match[1] + '.' + match[2]);
  else if (recovery) need(id(recovery[1]) && keys(body,['request_id','expected_version']) && version(body.expected_version) && record.operation === 'sample.' + recovery[1] + '.recover');
  else need(record.path === SIMULATION_PREFIX + '/daily/reconcile' && record.operation === 'daily.reconcile' && keys(body,['request_id','environment_id']) && id(body.environment_id));
  need(record.path === SIMULATION_PREFIX + tail);
  return body;
}
export function checkSimulationRequestV176(value, record) {
  need(object(value) && typeof value.found === 'boolean' && value.request_id === record.request_id, '服务器回执没有对应原请求。');
  if (!value.found) { need(keys(value,['found','request_id'])); return copy(value); }
  need(keys(value,['found','request_id','operation','result']) && value.operation === record.operation, '原请求操作与回执不一致。');
  const body = commandShape(record, record.scope), match = /^definition\.([a-f0-9-]{36})\.(enable|pause|sample)$/.exec(record.operation), recovery = /^sample\.([a-f0-9-]{36})\.recover$/.exec(record.operation);
  if (match?.[2] === 'sample') { checkSimulationSampleV176(value.result); need(value.result.definition_id === match[1]); }
  else if (match) { definition(value.result); need(value.result.id === match[1] && value.result.version === body.expected_version + 1 && value.result.status === (match[2] === 'enable' ? 'enabled' : 'paused')); }
  else if (recovery) { checkSimulationSampleV176(value.result); need(value.result.id === recovery[1] && value.result.actor_id === record.scope.actor_id && value.result.version === body.expected_version + 1 && value.result.status === 'awaiting_receipt' && value.result.observation_id === null && value.result.recovery_status === 'queued' && id(value.result.recovery_job_id), '原回执核对请求的保存结果不对应原取样，未当作数据已接收。'); }
  else { simulationMarkV176(value.result); need(keys(value.result,['rows','simulation','origin_kind','field_verified']) && Array.isArray(value.result.rows)); for (const row of value.result.rows) { daily(row); need(row.environment_id === body.environment_id); } }
  return copy(value);
}

// One shared pending slot per actor/workspace/instance. Each original is also
// stored independently, so a quota failure between the two writes cannot make
// the intent invisible after a remount. Completed originals remain archived.
export class SimulationJournalV176 {
  constructor(scope, options = {}) {
    let storage;
    try { storage = own(options,'storage') ? options.storage : globalThis.localStorage; } catch { storage = null; }
    this.scope = simulationScopeV176(scope); this.key = simulationScopeKeyV176(scope);
    this.storage = storage || {getItem(){throw Error('本机存储不可读取，现可查看服务器历史，暂不能新增接入请求。');}};
    this.locks = own(options,'locks') ? options.locks : globalThis.navigator?.locks;
    this.record = null; this.dirty = false;
  }
  originalKey(requestID) { return this.key + ':original:' + requestID; }
  receiptKey(requestID) { return this.key + ':receipt:' + requestID; }
  async locked(run) {
    need(this.locks && typeof this.locks.request === 'function', '浏览器暂不支持安全请求占用，请保留原请求，换用支持 Web Locks 的浏览器核对。');
    return this.locks.request(this.key, {mode:'exclusive'}, run);
  }
  write(key, raw) { this.storage.setItem(key, raw); need(this.storage.getItem(key) === raw, '本机请求读回失败，尚未发送。'); }
  read() {
    const candidates = new Map(), pointer = this.storage.getItem(this.key + ':pending');
    const consider = raw => {
      if (!raw) return;
      let row; try { row = JSON.parse(raw); } catch { throw Error('本机原请求损坏，请保留记录，不要另建请求号。'); }
      commandShape(row, this.scope);
      const finished = this.storage.getItem(this.receiptKey(row.request_id));
      if (finished !== null) { need(finished === row.sha256, '本机原回执摘要不一致。'); return; }
      const old = candidates.get(row.request_id); need(!old || JSON.stringify(old) === raw, '同一请求号的本机正文不一致。'); candidates.set(row.request_id, row);
    };
    consider(pointer);
    need(this.storage.length <= 20000, '本机记录过多，请保留记录并核对存储空间。');
    for (let i=0; i<this.storage.length; i++) { const key = this.storage.key(i); if (key?.startsWith(this.key + ':original:')) consider(this.storage.getItem(key)); }
    const memory = memories.get(this.key); if (memory) consider(JSON.stringify(memory));
    need(candidates.size <= 1, '本范围存在多份未核原请求，请保留全部记录，暂不新增操作。');
    this.record = candidates.size ? copy([...candidates.values()][0]) : null;
    if (this.record) { const raw = JSON.stringify(this.record); this.dirty = this.storage.getItem(this.key + ':pending') !== raw || this.storage.getItem(this.originalKey(this.record.request_id)) !== raw; }
    else this.dirty = false;
    return this.record ? copy(this.record) : null;
  }
  async pending(current = () => true) {
    return this.locked(async () => { need(current(), '当前页面身份已变化，原请求保留。'); const row = this.read(); if (row) { const hash = await simulationHashV176(row.raw); need(current(), '当前页面身份已变化，原请求保留。'); need(row.sha256 === null || row.sha256 === hash, '原请求摘要不一致，请保留原文。'); if (row.sha256 === null) { row.sha256 = hash; this.record = row; memories.set(this.key,copy(row)); this.dirty = true; } } return row ? copy(row) : null; });
  }
  persist() {
    need(this.record && SHA.test(this.record.sha256 || ''), '原请求尚未完整保存。');
    const raw = JSON.stringify(this.record); memories.set(this.key,copy(this.record)); this.dirty = true;
    const previous = this.storage.getItem(this.originalKey(this.record.request_id)); need(previous === null || previous === raw, '原编号已有不同本机请求，未覆盖。');
    this.write(this.originalKey(this.record.request_id), raw); this.write(this.key + ':pending', raw); this.dirty = false;
    return copy(this.record);
  }
  async install(action, target, expectedVersion, current = () => true) {
    return this.locked(async () => {
      need(current(), '当前页面身份已变化。'); need(this.read() === null, '原接入请求尚待核对，没有另建编号。');
      const request_id = crypto.randomUUID(); let body, path, operation;
      if (action === 'daily') { need(id(target)); body={request_id,environment_id:target}; path=SIMULATION_PREFIX+'/daily/reconcile'; operation='daily.reconcile'; }
      else if (action === 'recover') { need(id(target) && version(expectedVersion)); body={request_id,expected_version:expectedVersion}; path=SIMULATION_PREFIX+'/samples/'+target+'/recover'; operation='sample.'+target+'.recover'; }
      else { need(id(target) && ['enable','pause','sample'].includes(action) && version(expectedVersion)); body={request_id,expected_version:expectedVersion}; path=SIMULATION_PREFIX+'/definitions/'+target+'/'+action; operation='definition.'+target+'.'+action; }
      this.record={schema:'simulation-original-request-v176',scope:copy(this.scope),path,operation,request_id,raw:JSON.stringify(body),sha256:null};
      this.dirty=true; memories.set(this.key,copy(this.record));
      this.record.sha256=await simulationHashV176(this.record.raw); memories.set(this.key,copy(this.record));
      need(current(), '当前页面身份已变化，原请求已保留，未继续发送。');
      return this.persist();
    });
  }
  async retryPersistence(current = () => true) {
    return this.locked(async () => { need(current(), '当前页面身份已变化。'); this.read(); need(this.record, '没有待保存的原请求。'); if (!this.record.sha256) { this.record.sha256=await simulationHashV176(this.record.raw); need(current(), '当前页面身份已变化。'); } return this.persist(); });
  }
  async finish(record, current = () => true) {
    return this.locked(async () => {
      need(current(), '当前页面身份已变化，原回执待重新核对。'); this.read(); need(this.record?.raw===record.raw && this.record.sha256===record.sha256 && this.record.request_id===record.request_id, '本机原请求已变化，未清除任何请求。');
      this.write(this.receiptKey(record.request_id),record.sha256);
      const pointer=this.storage.getItem(this.key+':pending'); need(pointer===JSON.stringify(record), '本机原请求指针已变化，未清除。');
      this.storage.removeItem(this.key+':pending'); need(this.storage.getItem(this.key+':pending')===null, '原回执已保存，本机待核标记尚未清理。');
      memories.delete(this.key); this.record=null; this.dirty=false;
    });
  }
  isUnsaved() { return this.dirty; }
}

export class SimulationClientV176 {
  constructor(api, scope, {current=()=>true, ...storage} = {}) { need(typeof api==='function'); this.api=api; this.scope=simulationScopeV176(scope); this.current=current; this.journal=new SimulationJournalV176(scope,storage); this.disposed=false; }
  guard() { need(!this.disposed && this.current(), '账号、工作区或实例已变化，旧操作已停止，原请求保留。'); }
  async identity(write=false) {
    this.guard(); const me=await this.api('/me'); this.guard();
    need(me?.user_id===this.scope.actor_id && me?.workspace_id===this.scope.workspace_id && me?.role===this.scope.role, '当前会话已变化，请重新核对账号和工作区。');
    if (write) need(['admin','operator'].includes(me.role), '当前账号仅可查看，不能办理接入操作。');
    return me;
  }
  async readIndex() { await this.identity(); const value=await this.api(SIMULATION_PREFIX); await this.identity(); return checkSimulationIndexV176(value,this.scope); }
  async readSample(sample) { await this.identity(); const value=await this.api(SIMULATION_PREFIX+'/samples/'+sample.id); await this.identity(); return checkSimulationDetailV176(value,sample,this.scope); }
  async probe(record) {
    this.guard(); commandShape(record,this.scope); need(await simulationHashV176(record.raw)===record.sha256,'原请求摘要无法核对。'); this.guard();
    await this.identity(); const value=await this.api(SIMULATION_PREFIX+'/requests/'+record.request_id+'?expected_sha256='+record.sha256); await this.identity(); return checkSimulationRequestV176(value,record);
  }
  async settle(record) {
    const value=await this.probe(record);
    if (value.found) { await this.journal.finish(record,()=>!this.disposed&&this.current()); this.guard(); }
    return value;
  }
  async recoveryAdmission(sampleID, expectedVersion) {
    const index=await this.readIndex(); this.guard();
    const sample=index.samples.find(row=>row.id===sampleID);
    need(sample && canRecoverSimulationSampleV176(sample,this.scope), '当前原取样不允许核对：须为本账号尚无登记的原取样，且不能有已排队或运行中的核对任务。');
    need(sample.version===expectedVersion,'原取样版本已变化，原请求保留；请核对最新记录。');
    return sample;
  }
  async sendOriginal(record) {
    // A cold GET precedes every explicit resend. An unknown result is not a
    // negative execution guarantee; the server's exact nonce remains the fence.
    this.guard(); const prior=await this.settle(record); if (prior.found) return prior;
    await this.identity(true);
    return this.journal.locked(async()=>{
      this.guard(); const current=this.journal.read(); need(current?.raw===record.raw&&current.sha256===record.sha256&&!this.journal.dirty,'请先完整保存本机原请求，再核对送达。');
      need(await simulationHashV176(record.raw)===record.sha256,'原请求摘要已变化。'); this.guard(); await this.identity(true); this.guard();
      const recovery=/^sample\.([a-f0-9-]{36})\.recover$/.exec(record.operation);
      if(recovery){await this.recoveryAdmission(recovery[1],JSON.parse(record.raw).expected_version);await this.identity(true);this.guard();}
      let deliveryError;
      try { await this.api(record.path,{method:'POST',body:record.raw}); } catch(error) { deliveryError=error; }
      this.guard();
      // Do not acquire the same Web Lock recursively. Cold-check here; local
      // settlement is performed after releasing this send lock.
      return {sent:true,deliveryError};
    }).then(async attempt=>{
      this.guard(); const saved=await this.settle(record);
      if (!saved.found && attempt.deliveryError) throw Error(attempt.deliveryError.message+' 原请求尚待核对，未更换编号。');
      return saved;
    });
  }
  async start(action,target,expectedVersion) { await this.identity(true); if(action==='recover')await this.recoveryAdmission(target,expectedVersion);const record=await this.journal.install(action,target,expectedVersion,()=>!this.disposed&&this.current()); this.guard(); return this.sendOriginal(record); }
  dispose() { this.disposed=true; }
}
