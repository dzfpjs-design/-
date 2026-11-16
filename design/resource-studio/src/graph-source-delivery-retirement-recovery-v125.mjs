// Local discovery is only a hint. The controller verifies both immutable
// command hashes and the actual server nonces after checking actor/workspace.
// This module never writes storage or certifies that a request was ended.
export const GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_MAX_RECORD_LENGTH=256*1024;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[a-f0-9]{64}$/;
const OUTBOX='graph-source-delivery-retirement-outbox-v125',COMPLETED='graph-source-delivery-retirement-completed-v125';
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const integer=(value,min=1)=>Number.isSafeInteger(value)&&value>=min;
const uuid=value=>typeof value==='string'&&UUID.test(value);
const validText=value=>typeof value==='string'&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const boundedReason=value=>validText(value)&&value.length>0&&value.length<=2000&&[...value].length<=1000;
// Match Python str.strip() only for the meaningful-text test, preserving the
// original reason. JS trim() has a different Unicode whitespace set.
const meaningful=value=>/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(value);
const clone=value=>JSON.parse(JSON.stringify(value));
function failure(code,message){return Object.assign(Error(message),{code});}
function need(value){if(!value)throw failure('GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_RECORD','本机结束操作无法完整核对，原记录保留。');}
function access(callback){try{return callback();}catch{throw failure('GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_STORAGE','本机存储暂无法读取；未覆盖或删除原结束操作，请恢复存储后重试。');}}
function scopeValue(value){need(exact(value,['actor_id','workspace_id'])&&uuid(value.actor_id)&&uuid(value.workspace_id));return clone(value);}
const sameScope=(left,right)=>exact(left,['actor_id','workspace_id'])&&left.actor_id===right.actor_id&&left.workspace_id===right.workspace_id;
const prefixFor=scope=>'ga-graph-delivery-retirement-v125:'+scope.workspace_id+':'+scope.actor_id+':';
function same(left,right){
 if(left===right)return true;
 return object(left)&&object(right)&&Object.keys(left).length===Object.keys(right).length
  &&Object.keys(left).every(key=>Object.hasOwn(right,key)&&same(left[key],right[key]));
}
function command(value){
 need(exact(value,['schema','request_id','original','reason'])&&value.schema==='graph-event-intent-retry-retirement-v1'
  &&uuid(value.request_id)&&boundedReason(value.reason)&&meaningful(value.reason));
 const original=value.original;
 // The old RetryIntent reason allows whitespace-only text. Do not trim it,
 // add a schema, change its version or generate a replacement original nonce.
 need(exact(original,['request_id','job_version','reason'])&&uuid(original.request_id)&&integer(original.job_version)
  &&boundedReason(original.reason)&&value.request_id!==original.request_id);
}
function displayReason(value){
 const trimmed=value.trim();
 if(!trimmed||UUID.test(trimmed)||SHA.test(trimmed)||/^[\[{]/.test(trimmed))return '已保留原结束说明';
 return [...trimmed].slice(0,120).join('');
}
function checked(key,raw,scope){
 need(typeof key==='string'&&key.startsWith(prefixFor(scope))&&typeof raw==='string'&&raw.length<=GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_MAX_RECORD_LENGTH);
 let record;try{record=JSON.parse(raw);}catch{need(false);}
 need(exact(record,['schema','scope','window_id','intent_id','producer_id','original_window_id','body','exact_body','command_hash'])
  &&[OUTBOX,COMPLETED].includes(record.schema)&&sameScope(record.scope,scope)&&uuid(record.window_id)&&uuid(record.original_window_id)
  &&uuid(record.intent_id)&&uuid(record.producer_id)&&typeof record.command_hash==='string'&&SHA.test(record.command_hash));
 command(record.body);
 need(typeof record.exact_body==='string'&&record.exact_body===JSON.stringify(record.body));
 const local_completion=record.schema===COMPLETED;
 need(key===prefixFor(scope)+(local_completion?'completed:':'outbox:')+record.body.request_id);
 const summary=(local_completion?'本机曾记录核对完成；仍需向服务器核对结束操作。':'这次结束操作仍需向服务器核对。')+' '+displayReason(record.body.reason);
 return {record,candidate:{key,raw,scope:clone(scope),window_id:record.window_id,intent_id:record.intent_id,producer_id:record.producer_id,
  kind:'retirement',label:'送达恢复的原结束操作',summary,local_completion}};
}

/** Physical offsets include other scopes. An empty bounded page can continue.
 * Key-order changes require fresh discovery; selection always re-reads exact
 * bytes. No other actor/workspace's value is read or parsed.
 */
export function enumerateGraphSourceDeliveryRetirementRecovery({storage,scope,offset=0,limit=20,maxScan=200}){
 scope=scopeValue(scope);need(integer(offset,0)&&integer(limit)&&limit<=50&&integer(maxScan)&&maxScan<=200);
 const length=access(()=>storage.length);need(integer(length,0));
 const prefix=prefixFor(scope),items=[],seen=new Set();let index=offset,scanned=0,problem_count=0;
 while(index<length&&scanned<maxScan&&items.length<limit){
  const key=access(()=>storage.key(index));index++;scanned++;
  if(key===null)continue;
  if(typeof key!=='string')throw failure('GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_STORAGE','本机目录暂无法读取，原结束操作保留。');
  if(!key.startsWith(prefix)||seen.has(key))continue;seen.add(key);
  const raw=access(()=>storage.getItem(key));
  try{items.push(checked(key,raw,scope).candidate);}catch{problem_count++;}
 }
 return {items,next_offset:index<length?index:null,problem_count};
}

export function readGraphSourceDeliveryRetirementRecoveryCandidate({storage,scope,candidate}){
 scope=scopeValue(scope);
 need(exact(candidate,['key','raw','scope','window_id','intent_id','producer_id','kind','label','summary','local_completion'])&&sameScope(candidate.scope,scope)
  &&typeof candidate.key==='string'&&candidate.key.startsWith(prefixFor(scope))&&typeof candidate.raw==='string'&&candidate.raw.length<=GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_MAX_RECORD_LENGTH);
 const raw=access(()=>storage.getItem(candidate.key));
 if(raw!==candidate.raw)throw failure('GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_CHANGED','选中的本机结束操作已经变化或暂不可读；未替换原记录，请重新读取并明确选择。');
 const value=checked(candidate.key,raw,scope);need(same(value.candidate,candidate));
 return {record:value.record,raw,key:candidate.key,kind:'retirement'};
}
