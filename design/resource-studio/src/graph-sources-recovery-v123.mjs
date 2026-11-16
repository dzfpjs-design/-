// Local discovery only. Identity, command digests and server receipts remain
// the controller's responsibility. No record is written, deleted or submitted.
export const GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH=256*1024;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[a-f0-9]{64}$/,VERSION=/^\d+\.\d+\.\d+$/;
const ADAPTERS={'processed-observation-v1':'已处理的观测结果','anomaly-candidate-v1':'待核实的异常'};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const integer=(value,min=1)=>Number.isSafeInteger(value)&&value>=min;
const uuid=value=>typeof value==='string'&&UUID.test(value);
const hash=value=>typeof value==='string'&&SHA.test(value);
const safe=(value,max)=>typeof value==='string'&&value.length<=max&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const text=(value,max)=>safe(value,max)&&value.trim().length>0;
const clone=value=>JSON.parse(JSON.stringify(value));
function error(code,message){return Object.assign(Error(message),{code});}
function need(value){if(!value)throw error('GRAPH_SOURCE_RECOVERY_RECORD','本机记录无法完整核对，原记录保留。');}
function access(callback){try{return callback();}catch{throw error('GRAPH_SOURCE_RECOVERY_STORAGE','本机存储暂无法读取；未覆盖或删除原记录，请恢复存储后重试。');}}
function scopeValue(value){need(exact(value,['actor_id','workspace_id'])&&uuid(value.actor_id)&&uuid(value.workspace_id));return clone(value);}
const sameScope=(left,right)=>exact(left,['actor_id','workspace_id'])&&left.actor_id===right.actor_id&&left.workspace_id===right.workspace_id;
const prefixFor=scope=>'ga-graph-sources-v122:'+scope.workspace_id+':'+scope.actor_id+':';
function equal(left,right){
 if(left===right)return true;
 if(Array.isArray(left)||Array.isArray(right))return Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&left.every((value,index)=>equal(value,right[index]));
 return object(left)&&object(right)&&Object.keys(left).length===Object.keys(right).length&&Object.keys(left).every(key=>Object.hasOwn(right,key)&&equal(left[key],right[key]));
}
function capability(value,kind){
 need(object(value)&&Object.keys(value).every(key=>['id','name','description','kind','content_hash','release_version'].includes(key))
  &&uuid(value.id)&&text(value.name,300)&&hash(value.content_hash)&&text(value.release_version,40)&&VERSION.test(value.release_version)
  &&(value.description===undefined||value.description===null||safe(value.description,4000))
  &&(kind==='source'?value.kind==='source':['tool','specialist_model','foundation_model'].includes(value.kind)));
}
function form(value){
 need(exact(value,['label','adapter_id','source','extractor','selected_registration','reasons'])&&safe(value.label,200)
  &&Object.hasOwn(ADAPTERS,value.adapter_id)&&(value.selected_registration===null||uuid(value.selected_registration))&&object(value.reasons));
 if(value.source!==null)capability(value.source,'source');if(value.extractor!==null)capability(value.extractor,'extractor');
 for(const [identity,reason] of Object.entries(value.reasons))need(uuid(identity)&&safe(reason,1000));
}
function command(value,kind){
 if(kind==='register')need(exact(value,['schema','request_id','label','adapter_id','source_id','source_hash','extractor_id','extractor_hash'])
  &&value.schema==='graph-event-producer-command-v1'&&uuid(value.request_id)&&text(value.label,200)&&Object.hasOwn(ADAPTERS,value.adapter_id)
  &&uuid(value.source_id)&&uuid(value.extractor_id)&&value.source_id!==value.extractor_id&&hash(value.source_hash)&&hash(value.extractor_hash));
 else need(kind==='revoke'&&exact(value,['schema','request_id','producer_id','producer_version','contract_hash','reason'])
  &&value.schema==='graph-event-producer-revocation-v2'&&uuid(value.request_id)&&uuid(value.producer_id)&&integer(value.producer_version)
  &&hash(value.contract_hash)&&text(value.reason,1000)&&value.reason===value.reason.trim());
}
function retirementCommand(value){
 need(exact(value,['schema','request_id','kind','original','reason'])&&value.schema==='graph-event-source-retirement-v1'
  &&uuid(value.request_id)&&['register','revoke'].includes(value.kind)&&text(value.reason,1000)&&value.reason===value.reason.trim());
 command(value.original,value.kind);
}
function parsedBody(raw,kind){
 need(safe(raw,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH));const value=JSON.parse(raw);
 if(kind==='retirement')retirementCommand(value);else command(value,kind);
 need(JSON.stringify(value)===raw);return value;
}
function revokeReceipt(value,body,scope,commandHash){
 need(exact(value,['id','request_id','actor_id','producer_id','command_hash','command','result'])&&uuid(value.id)
  &&value.request_id===body.request_id&&value.actor_id===scope.actor_id&&value.producer_id===body.producer_id
  &&value.command_hash===commandHash&&equal(value.command,body)&&exact(value.result,['status','version','contract_hash'])
  &&value.result.status==='revoked'&&value.result.version===body.producer_version+1&&value.result.contract_hash===body.contract_hash);
}
function retirementReceipt(value,body,scope,commandHash){
 need(exact(value,['id','request_id','actor_id','kind','original_request_id','original_command_hash','command_hash','command','result'])
  &&uuid(value.id)&&value.request_id===body.request_id&&value.actor_id===scope.actor_id&&value.kind===body.kind
  &&value.original_request_id===body.original.request_id&&hash(value.original_command_hash)&&value.command_hash===commandHash&&equal(value.command,body)
  &&exact(value.result,['outcome','kind','original_request_id','original_command_hash'])&&value.result.outcome==='retired'
  &&value.result.kind===value.kind&&value.result.original_request_id===value.original_request_id
  &&value.result.original_command_hash===value.original_command_hash);
}
function display(value,fallback,max=100){
 const content=typeof value==='string'?value.trim():'';
 if(!content||UUID.test(content)||SHA.test(content)||/^[\[{]/.test(content))return fallback;
 return [...content].slice(0,max).join('');
}
function candidateFor(key,raw,scope){
 need(typeof key==='string'&&key.startsWith(prefixFor(scope))&&safe(raw,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH));
 const record=JSON.parse(raw),prefix=prefixFor(scope);
 need(object(record)&&sameScope(record.scope,scope)&&uuid(record.window_id));
 let kind,owner=null,generation=null,label,summary,local_completion=false;
 if(['graph-source-form-v122','graph-source-form-entry-v122','graph-source-form-slot-v122'].includes(record.schema)){
  const old=record.schema==='graph-source-form-v122',slot=record.schema==='graph-source-form-slot-v122';
  need(exact(record,['schema','scope','window_id',...(old?[]:['owner']),...(slot?['generation']:[]),'form']));form(record.form);
  if(old)need(key.startsWith(prefix+'form:')&&uuid(key.slice((prefix+'form:').length)));
  else{owner=record.owner;need(uuid(owner));if(slot){generation=record.generation;need(integer(generation)&&key===prefix+'form:slot:'+owner+':'+(generation%2));}else need(key===prefix+'form:entry:'+owner);}
  kind='draft';label=display(record.form.label,'未命名的登记稿');
  const source=display(record.form.source?.name,'尚未选择来源',70),extractor=display(record.form.extractor?.name,'尚未选择处理能力',70);
  summary=(old?'此前保留的完整原稿':slot?'本页已保存的修改':'进入该页面时保留的原稿')+'；'+source+' → '+extractor+'。';
 }else if(record.schema==='graph-source-outbox-v122'){
  need(exact(record,['schema','scope','window_id','kind','body','exact_body','command_hash','preview_contract_hash'])
   &&['register','revoke'].includes(record.kind)&&hash(record.command_hash)
   &&(record.kind==='register'?hash(record.preview_contract_hash):record.preview_contract_hash===null));
  command(record.body,record.kind);need(equal(parsedBody(record.exact_body,record.kind),record.body)&&key===prefix+'outbox:'+record.body.request_id);
  kind='request';label=record.kind==='register'?display(record.body.label,'来源登记原请求'):'停止处理结果的原请求';
  summary=record.kind==='register'?ADAPTERS[record.body.adapter_id]+'；原登记结果仍需向服务器核对。':'原停止结果仍需向服务器核对；'+display(record.body.reason,'已保留原停止原因',100);
 }else if(record.schema==='graph-source-completed-v122'){
  need(['register','revoke'].includes(record.kind)&&exact(record,['schema','scope','window_id','kind','command_hash','exact_body',...(Object.hasOwn(record,'receipt')?['receipt']:[])])&&hash(record.command_hash));
  const body=parsedBody(record.exact_body,record.kind);need(key===prefix+'completed:'+body.request_id);
  if(Object.hasOwn(record,'receipt')){need(record.kind==='revoke');revokeReceipt(record.receipt,body,scope,record.command_hash);}
  kind='request';local_completion=true;label=record.kind==='register'?display(body.label,'来源登记原请求'):'停止处理结果的原请求';
  summary='本机曾记录核对完成；这不是服务器回执，仍需向服务器核对原结果。';
 }else if(record.schema==='graph-source-retired-v123'){
  need(exact(record,['schema','scope','window_id','kind','command_hash','exact_body','retirement'])
   &&['register','revoke'].includes(record.kind)&&hash(record.command_hash));
  const body=parsedBody(record.exact_body,record.kind);need(key===prefix+'retired:'+body.request_id);
  need(object(record.retirement));retirementCommand(record.retirement.command);
  const cancel=record.retirement.command;
  need(cancel.kind===record.kind&&equal(cancel.original,body)&&record.retirement.original_command_hash===record.command_hash&&hash(record.retirement.command_hash));
  retirementReceipt(record.retirement,cancel,scope,record.retirement.command_hash);
  kind='request';local_completion=true;label=record.kind==='register'?display(body.label,'来源登记原请求'):'停止处理结果的原请求';
  summary='本机曾记录取消结果；仍需向服务器核对原请求是否已保存或取消。';
 }else if(['graph-source-retirement-outbox-v123','graph-source-retirement-completed-v123'].includes(record.schema)){
  local_completion=record.schema==='graph-source-retirement-completed-v123';
  need(exact(record,['schema','scope','window_id',...(local_completion?[]:['body']),'exact_body','command_hash',...(local_completion?['receipt']:['original_window_id'])])&&hash(record.command_hash));
  const body=parsedBody(record.exact_body,'retirement');
  if(local_completion){need(key===prefix+'retire-completed:'+body.request_id);retirementReceipt(record.receipt,body,scope,record.command_hash);}
  else need(uuid(record.original_window_id)&&equal(record.body,body)&&key===prefix+'retire-outbox:'+body.request_id);
  kind='retirement';label='取消未结清请求的原请求';
  summary=(body.kind==='register'?'取消原登记请求':'取消原停止请求')+'；'+(local_completion?'本机曾记录完成，仍需服务器核对。':display(body.reason,'已保留原取消原因',100)+'；仍需服务器核对。');
 }else need(false);
 return {record,candidate:{key,raw,scope:clone(scope),window_id:record.window_id,owner,generation,kind,label,summary,local_completion}};
}

/** Physical storage offsets count every scanned key, including other scopes.
 * Only current-scope values are read. Empty pages can have a next offset.
 * A changing storage key order requires an explicit fresh enumeration; every
 * selected candidate is independently re-read before it can be used.
 */
export function enumerateGraphSourceRecovery({storage,scope,offset=0,limit=20,maxScan=200}){
 scope=scopeValue(scope);need(integer(offset,0)&&integer(limit)&&limit<=50&&integer(maxScan)&&maxScan<=200);
 const length=access(()=>storage.length);need(integer(length,0));
 const prefix=prefixFor(scope),items=[],seen=new Set();let index=offset,scanned=0,problem_count=0;
 while(index<length&&scanned<maxScan&&items.length<limit){
  const key=access(()=>storage.key(index));index++;scanned++;
  if(key===null)continue;if(typeof key!=='string')throw error('GRAPH_SOURCE_RECOVERY_STORAGE','本机目录暂无法读取，原记录保留。');
  if(!key.startsWith(prefix)||seen.has(key))continue;seen.add(key);
  const raw=access(()=>storage.getItem(key));
  // Storage failures propagate above; malformed matching records stay in place.
  try{items.push(candidateFor(key,raw,scope).candidate);}catch{problem_count++;}
 }
 return {items,next_offset:index<length?index:null,problem_count};
}

export function readGraphSourceRecoveryCandidate({storage,scope,candidate}){
 scope=scopeValue(scope);
 need(exact(candidate,['key','raw','scope','window_id','owner','generation','kind','label','summary','local_completion'])&&sameScope(candidate.scope,scope)
  &&typeof candidate.key==='string'&&candidate.key.startsWith(prefixFor(scope))&&safe(candidate.raw,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH));
 const raw=access(()=>storage.getItem(candidate.key));
 if(raw!==candidate.raw)throw error('GRAPH_SOURCE_RECOVERY_CHANGED','选中的本机记录已经变化或暂不可读；未替换原记录，请重新读取列表后明确选择。');
 const checked=candidateFor(candidate.key,raw,scope);need(equal(checked.candidate,candidate));
 return {record:checked.record,raw,key:candidate.key,kind:checked.candidate.kind};
}
