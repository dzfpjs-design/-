import {copy,same,digest} from './report-configuration-contract.mjs';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH=/^[0-9a-f]{64}$/;
const need=(value,message='业务要求核对的范围或保存回执无法核对，原输入保留。')=>{if(!value)throw Error(message);};
const omit=(value,key)=>Object.fromEntries(Object.entries(value).filter(([name])=>name!==key));
const scopeOf=(account,id)=>[account.user_id,account.workspace_id,id].join(':');
const textHash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(v=>v.toString(16).padStart(2,'0')).join('');

function targetValue(candidate,pointer){
 need(typeof pointer==='string'&&pointer.startsWith('/'));
 let value=candidate;
 for(const encoded of pointer.slice(1).split('/')){
  need(!/~(?:[^01]|$)/.test(encoded));const token=encoded.replace(/~1/g,'/').replace(/~0/g,'~');
  if(Array.isArray(value)){need(/^(0|[1-9][0-9]*)$/.test(token)&&Number(token)<value.length);value=value[Number(token)];}
  else {need(value&&typeof value==='object'&&Object.hasOwn(value,token));value=value[token];}
 }
 return value;
}

export async function verifyRequirementSaved(saved,account,draftID,pending=null){
 const r=saved?.receipt,c=saved?.checked,b=saved?.binding;
 need(UUID.test(saved?.check_id)&&Number.isSafeInteger(saved.version)&&saved.version>0
  &&saved.can_apply===false&&saved.can_publish===false&&saved.semantic_completeness_verified===false);
 need(r?.schema==='report-source-requirement-receipt-v1'&&r.actor_id===account.user_id
  &&r.workspace_id===account.workspace_id&&r.draft_id===draftID&&r.check_id===saved.check_id&&r.item_version===saved.version);
 need(c?.schema==='report-source-requirement-checked-v1'&&c.record?.schema==='report-source-requirement-check-v1');
 need(await digest(c.record)===c.record_hash&&await digest(omit(c,'check_hash'))===c.check_hash);
 need(await digest(c)===r.checked_hash&&await digest(b)===r.binding_hash&&await digest(omit(r,'receipt_hash'))===r.receipt_hash);
 need(['current','stale','pending_candidate','history'].includes(saved.review_status?.status));
 if(pending){
  const command=pending.body;
  const commandHash=await digest({kind:'source_requirement_check',workspace_id:account.workspace_id,
   actor_id:account.user_id,draft_id:draftID,body:command});
  need(same(c.record,command.value)&&same(b,pending.binding)&&r.command_hash===commandHash
   &&r.request_id===command.request_id&&saved.check_id===command.check_id&&saved.version===command.expected_version+1);
 }
 return saved;
}

export async function verifyRequirementView(view,{account,row,catalog}){
 need(view?.schema==='report-source-requirement-review-view-v1'&&view.draft_id===row.id
  &&view.actor_id===account.user_id&&view.workspace_id===account.workspace_id
  &&view.can_apply===false&&view.can_publish===false&&view.semantic_completeness_verified===false&&view.all_source_units_reviewed===false);
 const candidate=row.review_value?.result??row.model_result??null;
 const kind=row.review_value?'manual':candidate?'model':'not_generated';
 const binding={draft_revision:row.revision,source_hash:catalog.descriptor.source_hash,
  catalog_hash:catalog.descriptor.catalog_hash,candidate_kind:kind,
  candidate_hash:candidate===null?null:await digest(candidate),
  effective_value_hash:candidate===null?null:await digest(row.review_value??candidate)};
 need(same(view.binding,binding)&&view.candidate_available===(candidate!==null));
 need(Array.isArray(view.source_units)&&view.source_total===catalog.descriptor.ordered_source_refs.length);
 for(const unit of view.source_units){
  need(catalog.descriptor.ordered_source_refs.includes(unit.source_unit_id));
  const text=Object.values(catalog.index).filter(v=>v.source_ref===unit.source_unit_id).map(v=>v.text).join('');
  need(unit.preview===Array.from(text).slice(0,100).join('')&&unit.empty===!text&&HASH.test(unit.unit_hash));
 }
 const selected=view.selected_unit;
 if(selected){
  need(catalog.descriptor.ordered_source_refs.includes(selected.source_unit_id));
  const original=Object.entries(catalog.index).filter(([,v])=>v.source_ref===selected.source_unit_id);
  const text=original.map(([,v])=>v.text).join('');
  const spans=original.map(([span_ref,v])=>({span_ref,start:v.start,end:v.end,text_hash:v.text_hash}));
  const base={schema:'report-source-unit-v1',source_unit_id:selected.source_unit_id,
   source_hash:binding.source_hash,catalog_hash:binding.catalog_hash,spans,
   codepoints:Array.from(text).length,is_empty:!text,text_hash:await textHash(text)};
  need(selected.text===text&&Object.entries(base).every(([k,v])=>same(selected[k],v))&&await digest(base)===selected.unit_hash);
 }
 need(Array.isArray(view.target_options)&&Array.isArray(view.checks));
 for(const option of view.target_options)need(candidate&&await digest(targetValue(candidate,option.pointer))===option.value_hash&&typeof option.label==='string');
 for(const entry of view.checks)await verifyRequirementSaved(entry,account,row.id);
 return view;
}

export class SourceRequirementSession{
 constructor({account,draftID,storage=globalThis.localStorage,request,identity,check=()=>{},onChange=()=>{}}){
  need(UUID.test(account?.user_id)&&UUID.test(account?.workspace_id)&&UUID.test(draftID));
  Object.assign(this,{account:copy(account),draftID,storage,request,identity,check,onChange});
  this.scope=scopeOf(account,draftID);this.prefix='ga-source-requirements-v1:'+this.scope+':';
  this.owner=crypto.randomUUID();this.key=this.prefix+this.owner;this.active=true;this.localSaved=true;this.localError='';
  this.state={schema:'source-requirement-local-draft-v1',scope:this.scope,owner:this.owner,generation:0,
   form:null,binding:null,checkID:null,version:0,dirty:false,pending:null};
 }
 current(){need(this.active,'页面或账号已变化，原核对草稿保留。');this.check();}
 persist(){
  try{const raw=JSON.stringify(this.state);need(raw.length<=128*1024);this.storage.setItem(this.key,raw);
   need(this.storage.getItem(this.key)===raw);this.localSaved=true;this.localError='';return true;
  }catch{this.localSaved=false;this.localError='本机草稿暂时无法保存，请保留当前页面和输入。';return false;}
 }
 change(value){this.current();this.state={...this.state,...value};this.persist();this.onChange();}
 edit(change){need(this.state.form,'请先选择原文并开始记录要求。');this.change({form:{...this.state.form,...copy(change)},dirty:true,generation:this.state.generation+1});}
 begin(view){
  this.current();need(!this.state.dirty&&!this.state.pending,'当前输入已保留，请先保存或恢复原保存结果。');
  need(view.selected_unit,'这份资料没有可核对的原文位置。');
  this.change({form:{schema:'report-source-requirement-check-v1',requirement:'',
   source_unit_ids:[view.selected_unit.source_unit_id],disposition:'needs_evidence',reason:'',targets:[],
   candidate_hash:view.binding.candidate_hash,source_hash:view.binding.source_hash,catalog_hash:view.binding.catalog_hash},
   binding:copy(view.binding),checkID:crypto.randomUUID(),version:0,dirty:true,lastReceipt:null,generation:this.state.generation+1});
 }
 restoreCandidates(){
  const result=[];
  try{for(let i=0;i<this.storage.length;i++){
   const key=this.storage.key(i);if(!key?.startsWith(this.prefix)||key===this.key)continue;
   const raw=this.storage.getItem(key);if(!raw||raw.length>128*1024)continue;
   const state=JSON.parse(raw);
   if(state.schema==='source-requirement-local-draft-v1'&&state.scope===this.scope&&UUID.test(state.owner)
    &&state.form&&(state.dirty||state.pending))result.push({key,state});
  }}catch{this.localError='部分本机草稿暂时无法读取；现有输入保留。';}
  return result;
 }
 restore(entry){
  this.current();need(!this.state.dirty&&!this.state.pending,'请先保存当前核对，避免覆盖输入。');
  need(entry.key.startsWith(this.prefix)&&entry.state.scope===this.scope);
  this.change({...copy(entry.state),owner:this.owner,generation:entry.state.generation});
 }
 openSaved(saved){
  need(!this.state.dirty&&!this.state.pending,'请先保存当前输入。');
  this.change({form:copy(saved.checked.record),binding:copy(saved.binding),checkID:saved.check_id,
   version:saved.version,dirty:false,pending:null,lastReceipt:copy(saved.receipt),generation:this.state.generation+1});
 }
 restoreHistorical(saved,latestVersion){
  need(!this.state.dirty&&!this.state.pending,'请先保存或暂存当前输入。');
  need(Number.isSafeInteger(latestVersion)&&latestVersion>=saved.version);
  this.change({form:copy(saved.checked.record),binding:copy(saved.binding),checkID:saved.check_id,
   version:latestVersion,dirty:true,pending:null,lastReceipt:null,generation:this.state.generation+1});
 }
 keepOwnWithLatest(saved){
  need(this.state.form&&!this.state.pending&&saved.check_id===this.state.checkID
   &&Number.isSafeInteger(saved.version)&&saved.version>=this.state.version);
  this.change({version:saved.version,dirty:true,generation:this.state.generation+1});
 }
 stash(){
  this.current();need(this.persist(),this.localError);
  this.owner=crypto.randomUUID();this.key=this.prefix+this.owner;
  this.state={schema:'source-requirement-local-draft-v1',scope:this.scope,owner:this.owner,generation:0,
   form:null,binding:null,checkID:null,version:0,dirty:false,pending:null};
  this.localSaved=true;this.onChange();
 }
 recheck(view){
  need(this.state.form&&!this.state.pending,'请先核对原保存结果。');
  this.change({form:{...this.state.form,source_hash:view.binding.source_hash,catalog_hash:view.binding.catalog_hash,
   candidate_hash:view.binding.candidate_hash},binding:copy(view.binding),dirty:true,generation:this.state.generation+1});
 }
 async received(reply,pending){
  this.current();need(reply&&typeof reply.replayed==='boolean');
  await verifyRequirementSaved(reply.saved,this.account,this.draftID,pending);this.current();
  need(same(this.state.pending,pending),'原保存范围已变化，未采用迟到回执。');
  const unchanged=this.state.generation===pending.generation&&same(this.state.form,pending.body.value);
  this.change({pending:null,version:reply.saved.version,dirty:!unchanged,lastReceipt:copy(reply.saved.receipt)});
  return reply;
 }
 async send(pending,{probe=false}={}){
  this.current();need(this.persist(),this.localError);
  await this.identity();this.current();
  const base='/setup/report-evidence-readings/'+this.draftID;
  try{
   const reply=await this.request(probe?base+'/requirement-receipts/'+pending.body.request_id:
    base+'/requirement-checks/'+pending.body.check_id,probe?{}:{method:'PUT',body:JSON.stringify(pending.body)});
   await this.identity();this.current();return await this.received(reply,pending);
  }catch(error){
   this.current();
   // An original PUT returning a definite rejection after the server's nonce
   // lock proves it was not accepted. GET 404 alone cannot prove that.
   if(!probe&&[409,422].includes(error.status)&&['draft_revision_conflict','requirement_version_conflict','requirement_check_invalid'].includes(error.detail?.code)){
    this.change({pending:null,dirty:true});
   }
   throw error;
  }
 }
 async save(){
  this.current();need(!this.state.pending,'原保存结果待核对，请先查回执或用原编号重试。');
  need(this.state.form&&this.state.form.requirement.trim()&&this.state.form.reason.trim(),'请填写业务要求和核对说明。');
  const form=this.state.form;
  need(Array.from(form.requirement).length<=2000&&Array.from(form.reason).length<=2000,'业务要求和核对说明分别最多填写 2000 字。');
  need(form.source_unit_ids.length>=1&&form.source_unit_ids.length<=20,'一项要求可关联 1 至 20 处原文。');
  need(form.targets.length<=24,'一项要求最多关联 24 处流程位置。');
  need(!['represented','incorrect'].includes(form.disposition)||form.targets.length,'请至少选择一处对应的流程位置。');
  need(form.disposition!=='out_of_scope'||!form.targets.length,'与业务流程无关时，请取消已选的流程位置。');
  need(this.state.binding,'请重新读取资料与流程。');
  const pending={generation:this.state.generation,binding:copy(this.state.binding),body:{
   schema:'report-source-requirement-save-v1',request_id:crypto.randomUUID(),check_id:this.state.checkID,
   expected_version:this.state.version,draft_revision:this.state.binding.draft_revision,value:copy(this.state.form)}};
  this.change({pending});need(this.localSaved,this.localError);return this.send(pending);
 }
 async recover(probe=true){this.current();need(this.state.pending,'没有待核对的保存请求。');return this.send(copy(this.state.pending),{probe});}
 canLeave(){return this.localSaved;}
 deactivate(){this.active=false;}
}
