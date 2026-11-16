import {continuationCanonical as canonical,continuationHash as hash} from './same-goal-continuation-v312-controller.mjs';
import {withScopedRequest} from './scope-request-budget-v314.mjs';

export const HUMAN_BUDGET_PREFIX='ga-graph-human-budget:';
const UUID=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const SHA=/^[0-9a-f]{64}$/;
const positive=value=>Number.isSafeInteger(value)&&value>0;
const text=value=>typeof value==='string'&&Array.from(value).length<=2000&&!value.includes('\0');
const copy=value=>JSON.parse(JSON.stringify(value));
const need=(condition,message='原人工步骤申请尚未核对，填写内容和原请求保留。')=>{if(!condition)throw Error(message);};
const exact=(value,keys)=>value&&typeof value==='object'&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const blank=()=>({submitReason:'',decisions:{}});
const commandKeys={submit:['request_id','expected_task_version','path_hash','reason'],
 decide:['request_id','expected_task_version','submission_hash','decision','reason']};

function checkedView(value,taskId){
 const path=rows=>Array.isArray(rows)&&rows.every(row=>typeof row.activation_id==='string'&&/^a\d+$/.test(row.activation_id)
  &&typeof row.node_id==='string'&&typeof row.role==='string');
 need(value?.schema==='workflow-graph-human-budget-view-v1'&&value.task_id===taskId&&positive(value.task_version)
  &&path(value.path)&&(value.path_hash===null||SHA.test(value.path_hash))&&Array.isArray(value.records)
  &&typeof value.permissions?.can_submit==='boolean'&&typeof value.permissions?.can_decide==='boolean');
 const seen=new Set();
 for(const row of value.records){
  need(SHA.test(row.submission_hash)&&!seen.has(row.submission_hash)&&['in_review','approved','returned'].includes(row.status)
   &&Number.isSafeInteger(row.stage)&&row.stage>=0&&text(row.reason)&&path(row.path));
  seen.add(row.submission_hash);
 }
 need(value.records.filter(row=>row.status!=='returned').length<=1);
 return copy(value);
}
function displayError(error){
 if(error?.status===403)return '当前账号没有本次操作权限，原输入保留。';
 const value=error?.message;
 if(typeof value!=='string'||!value||/Traceback|TypeError|KeyError|AttributeError|secret|authorization|^\s*[\[{]/i.test(value))
  return '原申请结果尚未核对，请保留原编号。';
 return value.replace(/\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b/gi,'原记录').replace(/\b[a-f0-9]{64}\b/gi,'原校验记录').slice(0,300);
}
export class GraphHumanBudgetController{
 constructor({account,task,request,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto}){
  this.scope={actor_id:account.user_id,workspace_id:account.workspace_id,task_id:task.id,release_id:task.instance_release_id};
  need(Object.values(this.scope).every(value=>UUID.test(value)));
  Object.assign(this,{role:account.role,request,storage,session,locks,crypto});
  this.csrf=session.getItem('ga-setup-csrf');
  need(typeof this.csrf==='string'&&this.csrf.length>0);
  this.key=HUMAN_BUDGET_PREFIX+canonical(this.scope);
  this.base='/workspace/tasks/'+task.id+'/graph/human-budget';
  this.active=true;this.suspended=false;this.denied=false;this.epoch=0;
  this.context=null;this.draft=blank();this.pending=null;this.owner=null;
  this.localSaved=false;this.loading=false;this.busy=false;this.error='';this.notice='';this.onChange=()=>{};
 }
 _check(epoch=this.epoch){
  need(this.active&&!this.suspended&&epoch===this.epoch&&!this.denied);
  if(this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id
   ||this.session.getItem('ga-setup-csrf')!==this.csrf){
   this._deny();
   throw Error('当前账号或工作区已变化。');
  }
 }
 _deny(){this.denied=true;this.epoch++;this.context=null;this._emit();}
 _emit(){if(this.active&&!this.suspended)this.onChange();}
 view(){
  if(this.active&&!this.suspended&&!this.denied)try{this._check();}catch{}
  const hidden=this.denied||!this.active;
  return {denied:hidden,context:hidden?null:this.context,draft:hidden?null:this.draft,
   pending:hidden?null:this.pending,localSaved:this.localSaved,loading:this.loading,busy:this.busy,error:this.error,notice:this.notice};
 }
 _put(key,value){
  const raw=canonical(value);
  this.storage.setItem(key,raw);
  need(this.storage.getItem(key)===raw,'本机保存尚未读回，请重试。');
 }
 _saveDraft(){
  try{
   need(UUID.test(this.owner));
   this._put(this.key+':draft:'+this.owner,{scope:this.scope,draft:this.draft});
   this.localSaved=true;
   return true;
  }catch(error){this.localSaved=false;this.error=displayError(error);return false;}
 }
 _checkBody(operation,body){
  need(commandKeys[operation]&&exact(body,commandKeys[operation])&&UUID.test(body.request_id)&&positive(body.expected_task_version)
   &&text(body.reason)&&body.reason.trim());
  if(operation==='submit')need(SHA.test(body.path_hash));
  else need(SHA.test(body.submission_hash)&&['approve','return'].includes(body.decision));
 }
 _journal(){
  const raw=this.storage.getItem(this.key+':original');
  if(raw===null){need(!this.pending,'本机原申请缺失，不能另建。');return null;}
  const row=JSON.parse(raw);
  need(exact(row,['scope','operation','body','body_hash','state'])&&canonical(row.scope)===canonical(this.scope)
   &&SHA.test(row.body_hash)&&['pending','accepted'].includes(row.state)&&canonical(row)===raw);
  this._checkBody(row.operation,row.body);
  if(this.pending)need(row.body_hash===this.pending.body_hash&&canonical(row.body)===canonical(this.pending.body),
   '本机原申请已变化，请核对。');
  return row;
 }
 async _raw(path,options,epoch){
  const headers={'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.csrf};
  return withScopedRequest(()=>this.request(path,{...options,headers}),()=>this._check(epoch));
 }
 async _identity(epoch){
  let me;
  try{me=await this._raw('/me',{},epoch);}
  catch(error){this._check(epoch);if([401,403].includes(error.status))this._deny();throw error;}
  this._check(epoch);
  if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||me?.role!==this.role){
   this._deny();
   throw Error('当前账号或岗位已变化。');
  }
 }
 async _request(path,options={},epoch=this.epoch){
  await this._identity(epoch);
  this._check(epoch);
  let value;
  try{value=await this._raw(path,options,epoch);}
  catch(error){this._check(epoch);if(error.status===401||error.status===403&&!options.method)this._deny();throw error;}
  await this._identity(epoch);
  this._check(epoch);
  return value;
 }
 open(){
  if(!this.openPromise)this.openPromise=(async()=>{
   this._check();
   try{
    const slot=this.key+':tab',old=this.session.getItem(slot);
    if(old!==null){
     need(UUID.test(old));
     const prior=JSON.parse(this.storage.getItem(this.key+':draft:'+old));
     need(prior&&canonical(prior.scope)===canonical(this.scope)&&exact(prior.draft,Object.keys(blank()))
      &&text(prior.draft.submitReason)&&prior.draft.decisions&&typeof prior.draft.decisions==='object'&&!Array.isArray(prior.draft.decisions)
      &&Object.entries(prior.draft.decisions).every(([id,value])=>SHA.test(id)&&exact(value,['reason','choice'])
       &&text(value.reason)&&['','approve','return'].includes(value.choice)));
     this.draft=copy(prior.draft);
    }
    this.owner=this.crypto.randomUUID();
    need(this._saveDraft());
    this.session.setItem(slot,this.owner);
    need(this.session.getItem(slot)===this.owner);
    const original=this._journal();
    if(original?.state==='pending'){
     need(await hash(canonical(original.body),this.crypto)===original.body_hash);
     this._check();
     this.pending=original;
    }
   }catch(error){this.error=displayError(error);this.localSaved=false;this._emit();}
   if(this.active&&!this.suspended&&!this.denied)await this.refresh();
  })();
  return this.openPromise;
 }
 async refresh(){
  this._check();
  need(!this.busy&&!this.loading);
  const epoch=this.epoch;
  this.loading=true;this.context=null;this._emit();
  try{this.context=checkedView(await this._request(this.base,{},epoch),this.scope.task_id);}
  catch(error){if(this.active&&!this.denied&&epoch===this.epoch)this.error=displayError(error);}
  finally{this.loading=false;this._emit();}
 }
 update(patch){
  this._check();
  need(!this.busy&&!this.pending&&Object.entries(patch).every(([key,value])=>['submitReason','decisionReason','decision'].includes(key)&&text(value)));
  if('decision' in patch)need(['','approve','return'].includes(patch.decision));
  const next=copy(this.draft);
  if('submitReason' in patch)next.submitReason=patch.submitReason;
  if('decision' in patch||'decisionReason' in patch){
   const record=this.context?.records.find(row=>row.status==='in_review');
   need(record&&this.context.permissions.can_decide===true);
   const current=next.decisions[record.submission_hash]||{reason:'',choice:''};
   next.decisions[record.submission_hash]={reason:patch.decisionReason??current.reason,choice:patch.decision??current.choice};
  }
  this.draft=next;
  this._saveDraft();
  this._emit();
 }
 retrySave(){this._check();this._saveDraft();this._emit();}
 storageChanged(){
  try{
   this._check();
   const row=this._journal();
   if(row?.state==='pending')this.pending=row;
   else if(!this.busy)this.pending=null;
  }catch(error){this.localSaved=false;this.error=displayError(error);}
  this._emit();
 }
 _body(operation){
  const value=this.context;
  const body={request_id:this.crypto.randomUUID(),expected_task_version:value?.task_version};
  if(operation==='submit'){
   need(value?.permissions.can_submit===true&&value.path_hash&&value.path.length);
   Object.assign(body,{path_hash:value.path_hash,reason:this.draft.submitReason});
  }else{
   const record=value?.records.find(row=>row.status==='in_review');
   need(operation==='decide'&&value?.permissions.can_decide===true&&record);
   const human=this.draft.decisions[record.submission_hash];
   need(human);
   Object.assign(body,{submission_hash:record.submission_hash,decision:human.choice,reason:human.reason});
  }
  this._checkBody(operation,body);
  return body;
 }
 async submit(operation){
  this._check();
  need(!this.busy&&!this.loading&&this.localSaved&&!this.pending);
  const body=this._body(operation);
  return this._perform(async epoch=>{
   need(this._journal()?.state!=='pending','已有待核对的原申请，请先按原编号重试。');
   const body_hash=await hash(canonical(body),this.crypto);
   this._check(epoch);
   need(this._saveDraft());
   const original={scope:this.scope,operation,body,body_hash,state:'pending'};
   this._put(this.key+':original',original);
   this.pending=copy(original);
   this._emit();
   return original;
  });
 }
 async retry(){
  this._check();
  need(!this.busy&&!this.loading&&this.pending);
  return this._perform(async()=>{const row=this._journal();need(row?.state==='pending');return row;});
 }
 async _perform(prepare){
  need(typeof this.locks?.request==='function','浏览器无法协调多个窗口，请先保留原输入。');
  const epoch=this.epoch;
  this.busy=true;this.error='';this.notice='';this._emit();
  try{
   await this.locks.request(this.key+':write',{mode:'exclusive'},async()=>{
    this._check(epoch);
    const original=await prepare(epoch);
    const body=canonical(original.body);
    need(await hash(body,this.crypto)===original.body_hash);
    this._check(epoch);
    const path=original.operation==='submit'?this.base:this.base+'/decide';
    const value=checkedView(await this._request(path,{method:'POST',body},epoch),this.scope.task_id);
    this._check(epoch);
    need(this._journal()?.body_hash===original.body_hash);
    this._put(this.key+':original',{...original,state:'accepted'});
    this.context=value;this.pending=null;this.notice='原操作已保存。';
   });
  }catch(error){
   if(this.active&&!this.denied&&epoch===this.epoch){
    this.error=displayError(error);
    try{const row=this._journal();if(row?.state==='pending')this.pending=row;}
    catch{this.localSaved=false;}
   }
  }finally{this.busy=false;this._emit();}
  return this.notice==='原操作已保存。';
 }
 guard(){
  try{this._check();if(this.busy||!this._saveDraft())return false;this._journal();return true;}
  catch{return false;}
 }
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.context=null;this.onChange=()=>{};}
}
