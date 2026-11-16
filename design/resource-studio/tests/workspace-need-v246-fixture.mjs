// Synthetic transport for actual controller/React checks. It does not prove a
// real receiver, Postgres, user browser or quantity-model accuracy.
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {needCanonical as canonical,needScope,needPrefix,blankNeedDraft} from '../src/workspace-need-v246-protocol.mjs';
let Controller;
export async function initializeNeedController(){assert.ok(globalThis.window?.document&&globalThis.localStorage&&globalThis.sessionStorage,'establish browser globals before production runtime-api import');({WorkspaceNeedControllerV246:Controller}=await import('../src/workspace-need-v246-controller.mjs'));}
export const id=n=>'24600000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>structuredClone(v),sha=v=>createHash('sha256').update(v).digest('hex');
export class Memory{constructor(){this.rows=new Map();this.reads=[];this.failSet=null;this.failRemove=false;}get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}getItem(k){this.reads.push(k);return this.rows.get(k)??null;}setItem(k,v){if(this.failSet?.(k))throw Error('本机人稿保存失败');this.rows.set(k,String(v));}removeItem(k){if(!this.failRemove)this.rows.delete(k);}}
export function needFixture(){
 const account={user_id:id(1),workspace_id:id(2),username:'数量资料核查人',role:'admin'},instanceId=id(3),entityId=id(4),releaseId=id(5),businessId='business_quantity',field='field_count_registered',label='登记数量',question='这块地当前登记的数量是多少？',scope=needScope(account,instanceId),prefix=needPrefix(scope),base='/workspace/instances/'+instanceId+'/data-needs',storage=new Memory(),session=new Memory(),csrf='workspace-need-v246-test-csrf';
 for(const[k,v]of Object.entries({'ga-setup-csrf':csrf,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(k,v);
 const tails=new Map(),locks={request:async(key,options,fn)=>{const prior=tails.get(key)||Promise.resolve(),next=prior.catch(()=>{}).then(fn);tails.set(key,next);try{return await next;}finally{if(tails.get(key)===next)tails.delete(key);}}};
 const meta={key:field,label,kind:'整数',unit:'株',schema_hash:sha(canonical({type:'integer',minimum:0}))},originalBody={request_id:id(11),release_id:releaseId,business_id:businessId,entity_id:entityId,purpose:question,fields:[field],source:'observations',confirmation:'confirmed',max_age_hours:24};
 const state={identity:copy(account),currentRelease:releaseId,fieldPresent:true,businessPresent:true,entityPresent:true,targetMeta:copy(meta),version:1,mode:'commit',posts:[],created:new Map(),identityGate:null,optionGate:null,detailGate:null,optionReads:0,detailReads:0,next:30,lookupVisible:true,lookupEvidence:'legacy_job_context'};
 const options=()=>({release_id:state.currentRelease,businesses:state.businessPresent?[{id:businessId,name:'数量资料核查'}]:[],objects:state.entityPresent?[{id:entityId,name:'测试样地',fields:state.fieldPresent?[field]:[],field_labels:{[field]:label}}]:[],objects_truncated:false,existing_records_only:true});
 function descriptor(body=originalBody,queryId=id(10),version=state.version){
  const conditions={purpose:body.purpose,fields:copy(body.fields),source:body.source,confirmation:body.source==='archive'?null:body.confirmation,max_age_hours:body.source==='archive'?null:body.max_age_hours},source_fields=body.fields.map(k=>({...copy(meta),key:k})),targetFields=state.fieldPresent?body.fields.map(k=>({...copy(state.targetMeta),key:k})):[],reasons=[];
  if(!state.businessPresent)reasons.push('原业务当前未启用，请重新选择业务');if(!state.entityPresent)reasons.push('当前对象范围不可用于复制');if(!state.fieldPresent||canonical(source_fields)!==canonical(targetFields))reasons.push('当前字段类型、单位或完整约束与原条件不一致，请重新选择字段');
  if(!['admin','operator'].includes(state.identity.role))reasons.push('当前账号只读，不能复制为新的提交草稿');
  const value={schema:'workspace-data-need-copy-v246',source:{query_id:queryId,query_version:version,request_id:body.request_id,body_hash:sha(canonical(body)),context_hash:sha('saved-context-'+queryId),request_hash:sha('prepared-request-'+queryId),instance_id:instanceId,release_id:body.release_id,business_id:body.business_id,entity_id:body.entity_id,object_definition_hash:'a'.repeat(64)},conditions,source_fields,target:{instance_id:instanceId,release_id:state.currentRelease,business_id:body.business_id,entity_id:body.entity_id,object_definition_hash:'a'.repeat(64),fields:targetFields},can_copy:!reasons.length,reasons};
  return {...value,descriptor_hash:sha(canonical(value))};
 }
 function view(body=originalBody,queryId=id(10),version=state.version,status='needs_clarification',detail=true){
  const clarifications=status==='needs_clarification'?['原查询把已选数量字段误当作聚合，原问题需要核对后另问']:[],requirement={source:body.source,minimum_verification:body.source==='archive'?'recorded':body.confirmation,time:body.source==='archive'?{mode:'archive',timezone:'Asia/Shanghai'}:{mode:'latest',max_age_seconds:body.max_age_hours*3600,timezone:'Asia/Shanghai'},minimum_records_per_entity:1};
  const v={id:queryId,version,entity_id:body.entity_id,object_name:'测试样地',business_name:'数量资料核查',field_labels:{[field]:label},purpose:body.purpose,fields:copy(body.fields),requirement,status,query_status:status,result_status:status,scope_valid:true,business_received:false,reason:clarifications[0]??null,background:null,created_at:'2026-10-30T01:02:03+00:00',existing_records_only:true,parent_goal_completed:false,clarifications,error:clarifications[0]??null,copy_descriptor:descriptor(body,queryId,version)};
  if(detail){if(body.source==='archive'){v.records=[];v.archive_evidence=[{fields:{[field]:27},version:1,observed_at:null}];}else v.records=[{observation_id:id(20),version:3,source_name:'已保存数量登记',original_values:{[field]:27},extracted_values:{[field]:27},reported_at:'2026-10-30T01:00:00+00:00',review:'confirmed',review_receipt:{decision:'confirmed',reason:'只核对登记与提取一致',reviewed_at:'2026-10-30T01:01:00+00:00',physical_state_verified:false},review_history:[],review_history_truncated:false,review_input_hash:'b'.repeat(64),physical_state_verified:false}];}
  return v;
 }
 const api=async(path,optionsArg={})=>{
  if(path==='/me'){assert.equal(optionsArg.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(optionsArg.headers['X-CSRF-Token'],csrf);if(state.identityGate)await state.identityGate;return copy(state.identity);}
  if(path===base+'/options'){state.optionReads++;if(state.optionGate)await state.optionGate;return options();}
  if(path.startsWith('/workspace/instances/'+instanceId+'/data-need-requests?')){
   assert.ok(!optionsArg.method||optionsArg.method==='GET');const url=new URL(path,'https://need-fixture.invalid'),requestID=url.searchParams.get('request_id'),bodyHash=url.searchParams.get('expected_body_hash');assert.deepEqual([...url.searchParams.keys()].sort(),['expected_body_hash','request_id']);
   const row=state.lookupVisible?(state.created.get(requestID)||(requestID===originalBody.request_id?{body:originalBody,id:id(10)}:null)):null;
   const response={schema:'workspace-data-need-request-recovery-v248',instance_id:instanceId,request_id:requestID,body_hash:bodyHash,found:!!row,request:null,query_path:null,read_only:true,device_actions:false,cloud_calls:0};
   if(!row)return response;assert.equal(bodyHash,sha(canonical(row.body)));const d=descriptor(row.body,row.id,row.id===id(10)?state.version:1),typed=state.lookupEvidence==='typed_body_receipt';
   response.request={query_id:row.id,query_version:d.source.query_version,actor_id:account.user_id,instance_id:instanceId,release_id:row.body.release_id,business_id:row.body.business_id,entity_id:row.body.entity_id,request_id:requestID,body_hash:bodyHash,context_hash:d.source.context_hash,query_request_hash:d.source.request_hash,evidence_level:state.lookupEvidence,typed_body:typed?copy(row.body):null,receipt_hash:typed?'d'.repeat(64):null,creation_audit_id:id(60),initial_job_id:id(61),typed_receipt_audit_id:typed?id(62):null};
   response.query_path='/api'+base+'/'+row.id;return response;
  }
  if(path===base&&optionsArg.method!=='POST')return {items:[view(originalBody,id(10),state.version,'needs_clarification',false),...Array.from(state.created.values(),r=>view(r.body,r.id,1,'sufficient',false))],truncated:false};
  if(path===base&&optionsArg.method==='POST'){
   const p=JSON.parse(storage.getItem(prefix+'pending'));assert.equal(p.raw,optionsArg.body);assert.equal(p.sha256,sha(optionsArg.body));assert.equal(p.body_hash,sha(canonical(JSON.parse(optionsArg.body))));assert.equal(storage.getItem(prefix+'original:'+p.body.request_id),JSON.stringify(p));const body=JSON.parse(optionsArg.body);state.posts.push({raw:optionsArg.body,body});
   if(state.mode==='unknown')throw Error('原资料需求回复未知');
   let row=state.created.get(body.request_id);if(!row){assert.equal(body.release_id,state.currentRelease);row={body:copy(body),id:id(state.next++)};state.created.set(body.request_id,row);}else assert.equal(sha(canonical(body)),sha(canonical(row.body)));
   if(state.mode==='lost')throw Error('资料需求已经保存但回复丢失');return view(row.body,row.id,1,'sufficient',false);
  }
  if(path.startsWith(base+'/')){state.detailReads++;if(state.detailGate)await state.detailGate;const query=path.slice((base+'/').length);if(query===id(10))return view();const row=[...state.created.values()].find(r=>r.id===query);assert.ok(row,'detail must select a real fixture row');return view(row.body,row.id,1,'sufficient');}
  throw Object.assign(Error('本合成场景未启用此附属能力：'+path),{status:503});
 };
 const make=extra=>{assert.equal(typeof Controller,'function');return new Controller({account,instanceId,api,storage,session,locks,crypto:webcrypto,...extra});};
 async function ready(c=make()){await c.start();c.edit({business_id:businessId,entity_id:entityId,purpose:'当前人稿需要保留',fields:[field]});assert.equal(c.localSaved,true);return c;}
 const object={id:entityId,name:'测试样地',entity_type:'plot',type_name:'样地',version:1,attributes:{name:'测试样地'},object_compatibility:{status:'current',usable:true,target_release_id:releaseId,source_release_number:1,source_type:'样地',source_label:'测试样地',reason:null}},business={id:businessId,name:'数量资料核查',available:true,execution_mode:'confirmed_human_workflow',flow:{steps:[{id:'human',name:'核查登记',agent:'人工确认',role:'负责人',purpose:'只核对原记录',output:'核查记录'}]}},release={id:releaseId,instance_id:instanceId,package_id:id(6),number:1,configuration_hash:'c'.repeat(64),snapshot:{object_types:[{key:'plot',name:'样地',fields:[{key:'name',label:'名称',kind:'文本',required:true,unit:null},{key:field,label,kind:'整数',required:false,unit:'株'}]}],businesses:[business],relationships:[]}},instance={id:instanceId,name:'数量核查实例',domain:'登记资料核查',version:1,status:'partially_active',active_release_id:releaseId,enabled_business_ids:[businessId]};
 return {account,instanceId,entityId,releaseId,businessId,field,label,question,scope,prefix,base,storage,session,locks,csrf,state,options,descriptor,view,api,make,ready,originalBody,object,business,release,instance};
}
