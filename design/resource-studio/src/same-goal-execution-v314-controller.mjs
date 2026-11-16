import {SameGoalContinuationControllerV312,continuationCanonical as canonical,continuationHash as hash} from './same-goal-continuation-v312-controller.mjs';

export const EXECUTION_PREFIX='ga-same-goal-execution-v314:';
const SCHEMA='same-goal-continuation-execution-';
const GRANT_SCHEMA='same-goal-continuation-grant-';
const UUID=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const SHA=/^[a-f0-9]{64}$/;
const COMMANDS=['submit','decide','start','cancel','review','recover'];
const GRANT_COMMANDS=['issue','revoke'];
const OPERATIONS=['mean','sum','min','max','count'];
const PHASES=['in_review','approved','returned','queued','cancel_requested','cancelled','adopted','result_returned'];
const positive=value=>Number.isSafeInteger(value)&&value>0;
const copy=value=>JSON.parse(JSON.stringify(value));
const need=(value,message='办理记录尚不能核对，原输入与原申请保留。')=>{if(!value)throw Error(message);};
const text=value=>typeof value==='string'&&Array.from(value).length<=2000&&!value.includes('\0');
const meaningful=value=>text(value)&&!!value.trim();
const equal=(a,b)=>canonical(a)===canonical(b);
const fields=['decide_reason','start_reason','cancel_reason','review_reason','recover_reason','decide_choice','review_choice'];
const grantDraft=()=>({toolId:null,field:'',operation:'',reason:'',revocations:{}});
const empty=()=>({grantId:null,rationale:'',actions:{},grantSetup:grantDraft(),supersedesPlanId:null});
const permissions=['can_decide','can_start','can_cancel','can_review','can_recover'];
const validFlags=value=>['can_dispatch','execution_approved','goal_completion_verified','cloud_send_authorized'].every(key=>value[key]===false);
const exact=(value,keys)=>value&&typeof value==='object'&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
function problem(error){
 if(error?.status===403)return '当前账号无权提交这项操作。原申请与输入保留。';
 const message=typeof error?.message==='string'?error.message:'';
 if(!message||/Traceback|TypeError|KeyError|AttributeError|secret|authorization|^\s*[\[{]/i.test(message))return '操作结果尚未核对，请读取原记录。';
 return message.replace(/\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b/gi,'对应记录').replace(/\b[a-f0-9]{64}\b/gi,'原校验记录').slice(0,500);
}
function checkedScope(value,scope,schema,family=SCHEMA){
 need(value?.schema===family+schema&&value.task_id===scope.task_id&&value.advice_plan_id===scope.advice_plan_id&&value.continuation_id===scope.continuation_id);
 return value;
}
function capabilityPin(value){
 return exact(value,['id','content_hash','release_version'])&&UUID.test(value.id)&&SHA.test(value.content_hash)
  &&typeof value.release_version==='string'&&value.release_version.length>0;
}
function policyRef(value){
 return exact(value,['policy_id','version_id','revision','record_hash'])&&UUID.test(value.policy_id)&&UUID.test(value.version_id)
  &&positive(value.revision)&&SHA.test(value.record_hash);
}
function checkedGrant(value,scope){
 checkedScope(value,scope,'view-v313',GRANT_SCHEMA);
 need(UUID.test(value.grant_id)&&positive(value.version)&&['active','revoked'].includes(value.phase)
  &&value.grant_ref?.id===value.grant_id&&SHA.test(value.grant_ref.record_hash)
  &&typeof value.expired==='boolean'&&typeof value.permissions?.can_revoke==='boolean'
  &&typeof value.field?.label==='string'&&OPERATIONS.includes(value.operation)&&value.max_calls===1&&validFlags(value));
 return copy(value);
}
function checkedGrantContext(value,scope){
 checkedScope(value,scope,'context-v313',GRANT_SCHEMA);
 need(positive(value.task_version)&&positive(value.continuation_version)&&SHA.test(value.revision_hash)&&SHA.test(value.source_hash)
  &&Array.isArray(value.tools)&&Array.isArray(value.operations)&&value.operations.every(item=>OPERATIONS.includes(item))
  &&Array.isArray(value.blocked_reasons)&&typeof value.permissions?.can_issue==='boolean'&&validFlags(value));
 need(value.policy===null||policyRef(value.policy?.ref)&&Array.isArray(value.policy.stages));
 for(const tool of value.tools){
  need(capabilityPin({id:tool.id,content_hash:tool.content_hash,release_version:tool.release_version})
   &&typeof tool.name==='string'&&Array.isArray(tool.fields)&&tool.fields.every(field=>typeof field.key==='string'&&typeof field.label==='string'));
 }
 return copy(value);
}
function checkedProposal(value,scope){
 checkedScope(value,scope,'preview-v314');
 need(SHA.test(value.preview_hash)&&value.continuation_ref?.id===scope.continuation_id&&positive(value.continuation_ref.version)
  &&SHA.test(value.continuation_ref.revision_hash)&&SHA.test(value.continuation_ref.source_hash)
  &&UUID.test(value.grant_ref?.id)&&value.grant_ref.version===1&&SHA.test(value.grant_ref.record_hash)
  &&value.plan_origin==='human_revision'&&meaningful(value.rationale)
  &&(value.supersedes_plan_id===null||UUID.test(value.supersedes_plan_id))&&positive(value.record_count)
  &&Number.isSafeInteger(value.missing_count)&&value.missing_count>=0&&value.missing_count<=value.record_count
  &&typeof value.field?.label==='string'&&['mean','sum','min','max','count'].includes(value.operation));
 return value;
}
function checkedPlan(value,scope,id=null){
 checkedScope(value,scope,'view-v314');
 need(UUID.test(value.plan_id)&&(!id||id===value.plan_id)&&positive(value.version)&&PHASES.includes(value.phase)&&validFlags(value)
  &&SHA.test(value.submission_hash)&&typeof value.data_received==='boolean'&&Array.isArray(value.operations)
  &&Number.isSafeInteger(value.stage)&&value.stage>=0
  &&permissions.every(key=>typeof value.permissions?.[key]==='boolean'));
 need(checkedProposal(value.proposal,scope).preview_hash===value.submission_hash);
 need(value.output===null?value.output_hash===null:SHA.test(value.output_hash));
 need(!value.data_received||value.output!==null);
 if(value.output!==null){
  const output=value.output;
  need(output.field===value.proposal.field.key&&output.operation===value.proposal.operation
   &&(output.value===null||Number.isFinite(output.value))
   &&Number.isSafeInteger(output.sample_count)&&output.sample_count>=0
   &&Number.isSafeInteger(output.missing_count)&&output.missing_count>=0
   &&output.sample_count+output.missing_count===value.proposal.record_count);
 }
 return copy(value);
}
function checkedContext(value,scope){
 checkedScope(value,scope,'context-v314');
 need(positive(value.task_version)&&positive(value.continuation_version)&&SHA.test(value.revision_hash)&&SHA.test(value.source_hash)
  &&validFlags(value)&&Array.isArray(value.grants)&&Array.isArray(value.blocked_reasons)&&Array.isArray(value.returned_candidates)
  &&['can_preview','can_submit'].every(key=>typeof value.permissions?.[key]==='boolean'));
 for(const grant of value.grants){
  need(grant.task_id===scope.task_id&&grant.advice_plan_id===scope.advice_plan_id&&grant.continuation_id===scope.continuation_id
   &&UUID.test(grant.grant_id)&&grant.grant_ref?.id===grant.grant_id&&SHA.test(grant.grant_ref.record_hash)
   &&typeof grant.eligible==='boolean'&&typeof grant.expired==='boolean');
 }
 const candidates=new Set();
 for(const row of value.returned_candidates){
  need(UUID.test(row.plan_id)&&!candidates.has(row.plan_id)&&['returned','result_returned','cancelled','failed'].includes(row.phase)&&typeof row.label==='string');
  candidates.add(row.plan_id);
 }
 return copy(value);
}

// A correction may retain an unspent authorization. Its ancestry must be
// readable before offering a grant whose prior call could have been reserved.
export function correctionGrantUsable(grant,correctionId,records){
 if(correctionId===null)return true;
 const byId=new Map((records||[]).map(row=>[row.plan_id,row]));
 const seen=new Set();
 while(correctionId!==null){
  const prior=byId.get(correctionId);
  if(!prior||seen.has(correctionId))return false;
  seen.add(correctionId);
  const started=prior.job!==null||prior.operations.some(row=>row.operation==='start');
  if(started&&prior.proposal.grant_ref.id===grant.grant_id)return false;
  correctionId=prior.proposal.supersedes_plan_id;
 }
 return true;
}

// Reuse the existing per-tab ownership, local readback,
// explicit recovery/resend and synchronous leave guard. This family supplies
// its own closed request, scope and server receipt checks.
export class SameGoalExecutionControllerV314 extends SameGoalContinuationControllerV312{
 constructor({continuation,...options}){
  super(options);
  need(UUID.test(continuation?.continuation_id)&&typeof this.csrf==='string'&&this.csrf.length>0);
  this.scope.continuation_id=continuation.continuation_id;
  this.key=EXECUTION_PREFIX+canonical(this.scope);
  const continuationBase=this.base+'/'+continuation.continuation_id;
  this.base=continuationBase+'/executions';
  this.grantBase=continuationBase+'/tool-grants';
  this.draft=empty();
  this.grantOpen=false;
  this.grantContext=this.grantList=null;
  this.grantLoading=false;
  this.grantNeedsRefresh=false;
 }
 view(){
  const value=super.view(),hidden=value.denied||!this.active;
  return {...value,role:this.role,grantOpen:this.grantOpen,grantLoading:this.grantLoading,
   grantContext:hidden?null:copy(this.grantContext),grantList:hidden?null:copy(this.grantList)};
 }
 _deny(error){this.grantContext=this.grantList=null;super._deny(error);}
 dispose(){this.grantContext=this.grantList=null;super.dispose();}
 async _request(path,options={},epoch=this.epoch){
  await this._identity(epoch);
  this._check(epoch);
  let result;
  try{result=await this.requestAPI(path,{...options,headers:this._headers()});}
  catch(error){
   this._check(epoch);
   if(error.status===401||error.status===403&&!options.method)this._deny(error);
   throw error;
  }
  await this._identity(epoch);
  this._check(epoch);
  return result;
 }
 _localRow(){return {schema:SCHEMA+'local-draft-v314',scope:this.scope,owner:this.owner,draft:this.draft,selected_id:this.selectedId};}
 async _load(){
  this._check();
  const slot=this.key+':tab-draft';
  const owner=this.session.getItem(slot);
  if(owner){
   need(UUID.test(owner));
   const row=JSON.parse(this.storage.getItem(this.key+':draft:'+owner));
   need(row?.schema===SCHEMA+'local-draft-v314'&&row.owner===owner&&equal(row.scope,this.scope));
   const draft=row.draft;
   need((exact(draft,['grantId','rationale','actions'])||exact(draft,['grantId','rationale','actions','grantSetup'])
     ||exact(draft,['grantId','rationale','actions','grantSetup','supersedesPlanId']))
    &&(draft.grantId===null||UUID.test(draft.grantId))&&text(draft.rationale)
    &&draft.actions&&typeof draft.actions==='object'&&!Array.isArray(draft.actions));
   for(const [id,action] of Object.entries(draft.actions)){
    need(UUID.test(id)&&Object.keys(action).every(key=>fields.includes(key)&&text(action[key])));
   }
   need(row.selected_id===null||UUID.test(row.selected_id));
   const setup=draft.grantSetup??grantDraft();
   need(exact(setup,['toolId','field','operation','reason','revocations'])
    &&(setup.toolId===null||UUID.test(setup.toolId))&&text(setup.field)&&text(setup.reason)
    &&(setup.operation===''||OPERATIONS.includes(setup.operation))&&setup.revocations&&typeof setup.revocations==='object'&&!Array.isArray(setup.revocations)
    &&Object.entries(setup.revocations).every(([id,reason])=>UUID.test(id)&&text(reason)));
   const supersedesPlanId=draft.supersedesPlanId??null;
   need(supersedesPlanId===null||UUID.test(supersedesPlanId));
   this.draft=copy({...draft,grantSetup:setup,supersedesPlanId});
   this.selectedId=row.selected_id;
  }
  this.owner=this.crypto.randomUUID();
  need(this._saveDraft());
  this.session.setItem(slot,this.owner);
  need(this.session.getItem(slot)===this.owner);
  this._inventory();
  for(const {row} of this.unsettled){
   need(await hash(canonical(row.body),this.crypto)===row.body_hash);
   this._check();
  }
 }
 _path(operation,id){
  if(GRANT_COMMANDS.includes(operation)){
   need(operation==='issue'?id===null:UUID.test(id));
   return operation==='issue'?this.grantBase:this.grantBase+'/'+id+'/revoke';
  }
  need(COMMANDS.includes(operation)&&(operation==='submit'?id===null:UUID.test(id)));
  return operation==='submit'?this.base:this.base+'/'+id+'/'+operation;
 }
 _checkBody(operation,body){
  need(UUID.test(body?.request_id));
  if(operation==='issue'){
   need(exact(body,['request_id','expected_task_version','expected_continuation_version','revision_hash','source_hash','capability_pin','field','operation','policy_ref','validity_seconds','reason'])
    &&positive(body.expected_task_version)&&positive(body.expected_continuation_version)&&SHA.test(body.revision_hash)&&SHA.test(body.source_hash)
    &&capabilityPin(body.capability_pin)&&meaningful(body.field)&&Array.from(body.field).length<=100&&OPERATIONS.includes(body.operation)
    &&policyRef(body.policy_ref)&&body.validity_seconds===7200&&meaningful(body.reason));
   return;
  }
  if(operation==='revoke'){
   need(exact(body,['request_id','expected_grant_version','reason'])&&positive(body.expected_grant_version)&&meaningful(body.reason));
   return;
  }
  if(operation==='submit'){
   need(exact(body,['request_id','expected_task_version','expected_continuation_version','revision_hash','source_hash','grant_ref','plan_origin','rationale','supersedes_plan_id','preview_hash'])
    &&positive(body.expected_task_version)&&positive(body.expected_continuation_version)&&[body.revision_hash,body.source_hash,body.preview_hash].every(value=>SHA.test(value))
    &&exact(body.grant_ref,['id','record_hash','version'])&&UUID.test(body.grant_ref.id)&&SHA.test(body.grant_ref.record_hash)
    &&body.grant_ref.version===1&&body.plan_origin==='human_revision'&&meaningful(body.rationale)
    &&(body.supersedes_plan_id===null||UUID.test(body.supersedes_plan_id)));
   return;
  }
  const extra=operation==='decide'?['submission_hash','decision']:operation==='review'?['output_hash','decision']:[];
  need(exact(body,['request_id','expected_execution_version','reason',...extra])&&positive(body.expected_execution_version)&&meaningful(body.reason));
  if(operation==='decide')need(SHA.test(body.submission_hash)&&['approve','return'].includes(body.decision));
  if(operation==='review')need(SHA.test(body.output_hash)&&['adopt','return'].includes(body.decision));
 }
 _inventory(){
  const keys=this._keys(this.key+':original:');
  need(keys.length<=100);
  const pointerRaw=this.storage.getItem(this.key+':pending');
  const pointer=pointerRaw===null?null:JSON.parse(pointerRaw);
  if(pointerRaw!==null)need(pointer?.schema===SCHEMA+'local-pending-v314'&&UUID.test(pointer.request_id)&&SHA.test(pointer.body_hash)
   &&keys.includes(this.key+':original:'+pointer.request_id),'本机原申请正文缺失，不能另建申请。');
  if(this.pending)need(this.storage.getItem(this.key+':original:'+this.pending.request_id)===canonical(this.pending),
   '本机原申请正文缺失或变化，不能另建申请。');
  const unresolved=[];
  for(const key of keys){
   const raw=this.storage.getItem(key),row=JSON.parse(raw);
   need(row?.schema===SCHEMA+'local-command-v314'&&raw===canonical(row)&&equal(row.scope,this.scope)&&UUID.test(row.request_id)
    &&SHA.test(row.body_hash)&&row.body?.request_id===row.request_id&&key===this.key+':original:'+row.request_id
    &&row.path===this._path(row.operation,this._target(row)));
   this._checkBody(row.operation,row.body);
   if(pointer?.request_id===row.request_id)need(pointer.body_hash===row.body_hash);
   const saved=this.storage.getItem(this.key+':settled:'+row.request_id),state=saved===null?null:JSON.parse(saved);
   if(saved!==null){
    const legacy=state?.schema==='same-goal-continuation-local-disposition-v312';
    need((legacy?this._family(row.operation)==='execution':state?.schema===SCHEMA+'local-disposition-v314'&&state.family===this._family(row.operation))
     &&state.request_id===row.request_id&&state.body_hash===row.body_hash);
    if(state.state==='rejected'){
     const rejected=this._rejection(row);
     need(rejected&&state.receipt===null);
    }else{
     const target=legacy?state.receipt?.continuation_id:state.receipt?.target_id;
     need(['received','needs_check'].includes(state.state)&&state.receipt?.operation===row.operation
      &&UUID.test(target)&&(!this._target(row)||target===this._target(row))
      &&state.receipt.body_hash===row.body_hash&&state.receipt.result_status===(state.state==='received'?'available':'needs_check'));
    }
   }
   if(!state||state.state==='needs_check')unresolved.push({row,state});
  }
  this.unsettled=unresolved;
  const item=unresolved.find(value=>value.row.request_id===this.pending?.request_id)||unresolved[0];
  this.pending=item?copy(item.row):null;
  this.pendingRejected=item?this._rejection(item.row):null;
  this.needsCheck=unresolved.some(value=>value.state?.state==='needs_check');
 }
 _family(operation){return GRANT_COMMANDS.includes(operation)?'grant':'execution';}
 _target(row){
  const family=this._family(row.operation);
  if(Object.hasOwn(row,'family')){
   need(row.family===family&&Object.hasOwn(row,'target_id')&&!Object.hasOwn(row,'continuation_id'));
   return row.target_id;
  }
  need(family==='execution'&&Object.hasOwn(row,'continuation_id'));
  return row.continuation_id;
 }
 _rejection(row){
  const raw=this.storage.getItem(this.key+':rejected-response:'+row.request_id);
  if(raw===null)return null;
  const value=JSON.parse(raw);
  need(value?.request_id===row.request_id&&value.body_hash===row.body_hash&&[400,403,409,413,422].includes(value.status));
  return {request_id:value.request_id,status:value.status};
 }
 open(){
  if(!this.openPromise)this.openPromise=(async()=>{
   let failure='';
   try{
    await this._load();
    this._check();
    if(this.pending&&this.locks?.request)await this._locked(()=>this._recover(false));
   }catch(error){if(!this.denied)failure=problem(error);}
   if(!this.active||this.suspended||this.denied)return;
   try{await this.refresh();}
   catch(error){if(!this.denied)failure=problem(error);}
   if(this.active&&!this.suspended&&!this.denied){
    if(failure)this.error=failure;
    this.loading=false;
    this._emit();
   }
  })();
  return this.openPromise;
 }
 async refresh(){
  this._check();
  need(!this.busy&&!this.refreshing&&!this.grantLoading);
  this.refreshing=true;
  this.loading=true;
  this.context=this.list=this.plan=this.preview=null;
  this.error='';
  this._emit();
  const epoch=this.epoch;
  const selected=this.selectedId;
  const read=async(name,path,validate)=>{
   try{
    const value=await this._request(path,{},epoch);
    this._check(epoch);
    this[name]=validate(value);
    this._emit();
   }catch(error){
    if(this.active&&!this.denied&&epoch===this.epoch){this.error=problem(error);this._emit();}
   }
  };
  try{
   await Promise.allSettled([
    read('context',this.base+'/context',value=>checkedContext(value,this.scope)),
    read('list',this.base,value=>{
     checkedScope(value,this.scope,'view-v314/list');
     need(Array.isArray(value.records));
     return {...value,records:value.records.map(plan=>checkedPlan(plan,this.scope))};
    }),
    ...(selected?[read('plan',this.base+'/'+selected,value=>checkedPlan(value,this.scope,selected))]:[]),
   ]);
   this._check(epoch);
   this._inventory();
   if(this.grantOpen)await this._readGrants(epoch);
   this.grantNeedsRefresh=false;
  }catch(error){
   if(this.active&&!this.denied&&epoch===this.epoch){this.localSaved=false;this.error=problem(error);}
  }finally{
   this.refreshing=false;
   this.loading=false;
   this._emit();
  }
 }
 async openGrants(){
  this._check();
  need(this.role==='admin'&&!this.busy&&!this.loading&&!this.grantLoading);
  this.grantOpen=true;
  await this._readGrants(this.epoch);
 }
 async _readGrants(epoch){
  this._check(epoch);
  need(this.role==='admin'&&!this.grantLoading);
  this.grantLoading=true;
  this.grantContext=this.grantList=null;
  this._emit();
  try{
   const [context,list]=await Promise.all([
    this._request(this.grantBase+'/context',{},epoch),
    this._request(this.grantBase,{},epoch),
   ]);
   this._check(epoch);
   checkedScope(list,this.scope,'view-v313/list',GRANT_SCHEMA);
   need(Array.isArray(list.records));
   this.grantList={...list,records:list.records.map(row=>checkedGrant(row,this.scope))};
   this.grantContext=checkedGrantContext(context,this.scope);
  }catch(error){
   if(this.active&&!this.suspended&&!this.denied&&epoch===this.epoch)this.error=problem(error);
  }finally{this.grantLoading=false;this._emit();}
 }
 updateGrantDraft(patch){
  this._check();
  need(this.role==='admin'&&!this.busy&&!this.pending&&!this.needsCheck);
  const next=copy(this.draft.grantSetup);
  for(const [key,value] of Object.entries(patch)){
   if(key==='toolId'){
    need(value===null||UUID.test(value));
    if(next.toolId!==value)next.field='';
    next.toolId=value;
   }else if(key==='operation'){
    need(value===''||OPERATIONS.includes(value));next.operation=value;
   }else if(key==='field'||key==='reason'){
    need(text(value));next[key]=value;
   }else{
    need(key==='revoke'&&UUID.test(value?.id)&&text(value?.reason));
    next.revocations[value.id]=value.reason;
   }
  }
  this.draft={...this.draft,grantSetup:next};
  this._saveDraft();
  this._emit();
 }
 async issueGrant(){
  this._ready();
  const context=this.grantContext,form=this.draft.grantSetup;
  need(this.role==='admin'&&context?.permissions.can_issue===true&&context.policy&&!this.grantLoading);
  const tool=context.tools.find(row=>row.id===form.toolId&&row.fields.some(field=>field.key===form.field));
  need(tool&&context.operations.includes(form.operation)&&meaningful(form.reason),'请选择工具、字段和计算方式，并填写授权说明。');
  await this._writeCommand('issue',{request_id:this.crypto.randomUUID(),expected_task_version:context.task_version,
   expected_continuation_version:context.continuation_version,revision_hash:context.revision_hash,source_hash:context.source_hash,
   capability_pin:{id:tool.id,content_hash:tool.content_hash,release_version:tool.release_version},field:form.field,
   operation:form.operation,policy_ref:context.policy.ref,validity_seconds:7200,reason:form.reason});
 }
 async revokeGrant(id){
  this._ready();
  need(this.role==='admin'&&!this.grantLoading);
  const row=this.grantList?.records.find(value=>value.grant_id===id&&value.phase==='active'&&value.permissions.can_revoke);
  const reason=this.draft.grantSetup.revocations[id];
  need(row&&meaningful(reason),'请选择可撤销的原授权并填写原因。');
  await this._writeCommand('revoke',{request_id:this.crypto.randomUUID(),expected_grant_version:row.version,reason},id);
 }
 async select(id){
  this._check();
  need(!this.loading&&!this.pending&&this.list?.records.some(plan=>plan.plan_id===id)&&this.guard());
  this.selectedId=id;
  need(this._saveDraft());
  await this.refresh();
 }
 updateDraft(patch){
  this._check();
  need(!this.busy&&!this.pending&&!this.needsCheck);
  const next=copy(this.draft);
  for(const [key,value] of Object.entries(patch)){
   if(key==='grantId'){need(value===null||UUID.test(value));next.grantId=value;}
   else if(key==='rationale'){need(text(value));next.rationale=value;}
   else if(key==='supersedesPlanId'){
    need(value===null||this.context?.returned_candidates.some(row=>row.plan_id===value),'请先明确选择服务器列出的原办理记录。');
    next.supersedesPlanId=value;
   }
   else{
    need(fields.includes(key)&&text(value)&&this.plan&&this.selectedId===this.plan.plan_id);
    next.actions[this.selectedId]={...next.actions[this.selectedId],[key]:value};
   }
  }
  this.draft=next;
  if('grantId' in patch||'rationale' in patch||'supersedesPlanId' in patch)this.preview=null;
  this._saveDraft();
  this._emit();
 }
 _ready(){
  this._check();
  need(!this.loading&&!this.grantLoading&&!this.busy&&this.localSaved&&!this.pending&&!this.needsCheck,
   '请先核对原申请并确认本机输入已保存。');
 }
 _proposalBody(){
  const grant=this.context?.grants.find(row=>row.grant_id===this.draft.grantId&&row.eligible&&row.phase==='active'&&!row.expired&&row.version===1);
  need(grant&&meaningful(this.draft.rationale),'请选择可用授权并填写人工办理理由。');
  const correction=this.draft.supersedesPlanId;
  need(correction===null||this.context.returned_candidates.some(row=>row.plan_id===correction),'原办理已不在可补正范围，请重新核对。');
  need(correctionGrantUsable(grant,correction,this.list?.records),'请核对原办理范围，并为已预留执行的授权登记新的范围。');
  return {request_id:this.crypto.randomUUID(),expected_task_version:this.context.task_version,
   expected_continuation_version:this.context.continuation_version,revision_hash:this.context.revision_hash,
   source_hash:this.context.source_hash,grant_ref:{...grant.grant_ref,version:1},plan_origin:'human_revision',
   rationale:this.draft.rationale,supersedes_plan_id:correction};
 }
 async preparePreview(){
  this._ready();
  need(this.context?.permissions.can_preview===true);
  const body=this._proposalBody(),epoch=this.epoch;
  this.busy=true;this.error='';this.preview=null;this._emit();
  try{
   const bodyHash=await hash(canonical(body),this.crypto);
   this._check(epoch);
   need(this._saveDraft());
   this._put(this.key+':preview-original:'+body.request_id,{scope:this.scope,body,body_hash:bodyHash},true);
   const value=checkedProposal(await this._request(this.base+'/preview',{method:'POST',body:canonical(body)},epoch),this.scope);
   need(equal(value.grant_ref,body.grant_ref)&&value.rationale===body.rationale
    &&value.supersedes_plan_id===body.supersedes_plan_id
    &&value.continuation_ref.version===body.expected_continuation_version
    &&value.continuation_ref.revision_hash===body.revision_hash&&value.continuation_ref.source_hash===body.source_hash);
   this.preview=copy(value);
  }catch(error){if(this.active&&!this.denied&&epoch===this.epoch)this.error=problem(error);}
  finally{this.busy=false;this._emit();}
 }
 async submit(){
  this._ready();
  need(this.context?.permissions.can_submit===true&&this.preview);
  const body=this._proposalBody();
  need(equal(body.grant_ref,this.preview.grant_ref)&&body.rationale===this.preview.rationale
   &&body.supersedes_plan_id===this.preview.supersedes_plan_id);
  await this._writeCommand('submit',{...body,preview_hash:this.preview.preview_hash});
 }
 async command(operation){
  this._ready();
  need(operation!=='submit'&&COMMANDS.includes(operation)&&this.plan?.permissions['can_'+operation]===true);
  const form=this.draft.actions[this.plan.plan_id]||{};
  const body={request_id:this.crypto.randomUUID(),expected_execution_version:this.plan.version,reason:form[operation+'_reason']};
  if(operation==='decide')Object.assign(body,{submission_hash:this.plan.submission_hash,decision:form.decide_choice});
  if(operation==='review'){
   need(this.plan.job?.status==='succeeded'&&this.plan.data_received===true&&this.plan.output!==null);
   Object.assign(body,{output_hash:this.plan.output_hash,decision:form.review_choice});
  }
  await this._writeCommand(operation,body,this.plan.plan_id);
 }
 async _writeCommand(operation,body,id=null){
  this._checkBody(operation,body);
  const epoch=this.epoch;
  this.busy=true;this.error='';this._emit();
  try{
   await this._locked(async()=>{
    this._check(epoch);
    this._inventory();
    need(this.unsettled.length===0,'另一窗口的原申请仍需核对。');
    const bodyHash=await hash(canonical(body),this.crypto);
    this._check(epoch);
    need(this._saveDraft());
    const original={schema:SCHEMA+'local-command-v314',scope:this.scope,family:this._family(operation),operation,target_id:id,
     request_id:body.request_id,path:this._path(operation,id),body,body_hash:bodyHash};
    this._put(this.key+':original:'+body.request_id,original,true);
    this._put(this.key+':pending',{schema:SCHEMA+'local-pending-v314',request_id:body.request_id,body_hash:bodyHash});
    this._inventory();
    this._emit();
    await this._postOriginal(original);
   });
  }catch(error){if(this.active&&!this.denied&&epoch===this.epoch)this.error=problem(error);}
  finally{this.busy=false;this._emit();}
  await this._refreshGrantResult();
 }
 async recover(){await super.recover();await this._refreshGrantResult();}
 async resendOriginal(){await super.resendOriginal();await this._refreshGrantResult();}
 async releaseRejected(){await super.releaseRejected();await this._refreshGrantResult();}
 async _refreshGrantResult(){
  if(!this.grantNeedsRefresh||!this.active||this.suspended||this.denied)return;
  this.grantNeedsRefresh=false;
  await this.refresh();
 }
 _disposition(original,state,proof=null){
  const family=this._family(original.operation);
  this._put(this.key+':settled:'+original.request_id,{schema:SCHEMA+'local-disposition-v314',family,request_id:original.request_id,
   body_hash:original.body_hash,state,receipt:proof?{operation:proof.operation,target_id:family==='grant'?proof.grant_id:proof.plan_id,
    body_hash:proof.body_hash,result_status:proof.result_status}:null});
  if(state!=='needs_check'){
   const raw=this.storage.getItem(this.key+':pending');
   if(raw&&JSON.parse(raw).request_id===original.request_id)this.storage.removeItem(this.key+':pending');
  }
  this._inventory();
 }
 async _receipt(original){
  const epoch=this.epoch;
  need(await hash(canonical(original.body),this.crypto)===original.body_hash);
  this._check(epoch);
  const grant=this._family(original.operation)==='grant';
  const base=grant?this.grantBase:this.base;
  const value=checkedScope(await this._request(base+'/operations/'+original.request_id,{},epoch),this.scope,
   grant?'receipt-v313':'receipt-v314',grant?GRANT_SCHEMA:SCHEMA);
  const target=grant?value.grant_id:value.plan_id;
  need(value.found===true&&value.request_id===original.request_id&&value.operation===original.operation
   &&value.actor_id===this.scope.actor_id&&value.body_hash===original.body_hash&&UUID.test(target)
   &&(!this._target(original)||target===this._target(original))&&['available','needs_check'].includes(value.result_status));
  if(value.result_status==='needs_check'){
   need(value.result===null);
   this._disposition(original,'needs_check',value);
   this.notice='原申请已保存，结果仍需核对。';
   return false;
  }
  if(grant){
   const saved=checkedGrant(value.result,this.scope);
   need(saved.grant_id===target);
   this._disposition(original,'received',value);
   this.grantNeedsRefresh=true;
   this.preview=null;
   this.notice=original.operation==='issue'?'本次工具范围已登记，请从可用授权中选择。':'原授权撤销已保存。';
   return true;
  }
  const plan=checkedPlan(value.result,this.scope,value.plan_id);
  this._disposition(original,'received',value);
  this.plan=plan;
  this.selectedId=plan.plan_id;
  this.preview=null;
  this.list={...(this.list||{}),records:[...(this.list?.records||[]).filter(row=>row.plan_id!==plan.plan_id),plan]};
  this._saveDraft();
  this.notice='原操作已保存。';
  return true;
 }
}
