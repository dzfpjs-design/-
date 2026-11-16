import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {createGraphTaskController,graphHash} from '../src/graph-task-controller.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
export const scope={actor_id:'19500000-0000-4000-8000-000000000001',workspace_id:'19500000-0000-4000-8000-000000000002',task_id:'19500000-0000-4000-8000-000000000003'};
export const copy=value=>structuredClone(value);
export const notice=(index=0,patch={})=>({id:'19600000-0000-4000-8000-'+(index+1).toString(16).padStart(12,'0'),sender_name:'任务协调员',message:'请核对当前现场记录。\n原始通知内容完整保留。',delivered_at:'2026-10-24T12:30:45.123456+00:00',delivery_status:'delivered_to_in_app',read_status:'not_tracked',simulation_only:true,...patch});
// This checked-in fixture was emitted from the actual native reducer,
// save_draft, cold check_run and public_view; adoption selection is explicitly
// a patched boundary, not real DB/IAM/adoption evidence.
export const nativePublicFixture=JSON.parse(await readFile(new URL('../../../tests/fixtures/nested_task_waiting_inputs_v195.json',import.meta.url),'utf8'));
export class Memory{constructor(){this.rows=new Map();}get length(){return this.rows.size;}key(index){return [...this.rows.keys()][index]??null;}getItem(key){return this.rows.get(key)??null;}setItem(key,value){this.rows.set(key,String(value));}removeItem(key){this.rows.delete(key);}}
export function task(){
 const output={type:'object',properties:{note:{type:'string',title:'现场核对说明',minLength:1}},required:['note'],additionalProperties:false};
 return {id:scope.task_id,workspace_id:scope.workspace_id,version:1,title:'合成递归任务',business_name:'现场核验',instance_release_number:1,workflow_review_mode:'engineering_simulation',workflow_simulation_only:true,
  graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'waiting',commands:[],ready_human_ids:['human'],activations:{human:{id:'human',node_id:'private-human-node',version:0,epoch:0,status:'ready',draft:null,output:null},producer:{id:'producer',node_id:'private-producer-node',version:1,epoch:0,status:'completed',output:{count:0,note:null},draft:null}}},
  graph_pending_humans:[{activation_id:'human',node_id:'private-human-node',source_step_id:'original-human-step',role:'reviewer',actor_id:scope.actor_id,version:0,can_edit:true,activation_status:'ready',can_complete:true,output_schema:output,draft:null,inputs_available:true,required_inputs:[
   {label:'完整观察说明\n逐项记录',ordinal:0,available:true,value:'第一行原记录\n第二行原记录',source:'input',source_name:'启动资料\n原始备注',availability_code:'ready',producer_activation_id:null,schema:{type:'string'}},
   {label:'重复资料',ordinal:1,available:true,value:0,source:'exclusive_output',source_name:'现场核验结果\n登记数量',availability_code:'ready',producer_activation_id:'producer',schema:{type:'integer'}},
   {label:'重复资料',ordinal:2,available:true,value:null,source:'output',source_name:'原补充说明',availability_code:'ready',producer_activation_id:'producer',schema:{type:['string','null']}}
  ]}],graph_node_labels:{human:{name:'复核并记录现场结论',kind:'human',output_schema:output},producer:{name:'现场已保存核验',kind:'human',output_schema:{type:'object',properties:{count:{type:'integer',title:'登记数量'},note:{type:['string','null'],title:'原补充说明'}}}}},graph_jobs:{},graph_waits:[],object_snapshots:[],roles:{reviewer:{username:'合成负责人'}},permissions:{can_cancel_task:true},business:{flow:{steps:[{id:'original-human-step',name:'复核并记录现场结论',purpose:'对照实际输入，保留原核对记录',citations:[]}],citations:[]}}};
}
export function unavailable(value,code='missing'){
 const human=value.graph_pending_humans[0];Object.assign(human.required_inputs[1],{available:false,value:'不得呈现的未就绪占位值',availability_code:code,producer_activation_id:null});Object.assign(human,{inputs_available:false,activation_status:'waiting_guard',can_complete:false});value.graph_runtime.activations.human.status='waiting_guard';value.graph_runtime.ready_human_ids=[];
 if(code==='ambiguous'){value.graph_runtime.status='blocked';value.graph_runtime.error='exclusive_reference_ambiguous:private-reference-key';}return value;
}
export function environment({storage=new Memory(),session=new Memory()}={}){
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':scope.actor_id,'ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf':'public-nested-task'}))session.setItem(key,value);
 const calls=[],state={task:task(),lose:false,notSent:false},locks={request:async(_key,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}};
 const api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.includes('/graph/requests/')){assert.equal(method,'GET');const nonce=path.split('/').at(-1),record=state.task.graph_runtime.commands.find(item=>item.request_id===nonce);return {schema:'workflow-graph-request-recovery-v1',found:!!record,request:copy(record||null),task:copy(state.task)};}
  if(path==='/workspace/tasks/'+scope.task_id&&method==='GET')return copy(state.task);
  if(path.endsWith('/graph/command-hash')){assert.equal(method,'POST');const request=graphWireParseCanonical(options.body),command=request.command,body_hash=await graphWireDigest({activation_id:command.activation_id,output:command.output,actor_id:scope.actor_id,expected_version:command.version,cancel_epoch:command.cancel_epoch},{crypto:webcrypto}),value={schema:'workflow-graph-command-hash-v1',operation:'complete',actor_id:scope.actor_id,workspace_id:scope.workspace_id,task_id:scope.task_id,graph_hash:'a'.repeat(64),command,body_hash},canonical=graphWireCanonical(value);return {schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)};}
  const draft=path.endsWith('/graph/draft');assert.ok(draft||path.endsWith('/graph/complete'));assert.equal(method,draft?'PUT':'POST');const outboxKey=Array.from({length:storage.length},(_,index)=>storage.key(index)).find(key=>key?.endsWith(':outbox')),pending=JSON.parse(storage.getItem(outboxKey));assert.equal(pending.exact_body,options.body,'the original outbox is durable before every mutation');
  if(state.notSent)throw Error('合成：原请求到达状态不明');const body=JSON.parse(options.body),human=state.task.graph_pending_humans[0],activation=state.task.graph_runtime.activations.human;
  const body_hash=draft?await graphHash({actor_id:scope.actor_id,expected_version:body.version,cancel_epoch:body.cancel_epoch,activation_id:body.activation_id,draft:body.draft},webcrypto):pending.body_hash;assert.equal(state.task.graph_runtime.commands.some(item=>item.request_id===body.request_id),false);
  state.task.graph_runtime.commands.push({request_id:body.request_id,op:draft?'draft':'complete',actor_id:scope.actor_id,body_hash});human.version++;activation.version++;state.task.version++;state.task.graph_runtime.version++;
  if(draft){human.draft=copy(body.draft);activation.draft=copy(body.draft);}else{activation.output=copy(body.output);activation.status='completed';state.task.graph_pending_humans=[];state.task.graph_runtime.ready_human_ids=[];state.task.graph_runtime.status='succeeded';}
  if(state.lose)throw Error('合成：保存后响应丢失');return copy(state.task);
 };
 const controller=()=>createGraphTaskController({api,scope,storage,session,locks,crypto:webcrypto,typedNumbers:true});return {storage,session,calls,state,locks,api,controller,mutations:()=>calls.filter(call=>call.method!=='GET')};
}
export function nativeEnvironment(detail,{storage=new Memory(),session=new Memory()}={}){
 assert.equal(nativePublicFixture.source,'native_public_view');assert.equal(nativePublicFixture.adoption_authority_verified,false);
 const original=copy(detail),human=detail.graph_pending_humans[0],scopeValue={actor_id:human.actor_id,workspace_id:detail.workspace_id||'public_nested_workspace',task_id:detail.id},calls=[],state={task:copy(detail)},locks={request:async(_key,options,run)=>{assert.equal(options.mode,'exclusive');return run();}};
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':scopeValue.actor_id,'ga-setup-csrf-workspace':scopeValue.workspace_id,'ga-setup-csrf':'public-native-projection'}))session.setItem(key,value);
 const api=async(path,options={})=>{calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.method||'GET','GET','the native projection case never substitutes a fake write');assert.equal(options.headers?.['X-Workspace-ID'],scopeValue.workspace_id);if(path==='/me')return {user_id:scopeValue.actor_id,workspace_id:scopeValue.workspace_id,role:'operator'};assert.equal(path,'/workspace/tasks/'+encodeURIComponent(scopeValue.task_id));return copy(state.task);};
 const controller=()=>createGraphTaskController({api,scope:scopeValue,storage,session,locks,crypto:webcrypto,typedNumbers:true});return {scope:scopeValue,storage,session,locks,api,state,calls,original,controller,mutations:()=>calls.filter(call=>call.method!=='GET')};
}
