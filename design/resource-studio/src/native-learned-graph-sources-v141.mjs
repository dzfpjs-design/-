// Explicit learned GeoPair contract family. No missing extractor is treated as native.
import {graphWireCanonical,graphWireDigest,graphWireParseCanonical} from './full-semantic-graph-wire.mjs';
import {NativeGraphSourceJournalV136,nativeSourceScopeV136,NATIVE_SOURCE_MAX_RECORD_V136} from './native-graph-sources-journal-v136.mjs';
export const NATIVE_LEARNED_GEO_ADAPTER_V141='native-learned-binary-change-v1';
export const NATIVE_LEARNED_GEO_CONTRACT_V141='graph-event-native-learned-geo-source-contract-v1';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,names)=>object(value)&&Object.keys(value).length===names.split(',').length&&names.split(',').every(key=>Object.hasOwn(value,key));
const safe=(value,max=1000)=>typeof value==='string'&&[...value].length<=max&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
export const learnedSourceTrimV141=value=>value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
const text=(value,max=1000)=>safe(value,max)&&learnedSourceTrimV141(value).length>0;
const uuid=value=>typeof value==='string'&&UUID.test(value),sha=value=>typeof value==='string'&&SHA.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=1;
const same=(left,right)=>graphWireCanonical(left)===graphWireCanonical(right);
const need=(value,message='本地变化候选来源、原命令或保存回执无法完整核对；原稿和原请求保留。')=>{if(!value)throw Error(message);};
const clone=value=>JSON.parse(JSON.stringify(value));
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
export const NATIVE_LEARNED_GEO_RESULT_SCHEMA_V141=freeze({type:'object',additionalProperties:false,required:['attributes'],properties:{attributes:{type:'object',additionalProperties:false,required:['差异候选像素数','未知像素数'],properties:{'差异候选像素数':{type:'integer',minimum:0},'未知像素数':{type:'integer',minimum:0}}}}});
export function learnedGeoOutputSchemaV141(){
 const properties=Object.fromEntries(['adapter_id','observation_id','entity_id','source_id','run_id','task_id','business_day'].map(key=>[key,{type:'string'}]));
 Object.assign(properties,{schema:{const:'graph-event-native-learned-geo-output-v1'},date_basis:{const:'A_original_source_request'},observed_at:{type:'null'},candidate_only:{const:true},data_received:{const:true},source_time_verified:{const:false},physical_state_verified:{const:false},entity_state_updated:{const:false},result:NATIVE_LEARNED_GEO_RESULT_SCHEMA_V141});
 Object.assign(properties,{candidate_pixels:{type:'integer',minimum:0,title:'差异候选像素数'},unknown_pixels:{type:'integer',minimum:0,title:'未知像素数'}});
 return {type:'object',additionalProperties:false,required:Object.keys(properties),properties};
}
export function learnedModelTraceV141(value){
 need(keys(value,'installation_id,pack_id,generation,recipe_hash,pipeline_hash,weights_sha256,backend')&&uuid(value.installation_id)
  &&value.pack_id==='changer-r18-binary-v1'&&integer(value.generation)&&sha(value.recipe_hash)&&sha(value.pipeline_hash)&&sha(value.weights_sha256)&&value.backend==='cpu');return value;
}
export function learnedGraphSourceContractV141(value){
 need(keys(value,'schema,adapter_id,event_kind,correlation_kind,instance_id,source_id,source_hash,source_release_version,run_type,request_kind,capability_id,capability_hash,capability_release_version,model_trace,model_trace_hash,probability_threshold,alignment,runtime_hash,result_schema,output_schema,output_schema_hash,contract_hash')
  &&value.schema===NATIVE_LEARNED_GEO_CONTRACT_V141&&value.adapter_id===NATIVE_LEARNED_GEO_ADAPTER_V141
  &&value.event_kind==='observation.data'&&value.correlation_kind==='entity_id'&&value.run_type==='GeoPair'&&value.request_kind==='learned_binary_change'
  &&uuid(value.instance_id)&&uuid(value.source_id)&&uuid(value.capability_id)&&sha(value.source_hash)&&sha(value.capability_hash)
  &&text(value.source_release_version,100)&&text(value.capability_release_version,100)&&sha(value.model_trace_hash)&&sha(value.runtime_hash)
  &&sha(value.output_schema_hash)&&sha(value.contract_hash)&&typeof value.probability_threshold==='number'&&Number.isFinite(value.probability_threshold)&&value.probability_threshold>0&&value.probability_threshold<1
  &&['same_grid','reproject_after_to_before'].includes(value.alignment));
 learnedModelTraceV141(value.model_trace);need(value.runtime_hash===value.model_trace.pipeline_hash);
 need(same(value.result_schema,NATIVE_LEARNED_GEO_RESULT_SCHEMA_V141)&&same(value.output_schema,learnedGeoOutputSchemaV141()));return value;
}
export async function verifyLearnedGraphSourceContractV141(value){
 learnedGraphSourceContractV141(value);
 need(await graphWireDigest(value.model_trace)===value.model_trace_hash&&await graphWireDigest(value.output_schema)===value.output_schema_hash
  &&await graphWireDigest(value,{omit:'contract_hash'})===value.contract_hash);return value;
}
export function learnedSourceCommandV141(value,kind,scope){
 scope=nativeSourceScopeV136(scope);
 if(kind==='register')need(keys(value,'schema,request_id,label,adapter_id,instance_id,source_id,source_hash,capability_id,capability_hash')
  &&value.schema==='graph-event-native-learned-geo-producer-command-v1'&&uuid(value.request_id)&&text(value.label,200)
  &&value.adapter_id===NATIVE_LEARNED_GEO_ADAPTER_V141&&value.instance_id===scope.instance_id
  &&uuid(value.source_id)&&sha(value.source_hash)&&uuid(value.capability_id)&&sha(value.capability_hash));
 else if(kind==='revoke')need(keys(value,'schema,request_id,instance_id,producer_id,producer_version,contract_hash,reason')
  &&value.schema==='graph-event-native-learned-geo-producer-revocation-v1'&&uuid(value.request_id)&&value.instance_id===scope.instance_id
  &&uuid(value.producer_id)&&integer(value.producer_version)&&sha(value.contract_hash)&&text(value.reason)&&value.reason===learnedSourceTrimV141(value.reason));
 else{
  need(kind==='retirement'&&keys(value,'schema,request_id,instance_id,kind,original,reason')
   &&value.schema==='graph-event-native-learned-geo-source-retirement-v1'&&uuid(value.request_id)&&value.instance_id===scope.instance_id
   &&['register','revoke'].includes(value.kind)&&text(value.reason)&&value.reason===learnedSourceTrimV141(value.reason));
  learnedSourceCommandV141(value.original,value.kind,scope);need(value.request_id!==value.original.request_id);
 }
 return value;
}
const hashSchemas={register:'graph-event-native-learned-geo-producer-command-hash-v1',revoke:'graph-event-native-learned-geo-producer-revocation-command-hash-v1',retirement:'graph-event-native-learned-geo-source-retirement-command-hash-v1'};
export async function learnedSourceCommandHashV141(scope,kind,command){
 learnedSourceCommandV141(command,kind,scope);return graphWireDigest({schema:hashSchemas[kind],workspace_id:scope.workspace_id,actor_id:scope.actor_id,command});
}
export function emptyLearnedSourceFormV141(){return {label:'',source:null,capability:null,selected_registration:null,reasons:{}};}
function sourceRow(value){need(keys(value,'id,name,content_hash,release_version')&&uuid(value.id)&&text(value.name,300)&&sha(value.content_hash)&&text(value.release_version,100));return value;}
function capabilityRow(value){
 need(keys(value,'id,name,content_hash,release_version,model_trace,model_trace_hash,probability_threshold,alignment,runtime_hash')&&uuid(value.id)&&text(value.name,2000)&&sha(value.content_hash)&&text(value.release_version,100)&&sha(value.model_trace_hash)&&sha(value.runtime_hash)
  &&typeof value.probability_threshold==='number'&&Number.isFinite(value.probability_threshold)&&value.probability_threshold>0&&value.probability_threshold<1&&['same_grid','reproject_after_to_before'].includes(value.alignment));
 learnedModelTraceV141(value.model_trace);need(value.runtime_hash===value.model_trace.pipeline_hash);return value;
}
export function learnedSourceFormV141(value){
 need(keys(value,'label,source,capability,selected_registration,reasons')&&safe(value.label,200)&&(value.selected_registration===null||uuid(value.selected_registration))&&object(value.reasons));
 if(value.source!==null)sourceRow(value.source);if(value.capability!==null)capabilityRow(value.capability);
 for(const [identity,reason] of Object.entries(value.reasons))need(uuid(identity)&&safe(reason));return clone(value);
}
export function learnedSourceOutboxV141(value,scope){
 need(keys(value,'schema,scope,window_id,kind,body,exact_body,command_hash,preview_contract_hash')
  &&value.schema==='native-learned-graph-source-outbox-v141'&&same(value.scope,nativeSourceScopeV136(scope))&&uuid(value.window_id)
  &&['register','revoke','retirement'].includes(value.kind)&&sha(value.command_hash)&&typeof value.exact_body==='string'
  &&(value.kind==='register'?sha(value.preview_contract_hash):value.preview_contract_hash===null));
 const original=graphWireParseCanonical(value.exact_body);learnedSourceCommandV141(original,value.kind,scope);
 need(same(original,value.body));return value;
}
export async function checkLearnedSourceOptionsV141(value,scope){
 need(keys(value,'schema,instance_id,sources,capabilities,next_after,can_register,reason')&&value.schema==='graph-event-native-learned-geo-producer-options-v1'&&value.instance_id===scope.instance_id
  &&Array.isArray(value.sources)&&value.sources.length<=20&&Array.isArray(value.capabilities)&&value.capabilities.length<=200
  &&(value.next_after===null||uuid(value.next_after))&&typeof value.can_register==='boolean'&&(value.reason===null||text(value.reason,2000)));
 need(new Set(value.sources.map(row=>row.id)).size===value.sources.length&&new Set(value.capabilities.map(row=>row.id)).size===value.capabilities.length);
 value.sources.forEach(sourceRow);for(const row of value.capabilities){capabilityRow(row);need(await graphWireDigest(row.model_trace)===row.model_trace_hash);}return value;
}
function availability(value){need(keys(value,'available,reason_code,label')&&typeof value.available==='boolean'&&(value.reason_code===null||text(value.reason_code,100))&&text(value.label,2000));}
export async function checkLearnedSourceProducerV141(value,scope,{catalog=false}={}){
 need(keys(value,'id,label,status,version,contract_hash,contract,registration,execution_granted,physical_state_verified'+(catalog?',source_name,capability_name,current_availability,receipt_verified':''))
  &&uuid(value.id)&&text(value.label,200)&&['active','revoked'].includes(value.status)&&integer(value.version)
  &&sha(value.contract_hash)&&value.execution_granted===false&&value.physical_state_verified===false);
 await verifyLearnedGraphSourceContractV141(value.contract);need(value.contract.instance_id===scope.instance_id&&await graphWireDigest(value.contract)===value.contract_hash);
 const registration=value.registration;need(keys(registration,'request_id,actor_id,command_hash,command')&&uuid(registration.actor_id)&&uuid(registration.request_id)&&sha(registration.command_hash));
 learnedSourceCommandV141(registration.command,'register',scope);
 need(registration.request_id===registration.command.request_id&&registration.command.label===value.label
  &&registration.command.source_id===value.contract.source_id&&registration.command.source_hash===value.contract.source_hash
  &&registration.command.capability_id===value.contract.capability_id&&registration.command.capability_hash===value.contract.capability_hash);
 need(await learnedSourceCommandHashV141({...scope,actor_id:registration.actor_id},'register',registration.command)===registration.command_hash);
 if(catalog){need((value.source_name===null||text(value.source_name,300))&&(value.capability_name===null||text(value.capability_name,300))&&value.receipt_verified===false);availability(value.current_availability);}
 return value;
}
export async function checkLearnedSourcePreviewV141(value,body,scope){
 need(keys(value,'schema,instance_id,command,contract,contract_hash,command_hash,execution_granted,meaning')&&value.schema==='graph-event-native-learned-geo-producer-preview-v1'
  &&value.instance_id===scope.instance_id&&same(value.command,body)&&value.execution_granted===false&&text(value.meaning,2000));
 await verifyLearnedGraphSourceContractV141(value.contract);
 need(value.contract.instance_id===scope.instance_id&&value.contract.source_id===body.source_id&&value.contract.source_hash===body.source_hash
  &&value.contract.capability_id===body.capability_id&&value.contract.capability_hash===body.capability_hash
  &&await graphWireDigest(value.contract)===value.contract_hash&&await learnedSourceCommandHashV141(scope,'register',body)===value.command_hash);return value;
}
export async function checkLearnedSourceReceiptV141(receipt,pending,scope){
 const command=graphWireParseCanonical(pending.exact_body),signature=await learnedSourceCommandHashV141(scope,pending.kind,command);
 need(signature===pending.command_hash);
 if(pending.kind==='register'){
  await checkLearnedSourceProducerV141(receipt,scope);need(receipt.registration.actor_id===scope.actor_id&&same(receipt.registration.command,command)
   &&receipt.registration.command_hash===signature&&receipt.registration.request_id===command.request_id&&receipt.contract_hash===pending.preview_contract_hash);
 }else if(pending.kind==='revoke'){
  need(keys(receipt,'schema,id,request_id,actor_id,instance_id,producer_id,command_hash,command,result')&&receipt.schema==='graph-event-native-learned-geo-producer-revocation-receipt-v1'
   &&uuid(receipt.id)&&receipt.request_id===command.request_id&&receipt.actor_id===scope.actor_id&&receipt.instance_id===scope.instance_id
   &&receipt.producer_id===command.producer_id&&receipt.command_hash===signature&&same(receipt.command,command)
   &&keys(receipt.result,'status,version,contract_hash')&&receipt.result.status==='revoked'&&receipt.result.version===command.producer_version+1&&receipt.result.contract_hash===command.contract_hash);
 }else{
  need(keys(receipt,'schema,id,request_id,actor_id,instance_id,kind,original_request_id,original_command_hash,command_hash,command,result')
   &&receipt.schema==='graph-event-native-learned-geo-source-retirement-receipt-v1'&&uuid(receipt.id)&&receipt.request_id===command.request_id
   &&receipt.actor_id===scope.actor_id&&receipt.instance_id===scope.instance_id&&receipt.kind===command.kind
   &&receipt.original_request_id===command.original.request_id&&receipt.command_hash===signature&&same(receipt.command,command)
   &&receipt.original_command_hash===await learnedSourceCommandHashV141(scope,command.kind,command.original)
   &&keys(receipt.result,'outcome,kind,original_request_id,original_command_hash')&&receipt.result.outcome==='retired'
   &&receipt.result.kind===command.kind&&receipt.result.original_request_id===command.original.request_id&&receipt.result.original_command_hash===receipt.original_command_hash);
 }
 return receipt;
}
export async function checkLearnedSourceStateV141(value,pending,scope){
 const body=graphWireParseCanonical(pending.exact_body);need(keys(value,'schema,instance_id,kind,request_id,outcome,command_hash,saved,retirement')
  &&value.schema==='graph-event-native-learned-geo-source-request-state-v1'&&value.instance_id===scope.instance_id
  &&value.kind===pending.kind&&value.request_id===body.request_id&&['not_found','saved','retired'].includes(value.outcome));
 if(value.outcome==='not_found')need(value.command_hash===null&&value.saved===null&&value.retirement===null);
 else{
  need(value.command_hash===pending.command_hash);
  if(value.outcome==='saved'){need(value.retirement===null);await checkLearnedSourceReceiptV141(value.saved,pending,scope);}
  else{
   need(value.saved===null&&value.retirement?.command?.kind===pending.kind&&same(value.retirement.command.original,body));
   const retirement=value.retirement,original={kind:'retirement',exact_body:graphWireCanonical(retirement.command),command_hash:retirement.command_hash};
   await checkLearnedSourceReceiptV141(retirement,original,scope);
  }
 }
 return value;
}
export function checkLearnedGeoStagesV141(value,record){
 need(keys(value,'schema,observation_id,observation_version,source_id,entity_id,task_id,collected,data_received,data_status,data_message,deliveries,waiting_match,waiting_message,candidate_only,source_time_verified,physical_state_verified')
  &&value.schema==='native-learned-geo-graph-receipt-stages-v141'&&value.observation_id===record.id&&value.entity_id===record.entity_id
  &&integer(value.observation_version)
  &&value.task_id===record.record?.task_id&&uuid(value.source_id)&&uuid(value.task_id)&&value.collected===true
  &&typeof value.data_received==='boolean'&&['pending','received','not_declared','blocked','unavailable'].includes(value.data_status)
  &&text(value.data_message,2000)&&Array.isArray(value.deliveries)&&value.waiting_match==='not_checked'&&text(value.waiting_message,2000)
  &&value.candidate_only===true&&value.source_time_verified===false&&value.physical_state_verified===false);
 const intents=new Set();for(const delivery of value.deliveries){
  need(keys(delivery,'intent_id,producer_id,source_label,status,admission')&&uuid(delivery.intent_id)&&!intents.has(delivery.intent_id)&&uuid(delivery.producer_id)&&text(delivery.source_label,200)&&['pending','admitted'].includes(delivery.status));intents.add(delivery.intent_id);
  if(delivery.status==='pending')need(delivery.admission===null);
  else need(keys(delivery.admission,'id,sequence,admitted_at')&&uuid(delivery.admission.id)&&integer(delivery.admission.sequence)&&text(delivery.admission.admitted_at,100));
 }return value;
}

// Use the existing durable journal under a separate, family-specific namespace.
// Server request/body/hash schemas are not inferred from absent extractor fields.
export const learnedSourcePrefixV141=scope=>'ga-native-learned-graph-sources-v141:'+scope.workspace_id+':'+scope.actor_id+':'+scope.instance_id+':';
export class LearnedGraphSourceJournalV141 extends NativeGraphSourceJournalV136{
 constructor(options){super(options);this.prefix=learnedSourcePrefixV141(this.scope);}
}
function recoveryCandidate(key,raw,scope){
 const prefix=learnedSourcePrefixV141(scope);need(key.startsWith(prefix)&&typeof raw==='string'&&raw.length<=NATIVE_SOURCE_MAX_RECORD_V136);const value=JSON.parse(raw);need(same(value.scope,scope));
 if(value.schema==='native-graph-source-draft-v136'){
  need(keys(value,'schema,scope,window_id,owner,generation,form')&&uuid(value.owner)&&uuid(value.window_id)&&Number.isSafeInteger(value.generation)&&value.generation>=0);
  need(key===prefix+'form:entry:'+value.owner&&value.generation===0||key===prefix+'form:slot:'+value.owner+':'+value.generation%2&&value.generation>=1);
  learnedSourceFormV141(value.form);return {key,raw,kind:'draft',label:value.form.label.trim()||'未命名的变化候选来源稿',summary:(value.form.source?.name||'尚未选择来源')+' · '+(value.form.capability?.name||'尚未选择已登记能力'),value};
 }
 learnedSourceOutboxV141(value,scope);need(key===prefix+(value.kind==='retirement'?'retirement:':'request:')+value.body.request_id);
 return {key,raw,kind:value.kind==='retirement'?'retirement':'request',label:value.kind==='register'?value.body.label:value.kind==='revoke'?'停止来源的原操作':'结束未确认操作的原请求',summary:'完整原内容保留；选择后只查原结果，不会自动发送。',value};
}
export function readLearnedSourceRecoveryV141(storage,scope,selected){
 scope=nativeSourceScopeV136(scope);need(typeof selected?.key==='string'&&selected.key.startsWith(learnedSourcePrefixV141(scope)));const raw=storage.getItem(selected.key);need(raw===selected.raw);return recoveryCandidate(selected.key,raw,scope);
}
export function enumerateLearnedSourceRecoveryV141(storage,scope,{offset=0,limit=12,scan=120}={}){
 scope=nativeSourceScopeV136(scope);need(Number.isSafeInteger(offset)&&offset>=0&&Number.isSafeInteger(limit)&&limit>=1&&limit<=30&&Number.isSafeInteger(scan)&&scan>=limit&&scan<=500);
 const prefix=learnedSourcePrefixV141(scope),items=[];let problems=0,index=offset,visited=0;const length=storage.length;need(Number.isSafeInteger(length)&&length>=0);
 while(index<length&&items.length<limit&&visited<scan){
  const key=storage.key(index++);visited++;if(typeof key!=='string'||!key.startsWith(prefix))continue;
  if(!['form:entry:','form:slot:','request:','retirement:'].some(part=>key.startsWith(prefix+part)))continue;
  try{items.push(recoveryCandidate(key,storage.getItem(key),scope));}catch{problems++;}
 }
 return {loaded:true,items,problem_count:problems,next_offset:index<length?index:null};
}

