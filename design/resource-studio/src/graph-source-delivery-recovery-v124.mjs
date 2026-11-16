// Read-only local discovery. A candidate, including a completion hint, grants
// no retry authority. The controller checks the original command digest and
// actual server nonce after verifying the current actor and workspace.
export const GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH=256*1024;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[a-f0-9]{64}$/;
const OUTBOX='graph-source-delivery-outbox-v124',COMPLETED='graph-source-delivery-completed-v124';
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const integer=(value,min=1)=>Number.isSafeInteger(value)&&value>=min;
const uuid=value=>typeof value==='string'&&UUID.test(value);
const validText=value=>typeof value==='string'&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const clone=value=>JSON.parse(JSON.stringify(value));
function failure(code,message){return Object.assign(Error(message),{code});}
function need(value){if(!value)throw failure('GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD','本机原投递操作无法完整核对，原记录保留。');}
function access(callback){try{return callback();}catch{throw failure('GRAPH_SOURCE_DELIVERY_RECOVERY_STORAGE','本机存储暂无法读取；未覆盖或删除原投递操作，请恢复存储后重试。');}}
function scopeValue(value){need(exact(value,['actor_id','workspace_id'])&&uuid(value.actor_id)&&uuid(value.workspace_id));return clone(value);}
const sameScope=(left,right)=>exact(left,['actor_id','workspace_id'])&&left.actor_id===right.actor_id&&left.workspace_id===right.workspace_id;
const prefixFor=scope=>'ga-graph-delivery-v124:'+scope.workspace_id+':'+scope.actor_id+':';
function same(left,right){
 if(left===right)return true;
 return object(left)&&object(right)&&Object.keys(left).length===Object.keys(right).length
  &&Object.keys(left).every(key=>Object.hasOwn(right,key)&&same(left[key],right[key]));
}
function command(value){
 // RetryIntent has no schema or trimming rule. Preserve the original reason,
 // including leading/trailing or all whitespace, and count Unicode code points
 // like the backend's 1..1000-character field rather than UTF-16 code units.
 need(exact(value,['request_id','job_version','reason'])&&uuid(value.request_id)&&integer(value.job_version)
  &&validText(value.reason)&&value.reason.length>0&&value.reason.length<=2000&&[...value.reason].length<=1000);
}
function displayReason(value){
 const trimmed=value.trim();
 if(!trimmed||UUID.test(trimmed)||SHA.test(trimmed)||/^[\[{]/.test(trimmed))return '已保留原恢复说明';
 return [...trimmed].slice(0,120).join('');
}
function checked(key,raw,scope){
 need(typeof key==='string'&&key.startsWith(prefixFor(scope))&&typeof raw==='string'&&raw.length<=GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH);
 const record=JSON.parse(raw);
 need(exact(record,['schema','scope','window_id','intent_id','producer_id','body','exact_body','command_hash'])
  &&[OUTBOX,COMPLETED].includes(record.schema)&&sameScope(record.scope,scope)&&uuid(record.window_id)
  &&uuid(record.intent_id)&&uuid(record.producer_id)&&typeof record.command_hash==='string'&&SHA.test(record.command_hash));
 command(record.body);
 need(typeof record.exact_body==='string'&&record.exact_body===JSON.stringify(record.body));
 const local_completion=record.schema===COMPLETED;
 need(key===prefixFor(scope)+(local_completion?'completed:':'outbox:')+record.body.request_id);
 const summary=(local_completion?'本机曾记录核对完成；仍需向服务器核对原结果。':'原投递恢复结果仍需向服务器核对。')+' '+displayReason(record.body.reason);
 return {record,candidate:{key,raw,scope:clone(scope),window_id:record.window_id,intent_id:record.intent_id,producer_id:record.producer_id,
  kind:'request',label:'处理结果投递的原恢复操作',summary,local_completion}};
}

/** Offsets count physical keys, including other scopes. Read at most maxScan
 * keys and limit matching items; an empty page may still have next_offset.
 * Concurrent key-order changes require an explicit fresh enumeration. Every
 * selected candidate is independently re-read before the controller uses it.
 */
export function enumerateGraphSourceDeliveryRecovery({storage,scope,offset=0,limit=20,maxScan=200}){
 scope=scopeValue(scope);need(integer(offset,0)&&integer(limit)&&limit<=50&&integer(maxScan)&&maxScan<=200);
 const length=access(()=>storage.length);need(integer(length,0));
 const prefix=prefixFor(scope),items=[],seen=new Set();let index=offset,scanned=0,problem_count=0;
 while(index<length&&scanned<maxScan&&items.length<limit){
  const key=access(()=>storage.key(index));index++;scanned++;
  if(key===null)continue;
  if(typeof key!=='string')throw failure('GRAPH_SOURCE_DELIVERY_RECOVERY_STORAGE','本机目录暂无法读取，原投递操作保留。');
  if(!key.startsWith(prefix)||seen.has(key))continue;seen.add(key);
  const raw=access(()=>storage.getItem(key));
  try{items.push(checked(key,raw,scope).candidate);}catch{problem_count++;}
 }
 return {items,next_offset:index<length?index:null,problem_count};
}

export function readGraphSourceDeliveryRecoveryCandidate({storage,scope,candidate}){
 scope=scopeValue(scope);
 need(exact(candidate,['key','raw','scope','window_id','intent_id','producer_id','kind','label','summary','local_completion'])&&sameScope(candidate.scope,scope)
  &&typeof candidate.key==='string'&&candidate.key.startsWith(prefixFor(scope))&&typeof candidate.raw==='string'&&candidate.raw.length<=GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH);
 const raw=access(()=>storage.getItem(candidate.key));
 if(raw!==candidate.raw)throw failure('GRAPH_SOURCE_DELIVERY_RECOVERY_CHANGED','选中的本机原投递操作已经变化或暂不可读；未替换原记录，请重新读取并明确选择。');
 const value=checked(candidate.key,raw,scope);need(same(value.candidate,candidate));
 return {record:value.record,raw,key:candidate.key,kind:'request'};
}
