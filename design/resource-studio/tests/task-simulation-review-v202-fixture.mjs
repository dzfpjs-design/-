import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createTaskSimulationReviewController,SIMULATION_REVIEW_BASIS,SIMULATION_REVIEW_RECEIPT} from '../src/task-simulation-review-v202.mjs';
export const id=n=>'20200000-0000-4000-8000-'+String(n).padStart(12,'0');
export const scope={actor_id:id(1),workspace_id:id(2),task_id:id(3),activation_id:'device-activation'};
export const copy=v=>JSON.parse(JSON.stringify(v));
export class Memory{
 constructor(){this.map=new Map();this.fail=false;}
 get length(){return this.map.size;}key(index){return [...this.map.keys()][index]??null;}
 getItem(key){if(this.fail)throw Error('synthetic storage failure');return this.map.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('synthetic storage failure');this.map.set(key,String(value));}
 removeItem(key){if(this.fail)throw Error('synthetic storage failure');this.map.delete(key);}
}
export function basis(){return {schema:SIMULATION_REVIEW_BASIS,state:'reviewable',task_id:scope.task_id,activation_id:scope.activation_id,task_version:4,job_id:id(4),job_version:2,activation_version:0,cancel_epoch:0,basis_hash:'a'.repeat(64),recommended_candidate_ref:'private_candidate_reference',missing_information:['尚缺现场阈值','尚缺连续观测'],analysis_summary:'现有模拟数据支持加强关注，无法核实现场风险。',candidate_reason:'模型选择加强监测；仍缺现场与时序资料。',targets:[{display_name:'无人机模拟器',simulation_only:true},{display_name:'摄像头模拟器',simulation_only:true}],purpose:'bounded_simulated_tool_test',gaps_resolved:false,human_review_verified:false,can_confirm:true,reason:'仅可验证已批准的模拟设备动作。'};}
export function fixture(storage=new Memory(),session=new Memory()){
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':scope.actor_id,'ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf':'synthetic-csrf'}))session.setItem(key,value);
 const state={basis:basis(),task:{id:scope.task_id,version:4},records:new Map(),calls:[],saved:[],mode:{},role:'operator'};let c;
 const api=async(path,options={})=>{
  const method=options.method||'GET';state.calls.push({path,method,body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:state.role};
  if(state.mode.forbidden){const error=Error('当前账号无任务权限');error.status=403;throw error;}
  const base='/workspace/tasks/'+scope.task_id+'/graph';
  if(path===base+'/simulation-review?'+new URLSearchParams({activation_id:scope.activation_id})){assert.equal(method,'GET');state.mode.beforeRead?.(state);return copy(state.basis);}
  if(path.startsWith(base+'/requests/')){assert.equal(method,'GET');const key=path.split('/').at(-1),record=copy(state.records.get(key)||null);state.mode.modifyRecord?.(record);return {schema:'workflow-graph-request-recovery-v1',found:!!record,request:record,task:copy(state.task)};}
  if(path==='/workspace/tasks/'+scope.task_id){assert.equal(method,'GET');return copy(state.task);}
  assert.equal(path,base+'/simulation-review');assert.equal(method,'POST');const pending=JSON.parse(storage.getItem(c.outboxKey));assert.equal(pending.raw,options.body,'original command is durable before POST');assert.equal(await c.hash(options.body),pending.sha256);assert.ok(storage.getItem(c.prefix+'original:'+pending.body.request_id));const body=JSON.parse(options.body);
  if(state.mode.rejected||state.mode.unknown){const error=Error('合成拒绝或响应未知');if(state.mode.rejected)error.status=409;throw error;}
  const receipt={schema:SIMULATION_REVIEW_RECEIPT,request_id:body.request_id,task_id:scope.task_id,activation_id:scope.activation_id,job_id:id(4),state:'saved',queued:true,engineering_only:true,gaps_resolved:false,human_review_verified:false,replayed:false,original_request_preserved:true};
  state.records.set(body.request_id,{actor_id:scope.actor_id,body_hash:'b'.repeat(64),body:copy(body),receipt:copy(receipt)});state.task.version++;state.mode.afterCommit?.(receipt);if(state.mode.lostResponse)throw Error('合成提交成功后断线');return receipt;
 };
 const locks={request:async(_key,_options,run)=>run()};
 const make=()=>{c=createTaskSimulationReviewController({api,scope,storage,session,crypto:webcrypto,locks,onSaved:value=>{state.saved.push(value);return state.mode.parentReject?false:true;}});c.mount();return c;};
 make();return {get c(){return c;},make,api,scope,storage,session,state,locks};
}
export const fill=c=>{c.edit({reason:'保留原资料缺项，仅核对已批准的两项模拟设备动作。'});c.edit({acknowledged:true});};
export const posts=f=>f.state.calls.filter(item=>item.method==='POST');
