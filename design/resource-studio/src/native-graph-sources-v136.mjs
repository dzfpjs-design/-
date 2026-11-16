// Explicit AudioRun contract family. No missing extractor is treated as native.
import {graphWireCanonical,graphWireDigest,graphWireParseCanonical} from './full-semantic-graph-wire.mjs';
import {nativeSourceScopeV136} from './native-graph-sources-journal-v136.mjs';
export const NATIVE_AUDIO_ADAPTER_V136='native-audio-transcription-v1';
export const NATIVE_AUDIO_CONTRACT_V136='graph-event-native-audio-source-contract-v1';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,names)=>object(value)&&Object.keys(value).length===names.split(',').length&&names.split(',').every(key=>Object.hasOwn(value,key));
const safe=(value,max=1000)=>typeof value==='string'&&[...value].length<=max&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
export const nativeSourceTrimV136=value=>value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
const text=(value,max=1000)=>safe(value,max)&&nativeSourceTrimV136(value).length>0;
const uuid=value=>typeof value==='string'&&UUID.test(value),sha=value=>typeof value==='string'&&SHA.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=1;
const same=(left,right)=>graphWireCanonical(left)===graphWireCanonical(right);
const need=(value,message='本地语音来源、原命令或保存回执无法完整核对；原稿和原请求保留。')=>{if(!value)throw Error(message);};
const clone=value=>JSON.parse(JSON.stringify(value));
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
export const NATIVE_AUDIO_RESULT_SCHEMA_V136=freeze({type:'object',additionalProperties:false,required:['attributes'],properties:{attributes:{type:'object',additionalProperties:false,required:['已执行整片转写','机器转写文字'],properties:{'已执行整片转写':{type:'boolean'},'机器转写文字':{type:'string',maxLength:12000}}}}});
export function nativeAudioOutputSchemaV136(){
 const properties=Object.fromEntries(['adapter_id','observation_id','entity_id','source_id','run_id','task_id','business_day','date_basis'].map(key=>[key,{type:'string'}]));
 Object.assign(properties,{schema:{const:'graph-event-native-audio-output-v1'},observed_at:{type:'null'},candidate_only:{const:true},source_time_verified:{const:false},physical_state_verified:{const:false},entity_state_updated:{const:false},result:NATIVE_AUDIO_RESULT_SCHEMA_V136});
 return {type:'object',additionalProperties:false,required:Object.keys(properties),properties};
}
export function nativeInstallationTraceV136(value){
 need(keys(value,'installation_id,pack_id,generation,recipe_hash,backend')&&uuid(value.installation_id)
  &&value.pack_id==='sensevoice-small-v1'&&integer(value.generation)&&sha(value.recipe_hash)&&value.backend==='cpu');return value;
}
export function nativeGraphSourceContractV136(value){
 need(keys(value,'schema,adapter_id,event_kind,correlation_kind,instance_id,source_id,source_hash,source_release_version,run_type,installation,installation_hash,result_schema,output_schema,output_schema_hash,contract_hash')
  &&value.schema===NATIVE_AUDIO_CONTRACT_V136&&value.adapter_id===NATIVE_AUDIO_ADAPTER_V136
  &&value.event_kind==='observation.data'&&value.correlation_kind==='entity_id'&&value.run_type==='AudioRun'
  &&uuid(value.instance_id)&&uuid(value.source_id)&&sha(value.source_hash)&&text(value.source_release_version,100)
  &&sha(value.installation_hash)&&sha(value.output_schema_hash)&&sha(value.contract_hash));
 nativeInstallationTraceV136(value.installation);
 need(same(value.result_schema,NATIVE_AUDIO_RESULT_SCHEMA_V136)&&same(value.output_schema,nativeAudioOutputSchemaV136()));return value;
}
export async function verifyNativeGraphSourceContractV136(value){
 nativeGraphSourceContractV136(value);
 need(await graphWireDigest(value.installation)===value.installation_hash
  &&await graphWireDigest(value.output_schema)===value.output_schema_hash
  &&await graphWireDigest(value,{omit:'contract_hash'})===value.contract_hash);return value;
}
export function nativeSourceCommandV136(value,kind,scope){
 scope=nativeSourceScopeV136(scope);
 if(kind==='register')need(keys(value,'schema,request_id,label,adapter_id,instance_id,source_id,source_hash,installation_id,installation_hash')
  &&value.schema==='graph-event-native-audio-producer-command-v1'&&uuid(value.request_id)&&text(value.label,200)
  &&value.adapter_id===NATIVE_AUDIO_ADAPTER_V136&&value.instance_id===scope.instance_id
  &&uuid(value.source_id)&&sha(value.source_hash)&&uuid(value.installation_id)&&sha(value.installation_hash));
 else if(kind==='revoke')need(keys(value,'schema,request_id,instance_id,producer_id,producer_version,contract_hash,reason')
  &&value.schema==='graph-event-native-audio-producer-revocation-v1'&&uuid(value.request_id)&&value.instance_id===scope.instance_id
  &&uuid(value.producer_id)&&integer(value.producer_version)&&sha(value.contract_hash)&&text(value.reason)&&value.reason===nativeSourceTrimV136(value.reason));
 else{
  need(kind==='retirement'&&keys(value,'schema,request_id,instance_id,kind,original,reason')
   &&value.schema==='graph-event-native-audio-source-retirement-v1'&&uuid(value.request_id)&&value.instance_id===scope.instance_id
   &&['register','revoke'].includes(value.kind)&&text(value.reason)&&value.reason===nativeSourceTrimV136(value.reason));
  nativeSourceCommandV136(value.original,value.kind,scope);need(value.request_id!==value.original.request_id);
 }
 return value;
}
const hashSchemas={register:'graph-event-native-audio-producer-command-hash-v1',revoke:'graph-event-native-audio-producer-revocation-command-hash-v1',retirement:'graph-event-native-audio-source-retirement-command-hash-v1'};
export async function nativeSourceCommandHashV136(scope,kind,command){
 nativeSourceCommandV136(command,kind,scope);return graphWireDigest({schema:hashSchemas[kind],workspace_id:scope.workspace_id,actor_id:scope.actor_id,command});
}
export function emptyNativeSourceFormV136(){return {label:'',source:null,installation:null,selected_registration:null,reasons:{}};}
function sourceRow(value){need(keys(value,'id,name,content_hash,release_version')&&uuid(value.id)&&text(value.name,300)&&sha(value.content_hash)&&text(value.release_version,100));return value;}
function installationRow(value){need(keys(value,'id,name,installation_hash,trace')&&uuid(value.id)&&text(value.name,300)&&sha(value.installation_hash));nativeInstallationTraceV136(value.trace);need(value.id===value.trace.installation_id);return value;}
export function nativeSourceFormV136(value){
 need(keys(value,'label,source,installation,selected_registration,reasons')&&safe(value.label,200)
  &&(value.selected_registration===null||uuid(value.selected_registration))&&object(value.reasons));
 if(value.source!==null)sourceRow(value.source);if(value.installation!==null)installationRow(value.installation);
 for(const [identity,reason] of Object.entries(value.reasons))need(uuid(identity)&&safe(reason));return clone(value);
}
export function nativeSourceOutboxV136(value,scope){
 need(keys(value,'schema,scope,window_id,kind,body,exact_body,command_hash,preview_contract_hash')
  &&value.schema==='native-graph-source-outbox-v136'&&same(value.scope,nativeSourceScopeV136(scope))&&uuid(value.window_id)
  &&['register','revoke','retirement'].includes(value.kind)&&sha(value.command_hash)&&typeof value.exact_body==='string'
  &&(value.kind==='register'?sha(value.preview_contract_hash):value.preview_contract_hash===null));
 const original=graphWireParseCanonical(value.exact_body);nativeSourceCommandV136(original,value.kind,scope);
 need(same(original,value.body));return value;
}
export async function checkNativeSourceOptionsV136(value,scope){
 need(keys(value,'schema,instance_id,sources,installations,next_after,can_register,reason')
  &&value.schema==='graph-event-native-audio-producer-options-v1'&&value.instance_id===scope.instance_id
  &&Array.isArray(value.sources)&&value.sources.length<=20&&Array.isArray(value.installations)&&value.installations.length<=1
  &&(value.next_after===null||uuid(value.next_after))&&typeof value.can_register==='boolean'
  &&(value.reason===null||text(value.reason,2000)));
 need(new Set(value.sources.map(row=>row.id)).size===value.sources.length);
 value.sources.forEach(sourceRow);for(const row of value.installations){installationRow(row);need(await graphWireDigest(row.trace)===row.installation_hash);}return value;
}
function availability(value){need(keys(value,'available,reason_code,label')&&typeof value.available==='boolean'&&(value.reason_code===null||text(value.reason_code,100))&&text(value.label,2000));}
export async function checkNativeSourceProducerV136(value,scope,{catalog=false}={}){
 need(keys(value,'id,label,status,version,contract_hash,contract,registration,execution_granted,physical_state_verified'+(catalog?',source_name,installation_name,current_availability,receipt_verified':''))
  &&uuid(value.id)&&text(value.label,200)&&['active','revoked'].includes(value.status)&&integer(value.version)
  &&sha(value.contract_hash)&&value.execution_granted===false&&value.physical_state_verified===false);
 await verifyNativeGraphSourceContractV136(value.contract);need(value.contract.instance_id===scope.instance_id&&await graphWireDigest(value.contract)===value.contract_hash);
 const registration=value.registration;need(keys(registration,'request_id,actor_id,command_hash,command')&&uuid(registration.actor_id)&&uuid(registration.request_id)&&sha(registration.command_hash));
 nativeSourceCommandV136(registration.command,'register',scope);
 need(registration.request_id===registration.command.request_id&&registration.command.label===value.label
  &&registration.command.source_id===value.contract.source_id&&registration.command.source_hash===value.contract.source_hash
  &&registration.command.installation_id===value.contract.installation.installation_id&&registration.command.installation_hash===value.contract.installation_hash);
 need(await nativeSourceCommandHashV136({...scope,actor_id:registration.actor_id},'register',registration.command)===registration.command_hash);
 if(catalog){need((value.source_name===null||text(value.source_name,300))&&(value.installation_name===null||text(value.installation_name,300))&&value.receipt_verified===false);availability(value.current_availability);}
 return value;
}
export async function checkNativeSourcePreviewV136(value,body,scope){
 need(keys(value,'schema,instance_id,command,contract,contract_hash,command_hash,execution_granted,meaning')&&value.schema==='graph-event-native-audio-producer-preview-v1'
  &&value.instance_id===scope.instance_id&&same(value.command,body)&&value.execution_granted===false&&text(value.meaning,2000));
 await verifyNativeGraphSourceContractV136(value.contract);
 need(value.contract.instance_id===scope.instance_id&&value.contract.source_id===body.source_id&&value.contract.source_hash===body.source_hash
  &&value.contract.installation.installation_id===body.installation_id&&value.contract.installation_hash===body.installation_hash
  &&await graphWireDigest(value.contract)===value.contract_hash&&await nativeSourceCommandHashV136(scope,'register',body)===value.command_hash);return value;
}
export async function checkNativeSourceReceiptV136(receipt,pending,scope){
 const command=graphWireParseCanonical(pending.exact_body),signature=await nativeSourceCommandHashV136(scope,pending.kind,command);
 need(signature===pending.command_hash);
 if(pending.kind==='register'){
  await checkNativeSourceProducerV136(receipt,scope);need(receipt.registration.actor_id===scope.actor_id&&same(receipt.registration.command,command)
   &&receipt.registration.command_hash===signature&&receipt.registration.request_id===command.request_id&&receipt.contract_hash===pending.preview_contract_hash);
 }else if(pending.kind==='revoke'){
  need(keys(receipt,'schema,id,request_id,actor_id,instance_id,producer_id,command_hash,command,result')&&receipt.schema==='graph-event-native-audio-producer-revocation-receipt-v1'
   &&uuid(receipt.id)&&receipt.request_id===command.request_id&&receipt.actor_id===scope.actor_id&&receipt.instance_id===scope.instance_id
   &&receipt.producer_id===command.producer_id&&receipt.command_hash===signature&&same(receipt.command,command)
   &&keys(receipt.result,'status,version,contract_hash')&&receipt.result.status==='revoked'&&receipt.result.version===command.producer_version+1&&receipt.result.contract_hash===command.contract_hash);
 }else{
  need(keys(receipt,'schema,id,request_id,actor_id,instance_id,kind,original_request_id,original_command_hash,command_hash,command,result')
   &&receipt.schema==='graph-event-native-audio-source-retirement-receipt-v1'&&uuid(receipt.id)&&receipt.request_id===command.request_id
   &&receipt.actor_id===scope.actor_id&&receipt.instance_id===scope.instance_id&&receipt.kind===command.kind
   &&receipt.original_request_id===command.original.request_id&&receipt.command_hash===signature&&same(receipt.command,command)
   &&receipt.original_command_hash===await nativeSourceCommandHashV136(scope,command.kind,command.original)
   &&keys(receipt.result,'outcome,kind,original_request_id,original_command_hash')&&receipt.result.outcome==='retired'
   &&receipt.result.kind===command.kind&&receipt.result.original_request_id===command.original.request_id&&receipt.result.original_command_hash===receipt.original_command_hash);
 }
 return receipt;
}
export async function checkNativeSourceStateV136(value,pending,scope){
 const body=graphWireParseCanonical(pending.exact_body);need(keys(value,'schema,instance_id,kind,request_id,outcome,command_hash,saved,retirement')
  &&value.schema==='graph-event-native-audio-source-request-state-v1'&&value.instance_id===scope.instance_id
  &&value.kind===pending.kind&&value.request_id===body.request_id&&['not_found','saved','retired'].includes(value.outcome));
 if(value.outcome==='not_found')need(value.command_hash===null&&value.saved===null&&value.retirement===null);
 else{
  need(value.command_hash===pending.command_hash);
  if(value.outcome==='saved'){need(value.retirement===null);await checkNativeSourceReceiptV136(value.saved,pending,scope);}
  else{
   need(value.saved===null&&value.retirement?.command?.kind===pending.kind&&same(value.retirement.command.original,body));
   const retirement=value.retirement,original={kind:'retirement',exact_body:graphWireCanonical(retirement.command),command_hash:retirement.command_hash};
   await checkNativeSourceReceiptV136(retirement,original,scope);
  }
 }
 return value;
}
export function checkNativeAudioStagesV136(value,record){
 need(keys(value,'schema,observation_id,source_id,entity_id,task_id,collected,data_received,data_status,data_message,deliveries,waiting_match,waiting_message,candidate_only,source_time_verified,physical_state_verified')
  &&value.schema==='native-audio-graph-receipt-stages-v136'&&value.observation_id===record.id&&value.entity_id===record.entity_id
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
