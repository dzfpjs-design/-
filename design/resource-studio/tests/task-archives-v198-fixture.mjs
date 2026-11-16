// Public synthetic DTOs matching the closed V198 HTTP contract. These are not
// proof of PostgreSQL, source inventory integrity, or a live archive adoption.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
export const scope={actor_id:'19800000-0000-4000-8000-000000000001',workspace_id:'19800000-0000-4000-8000-000000000002',task_id:'19800000-0000-4000-8000-000000000003'};
export const archiveID='19800000-0000-4000-8000-000000000004';
export const copy=value=>JSON.parse(JSON.stringify(value));
export const flags={proposal_only:true,human_confirmed:false,formal_archive:false,entity_state_updated:false};
export const digest=async raw=>[...new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(v=>v.toString(16).padStart(2,'0')).join('');
export function memory(){const data=new Map();return {getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>{data.set(key,String(value));},removeItem:key=>data.delete(key),key:index=>[...data.keys()][index]??null,get length(){return data.size;},data};}
const hash=n=>n.toString(16).padStart(64,'0');
export const task={id:scope.task_id,title:'通用现场核对任务',workflow_review_mode:'engineering_simulation',workflow_simulation_only:true,graph_node_labels:{'actual-human-activation':{name:'现场复核'},'actual-analysis-activation':{name:'证据分析'}}};
export function fixture(storage=memory()){
 const calls=[],versions=[],commands=new Map(),state={actor_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator',mode:'',denyDetail:false,saved:0,postBodies:[]};
 const initial={schema:'workflow-task-archive-value-v198',record_type:'现场处置记录',title:'对象核对候选档案',summary:'当前说明是待核提案，尚不代表实际灾损已经确认。',basis:{task_id:scope.task_id,task_title:task.title,actor_id:scope.actor_id,business_name:'通用对象核对',graph_hash:hash(30)},sources:[
  {kind:'human_product',id:'19800000-0000-4000-8000-000000000008',label:'已保存的人工处理结果',source_hash:hash(21),snapshot:{task_id:scope.task_id,activation_id:'actual-human-activation',actor_id:scope.actor_id,output:{note:'现场核对的原保存说明\n多行记录保持原样',count:0,nullable:null}}},
  {kind:'invocation',id:'19800000-0000-4000-8000-000000000009',label:'已保存的注册能力结果',source_hash:hash(22),snapshot:{task_id:scope.task_id,activation_id:'actual-analysis-activation',job_id:'19800000-0000-4000-8000-000000000010',output:{summary:'注册分析保留了相互冲突的来源',missing_information:['还缺一次独立复核']}}}],issues:[{code:'unfinished_action',activation_id:'actual-pending-activation',status:'waiting_guard',label:'仍有处理结果待补充'}],review_mode:'engineering_simulation',simulation_only:true,summary_verification:'unconfirmed_proposal',...flags};
 function append(values){const n=versions.length+1,value={...copy(versions.at(-1)?.value||initial),...copy(values)};versions.push({version:n,version_hash:hash(n),value_hash:hash(100+n),actor_id:scope.actor_id,saved_at:'2026-10-24T10:00:'+String(n).padStart(2,'0')+'+00:00',value});return n;}
 append({});
 const view=(n=versions.length)=>({schema:'workflow-task-archive-view-v198',id:archiveID,task_id:scope.task_id,status:'candidate',...copy(versions[n-1]),history:versions.map(({value,...entry})=>copy(entry)),current_source_checked:false,...flags});
 const list=()=>({schema:'workflow-task-archive-list-v198',task_id:scope.task_id,items:[{id:archiveID,task_id:scope.task_id,...Object.fromEntries(['record_type','title','summary'].map(key=>[key,versions.at(-1).value[key]])),version:versions.length,version_hash:versions.at(-1).version_hash,status:'candidate',simulation_only:true,issue_count:versions.at(-1).value.issues.length,...flags}],current_source_checked:false});
 const api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});
  if(path==='/me')return {user_id:state.actor_id,workspace_id:state.workspace_id,role:state.role};
  const url=new URL(path,'https://synthetic-archive.invalid');
  if(method==='GET'&&url.pathname==='/task-archives'){
   assert.equal(url.searchParams.get('task_id'),scope.task_id);const result=list(),type=url.searchParams.get('record_type'),q=url.searchParams.get('q');
   result.items=result.items.filter(item=>(!type||item.record_type===type)&&(!q||item.title.includes(q)||item.summary.includes(q)));return result;
  }
  if(method==='GET'&&url.pathname==='/task-archives/requests/original'){
   const key=url.searchParams.get('request_key'),command=commands.get(key);if(command)assert.equal(url.searchParams.get('expected_sha256'),command.sha);
   return {schema:'workflow-task-archive-original-result-v198',request_key:key,found:!!command,body_sha256:command?.sha||null,result:copy(command?.receipt||null)};
  }
  if(method==='GET'&&url.pathname.startsWith('/task-archives/'+archiveID)){
   if(state.denyDetail){const error=Error('归集历史暂时无法核对，原历史仍保留');error.status=422;throw error;}
   const parts=url.pathname.split('/');return view(parts[3]==='versions'?Number(parts[4]):versions.length);
  }
  assert.equal(method,'POST');assert.equal(url.pathname,'/task-archives/'+archiveID+'/revisions');assert.notEqual(state.role,'viewer');
  const body=JSON.parse(options.body),sha=await digest(options.body);state.postBodies.push(options.body);
  assert.deepEqual(Object.keys(body).sort(),['schema','request_id','expected_version','source_version_hash','record_type','title','summary'].sort());
  const key=[...Array(storage.length)].map((_,index)=>storage.key(index)).find(key=>key.endsWith(':outbox'));assert.ok(key,'shared original request exists before transport');
  const pending=JSON.parse(storage.getItem(key));assert.equal(pending.raw,options.body);assert.equal(pending.sha256,sha);assert.ok(storage.getItem(key.replace(/outbox$/,'original:'+body.request_id)),'immutable original exists before transport');
  if(commands.has(body.request_id)){assert.equal(commands.get(body.request_id).raw,options.body);return copy(commands.get(body.request_id).receipt);}
  if(state.mode==='beforecommit')throw Error('合成网络中断：原请求结果未知');
  if(state.mode==='conflict'||body.expected_version!==versions.length||body.source_version_hash!==versions.at(-1).version_hash){const error=Error('档案已产生新版本');error.status=409;error.detail={code:'workflow_task_archive_version_conflict',not_applied:true,request_key:body.request_id,body_sha256:sha,message:'档案已有新版本，本次未应用，原输入保留'};if(state.mode==='spoofedConflict')error.detail.body_sha256=hash(999);throw error;}
  const version=append({record_type:body.record_type,title:body.title,summary:body.summary}),result={schema:'workflow-task-archive-receipt-v198',archive_id:archiveID,version,version_hash:versions.at(-1).version_hash,task_id:scope.task_id,status:'candidate',simulation_only:true,...flags};
  commands.set(body.request_id,{raw:options.body,sha,receipt:copy(result)});state.saved++;
  if(state.mode==='aftercommit')throw Error('合成响应丢失：已保存但前端尚未知');return copy(result);
 };
 return {api,calls,state,storage,versions,commands,view,list,append,task:copy(task)};
}
