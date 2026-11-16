// Synthetic transport only; never a backend proof or a model result.
import {webcrypto,createHash} from 'node:crypto';
import {graphWireCanonical} from '../../src/full-semantic-graph-wire.mjs';
import {stableStringify} from '../../src/content-signature.js';
const graphWireDigest=async value=>createHash('sha256').update(stableStringify(value)).digest('hex');
import {NATIVE_LEARNED_GEO_RESULT_SCHEMA_V141,learnedGeoOutputSchemaV141,learnedSourceCommandHashV141} from '../../src/native-learned-graph-sources-v141.mjs';
globalThis.crypto??=webcrypto;
export const id=number=>'14100000-0000-4000-8000-'+String(number).padStart(12,'0');
export const account={user_id:id(1),workspace_id:id(2),role:'admin',username:'合成管理员'};
export const scope={actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:id(3)};
export const source={id:id(4),name:'已保存的本地变化候选',content_hash:'a'.repeat(64),release_version:'1.0.0'};
export const trace={installation_id:id(5),pack_id:'changer-r18-binary-v1',generation:2,recipe_hash:'b'.repeat(64),pipeline_hash:'c'.repeat(64),weights_sha256:'d'.repeat(64),backend:'cpu'};
export const clone=value=>JSON.parse(JSON.stringify(value));
export function memory(initial=[]){
 const values=new Map(initial);return {values,reads:[],failWrite:false,failRead:false,get length(){return values.size;},key:index=>[...values.keys()][index]??null,
  getItem(key){this.reads.push(key);if(this.failRead)throw Error('synthetic read failure');return values.get(key)??null;},
  setItem(key,value){if(this.failWrite)throw Error('synthetic quota failure');values.set(key,String(value));},removeItem(key){values.delete(key);}};
}
export function sessionFor(who=account){return memory([['ga-setup-csrf','synthetic-learned-v141'],['ga-setup-csrf-user',who.user_id],['ga-setup-csrf-workspace',who.workspace_id]]);}
export function locks(){const tails=new Map();return {request(key,options,callback){const before=tails.get(key)||Promise.resolve();const result=before.then(callback);tails.set(key,result.then(()=>{},()=>{}));return result;}};}
export async function contract(){
 const value={schema:'graph-event-native-learned-geo-source-contract-v1',adapter_id:'native-learned-binary-change-v1',event_kind:'observation.data',correlation_kind:'entity_id',instance_id:scope.instance_id,source_id:source.id,source_hash:source.content_hash,source_release_version:source.release_version,run_type:'GeoPair',request_kind:'learned_binary_change',capability_id:id(9),capability_hash:'e'.repeat(64),capability_release_version:'1.0.0',model_trace:clone(trace),model_trace_hash:await graphWireDigest(trace),probability_threshold:0.5,alignment:'same_grid',runtime_hash:trace.pipeline_hash,result_schema:clone(NATIVE_LEARNED_GEO_RESULT_SCHEMA_V141),output_schema:learnedGeoOutputSchemaV141()};
 value.output_schema_hash=await graphWireDigest(value.output_schema);value.contract_hash=await graphWireDigest(value);return value;
}
export async function command(request_id=id(6)){return {schema:'graph-event-native-learned-geo-producer-command-v1',request_id,label:'本地变化候选核对结果',adapter_id:'native-learned-binary-change-v1',instance_id:scope.instance_id,source_id:source.id,source_hash:source.content_hash,capability_id:id(9),capability_hash:'e'.repeat(64)};}
export const wire=value=>({schema:'report-full-semantic-graph-canonical-wire-v1',value:clone(value),canonical:stableStringify(value)});
export async function capability(){return {id:id(9),name:'本地学习型变化检测',content_hash:'e'.repeat(64),release_version:'1.0.0',model_trace:clone(trace),model_trace_hash:await graphWireDigest(trace),probability_threshold:0.5,alignment:'same_grid',runtime_hash:trace.pipeline_hash};}
export async function producer(body=null){
 body??=await command();
 const frozen=await contract();return {id:id(7),label:body.label,status:'active',version:1,contract_hash:await graphWireDigest(frozen),contract:frozen,registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash:await learnedSourceCommandHashV141(scope,'register',body),command:clone(body)},execution_granted:false,physical_state_verified:false};
}
export async function outbox(body=null,kind='register',window_id=id(8)){
 body??=await command();
 return {schema:'native-learned-graph-source-outbox-v141',scope:clone(scope),window_id,kind,body:clone(body),exact_body:graphWireCanonical(body),command_hash:await learnedSourceCommandHashV141(scope,kind,body),preview_contract_hash:kind==='register'?await graphWireDigest(await contract()):null};
}
export function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
export async function server(){
 const frozen=await contract(),model=await capability(),saved=new Map(),stopped=new Map(),retired=new Map();
 const state={who:clone(account),calls:[],lost:null,beforePost:null,wrongReply:false,optionsSources:[clone(source)],optionsCapabilities:[model],version:1};
 const base='/workspace/instances/'+scope.instance_id+'/graph-event-native-learned-geo-producers';
 const publicRow=row=>({...clone(row),source_name:source.name,capability_name:model.name,current_availability:{available:row.status==='active',reason_code:row.status==='active'?null:'registration_stopped',label:row.status==='active'?'当前可供明确选择':'已停止供应'},receipt_verified:false});
 function original(kind,nonce){
  const found=kind==='register'?saved.get(nonce):stopped.get(nonce),retirement=retired.get(kind+':'+nonce);
  return {schema:'graph-event-native-learned-geo-source-request-state-v1',instance_id:scope.instance_id,kind,request_id:nonce,outcome:found?'saved':retirement?'retired':'not_found',command_hash:found?(kind==='register'?found.registration.command_hash:found.command_hash):retirement?.original_command_hash??null,saved:found?clone(found):null,retirement:retirement?clone(retirement):null};
 }
 async function api(path,options={}){
  state.calls.push({path,options:clone(options)});
  if(options.headers?.['X-Workspace-ID']!==scope.workspace_id||options.headers?.['X-CSRF-Token']!=='synthetic-learned-v141')throw Error('missing frozen authentication headers');
  if(path==='/me')return clone(state.who);
  if(path.startsWith(base+'/options'))return wire({schema:'graph-event-native-learned-geo-producer-options-v1',instance_id:scope.instance_id,sources:state.optionsSources,capabilities:state.optionsCapabilities,next_after:null,can_register:state.who.role==='admin',reason:null});
  if(path.startsWith(base+'/command-requests/')){const [,kind,nonce]=path.slice(base.length).split('/').slice(1);return wire(original(kind,nonce));}
  if(path.startsWith(base+'/command-retirements/requests/')){const nonce=path.split('/').at(-1),receipt=[...retired.values()].find(value=>value.request_id===nonce);return wire({found:!!receipt,retirement:receipt??null});}
  if(path.startsWith(base+'/registrations/')){const row=[...saved.values()].find(value=>value.id===path.split('/').at(-1));if(!row)throw Object.assign(Error('not found'),{status:404});return wire(publicRow(row));}
  if(path.startsWith(base+'?'))return wire({schema:'graph-event-native-learned-geo-producer-page-v1',instance_id:scope.instance_id,items:[...saved.values()].map(publicRow),next_after:null,can_register:state.who.role==='admin'});
  if(options.method==='POST'){
   const body=JSON.parse(options.body);await state.beforePost?.(path,body,options.body);
   if(state.lost==='before'){state.lost=null;throw Error('synthetic transport response unknown');}
   let response;
   if(path===base+'/preview')response={schema:'graph-event-native-learned-geo-producer-preview-v1',instance_id:scope.instance_id,command:body,contract:frozen,contract_hash:await graphWireDigest(frozen),command_hash:await learnedSourceCommandHashV141(scope,'register',body),execution_granted:false,meaning:'只供应已保存的本地变化候选变化候选，不证明现场状态。'};
   else if(path===base){const row=await producer(body);saved.set(body.request_id,row);response={producer:clone(row),created:true};if(state.wrongReply)response.producer.registration.command.request_id=id(999);}
   else if(path===base+'/revocations'){
    const row=[...saved.values()].find(value=>value.id===body.producer_id);if(!row||row.version!==body.producer_version)throw Object.assign(Error('source version changed'),{status:409});row.status='revoked';row.version++;
    const receipt={schema:'graph-event-native-learned-geo-producer-revocation-receipt-v1',id:id(31),request_id:body.request_id,actor_id:scope.actor_id,instance_id:scope.instance_id,producer_id:body.producer_id,command_hash:await learnedSourceCommandHashV141(scope,'revoke',body),command:body,result:{status:'revoked',version:body.producer_version+1,contract_hash:body.contract_hash}};stopped.set(body.request_id,receipt);response={receipt,replayed:false};
   }else if(path===base+'/command-retirements'){
    if(original(body.kind,body.original.request_id).outcome!=='not_found')throw Object.assign(Error('original already saved'),{status:409});
    const originalHash=await learnedSourceCommandHashV141(scope,body.kind,body.original),receipt={schema:'graph-event-native-learned-geo-source-retirement-receipt-v1',id:id(32),request_id:body.request_id,actor_id:scope.actor_id,instance_id:scope.instance_id,kind:body.kind,original_request_id:body.original.request_id,original_command_hash:originalHash,command_hash:await learnedSourceCommandHashV141(scope,'retirement',body),command:body,result:{outcome:'retired',kind:body.kind,original_request_id:body.original.request_id,original_command_hash:originalHash}};retired.set(body.kind+':'+body.original.request_id,receipt);response={retirement:receipt,replayed:false};
   }else throw Error('unexpected POST '+path);
   if(state.lost==='after'&&path!==base+'/preview'){state.lost=null;throw Error('synthetic committed response unknown');}return wire(response);
  }
  throw Error('unexpected request '+path);
 }
 return {api,state,base,saved,stopped,retired,original};
}

