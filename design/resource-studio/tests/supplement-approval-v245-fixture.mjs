// Synthetic transport and saved rows exercise real production controllers/React.
// They do not prove device reads, workers, a DB transaction or browser durability.
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {approvalCanonical as canonical,approvalScope,approvalPrefix} from '../src/supplement-approval-v245-protocol.mjs';
let Controller;
export async function initializeSupplementController(){assert.ok(globalThis.window?.document&&globalThis.localStorage&&globalThis.sessionStorage,'establish browser globals before loading runtime-api');({SupplementApprovalControllerV245:Controller}=await import('../src/supplement-approval-v245-controller.mjs'));}
export const id=n=>'24500000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>structuredClone(v),sha=v=>createHash('sha256').update(v).digest('hex');
export class Memory{
 constructor(){this.rows=new Map();this.reads=[];this.failSet=null;this.failRemove=false;}
 get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}
 getItem(k){this.reads.push(k);return this.rows.get(k)??null;}
 setItem(k,v){if(this.failSet?.(k))throw Error('本机保存失败');this.rows.set(k,String(v));}
 removeItem(k){if(!this.failRemove)this.rows.delete(k);}
}
export function supplementFixture(){
 const account={user_id:id(1),workspace_id:id(2),username:'补采批准人',role:'admin'},instanceId=id(3),scope=approvalScope(account,instanceId),prefix=approvalPrefix(scope),storage=new Memory(),session=new Memory(),csrf='supplement-v245-test-csrf';
 for(const[k,v]of Object.entries({'ga-setup-csrf':csrf,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(k,v);
 let chain=Promise.resolve();const locks={request:async(name,options,fn)=>{const prior=chain;let done;chain=new Promise(resolve=>done=resolve);await prior;try{return await fn();}finally{done();}}};
 const action={key:'saved-camera-read',object_name:'技术核对对象',name:'读取原通道',limits:'一次，限定保存范围',resource:{channel_name:'已保存通道',backend:'本地处理',channel_id:id(7)}};
 const plan={id:id(4),version:1,instance_id:instanceId,assessment_id:id(5),proposal_hash:'a'.repeat(64),status:'proposed',created_at:'2026-10-28T00:00:00Z',progress:'候选来源已保存',proposal:{objects:[{object:{id:id(6),name:action.object_name},needs:[{message:'缺少一次视频资料'}]}],actions:[action]}};
 const assessment={id:plan.assessment_id,status:'completed',result:{objects:[{gaps:[{kind:'video'}]}]}},form={keys:[action.key],seconds:6,consent:true},cacheKey='ga-supplement-v245:'+account.workspace_id+':'+account.user_id+':'+instanceId;
 const state={identity:copy(account),plan:copy(plan),posts:[],commits:0,mode:'commit',late:null,clientReject:null,meGate:null,readGate:null};
 function commit(body=state.late){assert.ok(body);if(state.plan.approval_request_id){assert.equal(state.plan.approval_request_id,body.request_id);assert.equal(state.plan.approval_action_hash,sha(canonical(Object.fromEntries(Object.entries(body).filter(([k])=>k!=='version')))));return copy(state.plan);}const approved={actions:body.action_keys.map(key=>copy(state.plan.proposal.actions.find(a=>a.key===key))),seconds:body.seconds,read_configuration:'fixture-local-only',once:true};state.plan={...state.plan,status:'reading',version:state.plan.version+1,approved,approved_hash:sha(canonical(approved)),approved_by:account.user_id,approval_request_id:body.request_id,approval_action_hash:sha(canonical(Object.fromEntries(Object.entries(body).filter(([k])=>k!=='version')))),sessions:body.action_keys.map((_,i)=>({id:id(20+i),channel_id:id(7+i),job_id:id(30+i)})),progress:'原批准已保存'};state.commits++;return copy(state.plan);}
 const api=async(path,options={})=>{
  if(path==='/me'){assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers['X-CSRF-Token'],csrf);if(state.meGate)await state.meGate;return copy(state.identity);}
  if(path.startsWith('/supplement-plans?'))return [copy(state.plan)];
  if(path==='/supplement-plans/'+plan.id){if(state.readGate)await state.readGate;return copy(state.plan);}
  if(path==='/supplement-plans/'+plan.id+'/approve'){
   const p=JSON.parse(storage.getItem(prefix+'pending'));assert.equal(p.raw,options.body);assert.equal(p.sha256,sha(options.body));assert.equal(storage.getItem(prefix+'original:'+p.body.request_id),JSON.stringify(p));assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);const body=JSON.parse(options.body);state.posts.push({path,raw:options.body,body});state.late=copy(body);
   if(state.mode==='pending_reply')return new Promise((resolve,reject)=>{state.clientReject=reject;});
   if(state.mode==='unknown')throw Error('网络中断；服务器原事务尚未提交');
   const result=commit(body);if(state.mode==='lost_after_commit')throw Error('服务器原事务已提交，回复丢失');return result;
  }
  if(path.startsWith('/supplement-watches?')||path.startsWith('/evidence-assessments?'))return [];
  throw Object.assign(Error('本合成测试未配置此附属能力：'+path),{status:503});
 };
 const settlements=[],make=extra=>{assert.equal(typeof Controller,'function');return new Controller({account,instanceId,api,storage,session,locks,crypto:webcrypto,onSettled:(row,original)=>settlements.push({row,original}),...extra});};
 function cache(patch={}){storage.setItem(cacheKey,JSON.stringify({selected:plan.id,form:copy(form),...patch}));}
 return {account,instanceId,scope,prefix,storage,session,locks,csrf,plan,assessment,form,cacheKey,state,commit,api,make,settlements,cache};
}
