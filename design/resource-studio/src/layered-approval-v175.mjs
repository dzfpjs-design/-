import {typedRequestJSON,parseTypedJSON} from './typed-numbers-v132.mjs';

export const APPROVAL_PREFIX='ga-layered-approval-v175:';
export const approvalStatus={draft:'待提交',in_review:'逐级审批中',returned:'已退回修改',withdrawn:'已撤回',approved:'审批通过，尚未开始',executing:'已明确开始，等待执行结果及验收',acceptance_returned:'验收退回，待补充说明',accepted:'独立验收已通过'};
const need=(value,message='审批原稿或原请求无法核对；内容保留，未提交新操作。')=>{if(!value)throw Error(message);};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const shaPattern=/^[a-f0-9]{64}$/;
export function approvalCommandPath(operation,subject){
 const settings={unit:'units',role:'roles',policy:'policies'};
 if(settings[operation])return '/workspace/approval-settings-v175/'+settings[operation];
 const task={task_revise:'revise',task_submit:'submit',task_decide:'decide',task_withdraw:'withdraw',task_start:'start',task_resubmit_acceptance:'resubmit-acceptance',task_accept:'accept'};
 need(task[operation]&&uuid.test(subject),'审批操作或任务范围不受支持。');return '/workspace/tasks/'+subject+'/approval-v175/'+task[operation];
}
export const approvalScopeKey=scope=>{need(scope?.actor_id&&scope?.workspace_id&&scope?.subject);return APPROVAL_PREFIX+JSON.stringify([scope.workspace_id,scope.actor_id,scope.subject]);};
export async function approvalSHA(text,subtle=globalThis.crypto?.subtle){need(typeof text==='string'&&subtle,'浏览器无法计算原请求摘要，尚未提交。');return Array.from(new Uint8Array(await subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(x=>x.toString(16).padStart(2,'0')).join('');}
function writeBack(storage,key,text){storage.setItem(key,text);need(storage.getItem(key)===text,'浏览器未能可靠保留本次输入，尚未提交；请保持当前页面。');}
let pageDraftOwner;
function draftRow(text){const row=parseTypedJSON(text);need(object(row)&&row.schema==='approval-human-draft-v175'&&object(row.value));return row;}
export function readApprovalDraft(storage,key){const text=storage.getItem(key+':draft');if(text===null)return null;const row=draftRow(text);if(row.owner_id)need(storage.getItem(key+':draft-owner:'+row.owner_id)===text,'其他窗口的新稿正在保存；各窗口原稿独立保留，请核对保留稿。');return row.value;}
export function writeApprovalDraft(storage,key,value){need(object(value));pageDraftOwner=pageDraftOwner||globalThis.crypto?.randomUUID?.();need(uuid.test(pageDraftOwner||''),'浏览器无法保留独立人稿，尚未提交。');const text=typedRequestJSON({schema:'approval-human-draft-v175',owner_id:pageDraftOwner,at:new Date().toISOString(),value});writeBack(storage,key+':draft-owner:'+pageDraftOwner,text);writeBack(storage,key+':draft',text);}
export function listApprovalDrafts(storage,key){const rows=[];for(let i=0;i<storage.length;i++){const name=storage.key(i);if(name?.startsWith(key+':')&&name.includes(':draft-owner:')){const row=draftRow(storage.getItem(name));rows.push({...row,key:name});}}return rows.sort((a,b)=>String(b.at).localeCompare(String(a.at)));}
export function checkApprovalTask(value,taskID){need(object(value)&&value.schema==='layered-approval-task-view-v175'&&value.task_id===taskID&&Number.isSafeInteger(value.version)&&value.version>=1&&object(value.value)&&object(value.policy)&&Array.isArray(value.policy.stages));return value;}
export const isManagedApprovalTask=task=>!!task&&(object(task.layered_approval_v175)||String(task.status||'').startsWith('approval_'));
export function approvalExecutionAllowed(task){return !isManagedApprovalTask(task)||task.layered_approval_v175?.status==='executing';}

// One pending slot plus an independent immutable original-body record per nonce.
// New sends require Web Locks and a verified browser write before network I/O.
export function createApprovalController({api,scope,storage=globalThis.localStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{},onSaved=async()=>{}}){
 const key=approvalScopeKey(scope),prefix=key+':',state={busy:false,pending:null,error:'',notice:'',missing:false,disposed:false};
 const notify=()=>{if(!state.disposed)onChange({...state});};
 const live=()=>need(!state.disposed,'页面范围已变化，原请求保留，未继续操作。');
 async function identity(){live();const me=await api('/me');live();need(me.user_id===scope.actor_id&&me.workspace_id===scope.workspace_id&&(!scope.role||me.role===scope.role),'账号、工作区或权限已变化；原账号输入保留，请重新打开工作区。');return me;}
 async function current(){
  const raw=storage.getItem(prefix+'pending');if(raw===null)return null;
  const row=JSON.parse(raw);need(object(row)&&row.schema==='approval-original-command-v175'&&row.actor_id===scope.actor_id&&row.workspace_id===scope.workspace_id&&row.subject===scope.subject&&uuid.test(row.request_id)&&shaPattern.test(row.body_sha256)&&typeof row.path==='string'&&row.path.startsWith('/workspace/')&&typeof row.exact_body==='string');
  need(storage.getItem(prefix+'original:'+row.request_id)===JSON.stringify({schema:row.schema,actor_id:row.actor_id,workspace_id:row.workspace_id,subject:row.subject,request_id:row.request_id,path:row.path,operation:row.operation,subject_id:row.subject_id,exact_body:row.exact_body,body_sha256:row.body_sha256}));
  need(row.path===approvalCommandPath(row.operation,row.subject_id));need(await approvalSHA(row.exact_body,crypto?.subtle)===row.body_sha256&&parseTypedJSON(row.exact_body).request_id===row.request_id);return row;
 }
 async function locked(action){need(locks?.request,'当前浏览器不支持安全保留并发审批请求；可查看记录，暂不能提交。');return locks.request(key,{mode:'exclusive'},async()=>{live();return action();});}
 function receipt(value,row){
  need(object(value)&&value.request_id===row.request_id,'返回结果与原审批请求不一致；原请求保留。');
  if(row.operation.startsWith('task_'))need(value.schema==='layered-approval-task-receipt-v175'&&value.task_id===row.subject_id&&value.operation===row.operation.slice(5)&&Number.isSafeInteger(value.version));
  else need(value.schema==='approval-setting-receipt-v175'&&value.operation===row.operation&&typeof value.id==='string'&&Number.isSafeInteger(value.version));
  return value;
 }
 async function finish(row,value){
  live();receipt(value,row);const saved=await current();need(saved?.request_id===row.request_id&&saved.body_sha256===row.body_sha256);
  writeBack(storage,prefix+'receipt:'+row.request_id,JSON.stringify({schema:'approval-local-receipt-v175',body_sha256:row.body_sha256,result:value}));
  storage.removeItem(prefix+'pending');need(storage.getItem(prefix+'pending')===null,'服务器已保存，但本机原请求尚未释放，请核对原回执。');
  state.pending=null;state.missing=false;state.notice='服务器已保存本次操作；原请求及回执已保留。';notify();
  try{await onSaved(value);live();}catch(e){state.error='本次操作已保存，读取最新状态未完成：'+e.message;notify();}return value;
 }
 async function lookup(row){
  await identity();const value=await api('/workspace/approval-settings-v175/requests/'+encodeURIComponent(row.request_id)+'?expected_sha256='+row.body_sha256);await identity();
  need(value?.schema==='layered-approval-original-result-v175'&&value.request_id===row.request_id&&value.operation===row.operation&&value.subject_id===row.subject_id&&value.body_sha256===row.body_sha256,'原请求回执的账号、对象或正文不一致；未采用返回结果。');return finish(row,value.result);
 }
 async function post(row){
  await identity();state.pending=row;notify();
  try{const value=await api(row.path,{method:'POST',body:row.exact_body});await identity();return await finish(row,value);}
  catch(error){
   live();try{return await lookup(row);}catch(check){
    if(!state.pending)throw check;
    state.missing=check.status===404;
    state.error=state.missing?'暂未读到原请求回执；尚不能确认保存。请保留原输入并核对原请求。':check.message;
    if(state.missing&&[400,403,409,413,422].includes(error.status)){
     const rejected={...row,rejection_status:error.status};writeBack(storage,prefix+'pending',JSON.stringify(rejected));state.pending=rejected;state.error=error.message+' 本次被服务器拒绝；原稿与原请求保留，可核对后修改。';
    }
    notify();throw error;
   }
  }
 }
 const controller={key,state,
  async read(path){await identity();const value=await api(path);await identity();return value;},
  async open(){try{state.pending=await current();state.error='';notify();return state.pending;}catch(e){state.error=e.message;notify();throw e;}},
  async send(path,operation,subject_id,body){
   if(state.busy)throw Error('上次操作仍在核对，请稍候。');state.busy=true;state.error='';notify();
   try{return await locked(async()=>{
    state.pending=await current();need(!state.pending,'仍有原请求待核对；不能另建编号覆盖原请求。');await identity();
    const request_id=crypto.randomUUID(),exact_body=typedRequestJSON({...body,request_id}),body_sha256=await approvalSHA(exact_body,crypto.subtle);
    const row={schema:'approval-original-command-v175',actor_id:scope.actor_id,workspace_id:scope.workspace_id,subject:scope.subject,request_id,path,operation,subject_id,exact_body,body_sha256};
    need(uuid.test(request_id)&&path===approvalCommandPath(operation,subject_id));need(new TextEncoder().encode(exact_body).byteLength<=1024*1024,'本次审批内容超过服务器上限，原人稿保留，尚未提交。');need(storage.getItem(prefix+'original:'+request_id)===null,'原请求编号已经存在，未覆盖其原文或再次提交。');writeBack(storage,prefix+'original:'+request_id,JSON.stringify(row));writeBack(storage,prefix+'pending',JSON.stringify(row));state.pending=row;notify();return post(row);
   });}catch(e){state.error=state.error||e.message;notify();throw e;}finally{state.busy=false;notify();}
  },
  async recover(){if(state.busy)return;state.busy=true;state.error='';notify();try{return await locked(async()=>{const row=await current();need(row,'当前没有待核对的原请求。');state.pending=row;try{return await lookup(row);}catch(e){state.missing=e.status===404;state.error=state.missing?'服务器暂无原请求回执，未认定成功；请保留本页输入。':e.message;throw e;}});}finally{state.busy=false;notify();}},
  async resend(){if(state.busy)return;state.busy=true;state.error='';notify();try{return await locked(async()=>{const row=await current();need(row);state.pending=row;try{return await lookup(row);}catch(e){if(e.status!==404)throw e;}return post(row);});}catch(e){state.error=state.error||e.message;notify();throw e;}finally{state.busy=false;notify();}},
  async editRejected(){return locked(async()=>{const row=await current();need(row?.rejection_status,'原请求结果尚未确定，不能丢弃或换号。');try{return await lookup(row);}catch(e){if(e.status!==404)throw e;}writeBack(storage,prefix+'rejected:'+row.request_id,JSON.stringify(row));storage.removeItem(prefix+'pending');state.pending=null;state.missing=false;state.error='';state.notice='被拒绝的原请求已独立保留，可修正输入后发起新操作。';notify();});},
  guard(){try{storage.getItem(prefix+'pending');return true;}catch{return false;}},
  dispose(){state.disposed=true;},
 };
 return controller;
}
