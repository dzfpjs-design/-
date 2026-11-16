// Synthetic closed transport only. Real HTTP/ACK/PG/worker and browser proof
// belongs to Root; these fixtures cannot establish it.
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {httpApprovalCanonical as canonical,httpApprovalScope,httpApprovalPrefix} from '../src/http-signal-approval-v247-protocol.mjs';
export const id=n=>'24700000-0000-4000-8000-'+String(n).padStart(12,'0');
export const copy=value=>structuredClone(value),sha=value=>createHash('sha256').update(value).digest('hex');
export const seal=(value,key)=>({...copy(value),[key]:sha(canonical(value))});
let Controller;
export async function initializeHTTPApprovalController(){assert.ok(globalThis.window?.document&&globalThis.localStorage&&globalThis.sessionStorage,'Browser globals must exist before importing production runtime-api');({HTTPSignalApprovalControllerV247:Controller}=await import('../src/http-signal-approval-v247-controller.mjs'));}
export class Memory{
 constructor(){this.rows=new Map();this.failSet=null;this.failRemove=false;}
 get length(){return this.rows.size;}key(index){return [...this.rows.keys()][index]??null;}getItem(key){return this.rows.get(key)??null;}
 setItem(key,value){if(this.failSet?.(key))throw Error('本机授权人稿写入失败');this.rows.set(key,String(value));}
 removeItem(key){if(!this.failRemove)this.rows.delete(key);}
}
export function httpApprovalFixture(){
 const account={user_id:id(1),workspace_id:id(2),username:'异常草稿核对人',role:'operator'},instanceId=id(3),releaseID=id(4),entityID=id(5),sourceID=id(6),extractorID=id(7),routeID=id(8),taskID=id(9),businessID='business_http_numeric_v247',scope=httpApprovalScope(account,instanceId),prefix=httpApprovalPrefix(scope),base='/workspace/instances/'+instanceId+'/signal-approval-routing',storage=new Memory(),session=new Memory(),csrf='synthetic-http-approval-csrf';
 for(const[key,value]of Object.entries({'ga-setup-csrf':csrf,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(key,value);
 const tails=new Map(),locks={request:async(key,_options,action)=>{const next=(tails.get(key)||Promise.resolve()).catch(()=>{}).then(action);tails.set(key,next);try{return await next;}finally{if(tails.get(key)===next)tails.delete(key);}}};
 const source={id:sourceID,name:'HTTP 温湿度模拟登记',version:2,content_hash:'a'.repeat(64)},extractor={id:extractorID,name:'已发布温湿度规则',version:3,content_hash:'b'.repeat(64)},entity={id:entityID,name:'东侧样地',definition_hash:'c'.repeat(64)},policy={id:id(10),version_id:id(11),hash:'d'.repeat(64)};
 const state={identity:copy(account),version:1,releaseID,mode:'normal',recoveryVisible:true,commands:new Map(),posts:[],reads:[],routes:[],occurrences:[],pages:null,postGate:null,optionsGate:null,detailGate:null,businessAvailable:true,sourceIssue:null,sourceFound:true,sourceGate:null};
 const options=()=>seal({schema:'signal-approval-options-v247',instance_id:instanceId,instance_version:state.version,release_id:state.releaseID,sources:[{...source,extractor_id:extractorID,simulation:true,field_verified:false}],extractors:[extractor],entities:[entity],businesses:[{id:businessID,name:'数值异常人工复核',execution_mode:'confirmed_human_workflow',input_provider:'none',required_inputs:[],can_create_approval_draft:state.businessAvailable,blocking_reason:state.businessAvailable?null:'正式审批策略已停用',target_hash:state.businessAvailable?'e'.repeat(64):null,approval_policy:state.businessAvailable?policy:null},{id:'business_image_v243',name:'图像原件技术复核',execution_mode:'confirmed_graph_workflow',input_provider:'none',required_inputs:['task_image_ids'],can_create_approval_draft:false,blocking_reason:'本次数值异常没有原图像，不能采用图像业务',target_hash:null,approval_policy:null}],limits:{valid_for_hours_default:24,valid_for_hours_max:24,max_tasks_default:10,max_tasks_max:100},can_enable:state.businessAvailable&&!state.routes.some(row=>row.status==='active'),blocking_reason:state.routes.some(row=>row.status==='active')?'本来源已有有效授权':state.businessAvailable?null:'正式审批策略已停用'},'options_hash');
 const route=(body,status='active')=>({id:routeID,release_id:body.release_id,status,source:copy(source),extractor:copy(extractor),entities:[copy(entity)],business:{id:body.business_id,name:'数值异常人工复核',execution_mode:'confirmed_human_workflow'},enabled_at:'2026-10-29T01:00:00+00:00',expires_at:new Date(Date.parse('2026-10-29T01:00:00+00:00')+body.valid_for_hours*3600000).toISOString(),max_tasks:body.max_tasks,used_tasks:0,reason:body.reason,grant_hash:'f'.repeat(64)});
 function occurrence({n=20,status='waiting_data',task_id=null,version=1,created_at='2026-10-29T01:01:00+00:00',reason=status==='waiting_data'?'原观测尚待数据管理独立收件':null}={}){return {id:id(n),version,route_id:routeID,signal_id:id(n+100),observation_id:id(n+200),source_identity_hash:sha('source-identity-'+n),status,reason,created_at,updated_at:created_at,task_id,task_path:task_id?'/api/workspace/tasks/'+task_id:null,receipt_hash:task_id?'1'.repeat(64):null};}
 const routes=()=>({schema:'signal-approval-routes-v247',instance_id:instanceId,instance_version:state.version,routes:copy(state.routes)});
 const sourceView=()=>({schema:'signal-approval-source-view-v247',task_id:taskID,found:state.sourceFound,source:!state.sourceFound||state.sourceIssue?null:{instance_id:instanceId,signal_id:id(120),observation_id:id(220),source:{id:sourceID,name:source.name},object:{id:entityID,name:entity.name},observed_at:'2026-10-29T01:01:00+00:00',source_time_verified:false,simulation:true,field_verified:false,data_received:true,receipt_hash:'2'.repeat(64),fields:[{key:'temperature',label:'温度',unit:'℃',value:27},{key:'humidity',label:'湿度',unit:'%',value:75.5}]},integrity_issue:state.sourceFound?state.sourceIssue:null});
 const api=async(path,request={})=>{
  const method=request.method||'GET';state.reads.push({path,method});
  if(path==='/me'){assert.equal(request.headers?.['X-Workspace-ID'],scope.workspace_id);assert.equal(request.headers?.['X-CSRF-Token'],csrf);return copy(state.identity);}
  if(path===base+'/options'){if(state.optionsGate)await state.optionsGate;return options();}
  if(path===base&&method==='GET')return routes();
  if(path.startsWith(base+'/requests/')){
   const url=new URL(path,'https://fixture.invalid'),nonce=url.pathname.split('/').at(-1),saved=state.commands.get(nonce),pending=JSON.parse(storage.getItem(prefix+'pending'));
   assert.equal(url.searchParams.get('raw_sha256'),pending.raw_sha256);assert.equal(nonce,pending.body.request_id);
   return {schema:'signal-approval-recovery-v247',found:!!saved&&state.recoveryVisible,request_id:nonce,raw:saved&&state.recoveryVisible?saved.raw:null,raw_sha256:saved&&state.recoveryVisible?sha(saved.raw):null,result:saved&&state.recoveryVisible?copy(saved.result):null,current:saved&&state.recoveryVisible?copy(state.routes.find(row=>row.id===saved.result.route.id)):null};
  }
  if(path.startsWith(base+'/occurrences?')){
   const url=new URL(path,'https://fixture.invalid'),after=url.searchParams.get('after');assert.equal(url.searchParams.get('limit'),'20');
   if(state.pages){const page=state.pages.get(after||'head');assert.ok(page,'Only explicit synthetic cursors are supported');return {schema:'signal-approval-occurrences-v247',instance_id:instanceId,...copy(page)};}
   return {schema:'signal-approval-occurrences-v247',instance_id:instanceId,items:copy(state.occurrences),next_cursor:null};
  }
  if(path.startsWith(base+'/occurrences/')&&method==='GET'){if(state.detailGate)await state.detailGate;const row=state.occurrences.find(value=>value.id===path.split('/').at(-1));assert.ok(row);return copy(row);}
  if(path===base+'/enable'||method==='POST'&&(path===base+'/'+routeID+'/pause'||path.endsWith('/retry'))){
   assert.equal(method,'POST');const p=JSON.parse(storage.getItem(prefix+'pending'));assert.equal(p.raw,request.body);assert.equal(p.raw_sha256,sha(request.body));assert.equal(p.body_hash,sha(canonical(JSON.parse(request.body))));assert.equal(storage.getItem(prefix+'original:'+p.body.request_id),JSON.stringify(p));
   state.posts.push({path,raw:request.body,body:JSON.parse(request.body)});if(state.postGate)await state.postGate;if(state.mode==='unknown')throw Error('原 HTTP 授权回复未知');
   let saved=state.commands.get(p.body.request_id);if(saved){assert.equal(saved.raw,p.raw);return copy(saved.result);}
   const body=JSON.parse(request.body),operation=p.operation;
   if(operation==='enable'){assert.equal(body.expected_version,state.version);assert.equal(body.release_id,state.releaseID);assert.equal(body.options_hash,options().options_hash);assert.equal(body.confirm_approval_drafts,true);state.version++;state.routes=[route(body)];}
   else if(operation==='pause'){assert.equal(body.expected_version,state.version);state.version++;state.routes[0]={...state.routes[0],status:'paused'};}
   else{const item=state.occurrences.find(row=>row.id===p.target_id);assert.ok(item);assert.equal(body.expected_version,item.version);}
   const receipt=seal({request_id:body.request_id,operation,route_id:routeID,actor_id:account.user_id,body_hash:sha(canonical(body)),raw_sha256:sha(request.body),accepted_instance_version:state.version,accepted_at:'2026-10-29T01:03:00+00:00'},'receipt_hash');
   const result={schema:'signal-approval-command-result-v247',instance_id:instanceId,instance_version:state.version,route:copy(state.routes[0]),receipt,approval_draft_only:true,device_actions:false,cloud_calls:0};saved={raw:p.raw,result};state.commands.set(body.request_id,saved);if(state.mode==='lost')throw Error('启用已保存但回复丢失');return copy(result);
  }
  if(path==='/workspace/tasks/'+taskID+'/signal-approval-source'){if(state.sourceGate)await state.sourceGate;return sourceView();}
  throw Object.assign(Error('本合成场景未启用附属能力：'+path),{status:503});
 };
 const make=extra=>{assert.equal(typeof Controller,'function');return new Controller({account,instanceId,releaseID,api,storage,session,locks,crypto:webcrypto,...extra});};
 async function ready(controller=make()){await controller.start();controller.edit({source_id:sourceID,extractor_id:extractorID,entity_ids:[entityID],business_id:businessID,valid_for_hours:'6',max_tasks:'3',reason:'只将此范围的新异常存为审批草稿'});controller.edit({confirmed:true});assert.equal(controller.localSaved,true);return controller;}
 const approval=()=>({schema:'layered-approval-task-view-v175',task_id:taskID,managed_id:id(40),version:1,task_version:1,status:'draft',owner_id:account.user_id,current_user_id:account.user_id,scope_id:id(41),current_stage:0,policy:{instance_id:instanceId,business_id:businessID,name:'正常三级正式审批',scope_id:id(41),stages:[{name:'一级负责人核对',role_key:'reviewer',scope_id:id(41)},{name:'二级业务复核',role_key:'manager',scope_id:id(41)},{name:'三级批准',role_key:'approver',scope_id:id(41)}],executor:{name:'执行负责人',scope_id:id(41),role_key:'executor'},acceptance:{name:'独立验收负责人',scope_id:id(41),role_key:'acceptance'},enabled:true},value:{title:'已保存的 HTTP 异常草稿',note:'尚未提交正式审批',graph_inputs:{},goal_spec:null},draft_history:[],submission_hash:null,submissions:[],decisions:[],decision_history:[],execution:null,acceptance:null,acceptance_history:[],supplements:[],acceptance_basis:null,acceptance_issue:null,permissions:{revise:true,submit:true,decide:false,start:false},blocked_reasons:{}});
 const task=()=>({id:taskID,instance_id:instanceId,instance_release_id:releaseID,instance_release_number:1,version:1,status:'approval_draft',title:'已保存的 HTTP 异常草稿',business_name:'数值异常人工复核',business:{name:'数值异常人工复核',flow:{citations:[]}},object_snapshots:[],workflow_runtime:{receipts:[]},layered_approval_v175:{status:'draft'}});
 return {account,instanceId,releaseID,entityID,sourceID,extractorID,routeID,taskID,businessID,scope,prefix,base,storage,session,locks,csrf,state,options,route,occurrence,routes,sourceView,api,make,ready,approval,task};
}
