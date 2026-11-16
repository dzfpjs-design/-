import {pixelCanonical as canonical,pixelCopy as copy,pixelUUID as uuid,pixelHash as hash,pixelNeed as need} from './pixel-change-v249-protocol.mjs';
import {PixelWorkspaceSessionV250} from './pixel-workspace-session-v250.mjs';
import {FormalContinuationJournalV345,formalScope} from './formal-continuation-journal-v345.mjs';
const plain=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const blank=()=>({execution_id:'',acknowledge_cross_version:false,reason:'确认原建议及当前资料的版本和后续办理范围。',inputs:{},selected_plan:''});
const validDraft=d=>plain(d)&&typeof d.execution_id==='string'&&typeof d.acknowledge_cross_version==='boolean'&&typeof d.reason==='string'&&plain(d.inputs)&&Object.values(d.inputs).every(x=>typeof x==='string')&&typeof d.selected_plan==='string';
const okContext=v=>plain(v)&&v.schema==='formal-continuation-context-v344'&&typeof v.available==='boolean'&&Number.isSafeInteger(v.task_version)&&hash(v.context_hash)&&(!v.available||Array.isArray(v.results));
const safeError=e=>String(e?.message||'操作未核对完成，原输入与申请保留。').replace(/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/gi,'对应记录').replace(/[0-9a-f]{64}/gi,'原校验记录').slice(0,400);
const okPlan=v=>plain(v)&&v.schema==='formal-continuation-view-v345'&&uuid(v.plan_id)&&Number.isSafeInteger(v.version)&&Array.isArray(v.steps)&&plain(v.permissions)&&typeof v.goal_completed==='boolean'&&(!v.goal_completed||Object.values(v.permissions).every(x=>x===false))&&v.acceptance_available===false&&v.device_actions===0&&v.cloud_calls===0;

export class FormalContinuationControllerV345{
 constructor({account,task,advice,request,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{},current=()=>true}){
  this.scope=formalScope(account,task,advice);Object.assign(this,{account,request,storage,session,locks,crypto,onChange,current});
  this.active=true;this.denied=false;this.epoch=0;this.busy=false;this.loaded=false;this.error='';this.notice='';this.context=null;this.contextError='';this.list=[];this.plan=null;this.pending=null;this.abandoning=false;this.draft=blank();this.saved=false;this.owner=null;this.otherDrafts=[];
  this.journal=new FormalContinuationJournalV345({scope:this.scope,storage,locks,crypto,check:()=>this.check()});this.base=this.journal.base;
  this.access=new PixelWorkspaceSessionV250({account,session,request,current:()=>this.active&&current(),onDenied:()=>{this.denied=true;this.context=null;this.plan=null;this.emit();}});
 }
 check(epoch=this.epoch){need(this.active&&!this.denied&&this.current()&&epoch===this.epoch,'页面或账号范围已变化，原输入和申请保留。');this.access.check();}
 emit(){if(this.active&&this.current())this.onChange(this.view());}
 view(){return {busy:this.busy,loaded:this.loaded,error:this.error,notice:this.notice,context:this.denied?null:copy(this.context),contextError:this.contextError,list:this.denied?[]:copy(this.list),plan:this.denied?null:copy(this.plan),pending:this.denied?null:copy(this.pending),abandoning:this.abandoning,draft:this.denied?null:copy(this.draft),saved:this.saved,otherDrafts:copy(this.otherDrafts),denied:this.denied};}
 _write(key,value){this.check();this.storage.setItem(key,value);need(this.storage.getItem(key)===value,'本机输入尚未完整保存，未提交。');}
 _draftKey(owner=this.owner){return this.journal.prefix+'draft:'+owner;}
 _checkedDraft(raw){let v;try{v=JSON.parse(raw);}catch{throw Error('本机输入记录损坏，原件保留。');}need(plain(v)&&v.schema==='formal-continuation-draft-v345'&&canonical(v.scope)===canonical(this.scope)&&uuid(v.owner)&&validDraft(v.draft));return v;}
 _saveDraft(){try{const row={schema:'formal-continuation-draft-v345',scope:this.scope,owner:this.owner,draft:this.draft};this._write(this._draftKey(),canonical(row));this.saved=true;return true;}catch(e){this.saved=false;this.error='本机输入未保存，请检查存储后重试；尚未提交。';return false;}}
 _draftInventory(){const values=[],seen=new Set([canonical(this.draft)]);let count=0;for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.journal.prefix+'draft:')){count++;const row=this._checkedDraft(this.storage.getItem(key));need(key===this._draftKey(row.owner));const text=canonical(row.draft),hasContent=!!Object.values(row.draft.inputs).find(v=>v.trim())||row.draft.reason!==blank().reason||!!row.draft.execution_id||row.draft.acknowledge_cross_version;if(row.owner!==this.owner&&hasContent&&!seen.has(text)){values.push({key,has_content:true});seen.add(text);}}}
  need(count<=1000,'本机历史草稿过多，请先核对。');this.otherDrafts=values;}
 _switchDraft(next){this.check();need(validDraft(next));const owner=this.crypto.randomUUID();need(uuid(owner)&&this.storage.getItem(this._draftKey(owner))===null,'本机草稿身份冲突，原输入保留。');
  const row={schema:'formal-continuation-draft-v345',scope:this.scope,owner,draft:copy(next)};this._write(this._draftKey(owner),canonical(row));
  const tab=this.journal.prefix+'tab-owner';this.session.setItem(tab,owner);need(this.session.getItem(tab)===owner,'本机草稿身份未保存。');
  this.owner=owner;this.draft=copy(next);this.saved=true;this._draftInventory();}
 _openDraft(){this.check();const tab=this.journal.prefix+'tab-owner',last=this.session.getItem(tab);let draft=blank();
  if(last!==null){need(uuid(last));const raw=this.storage.getItem(this._draftKey(last));need(raw!==null,'原本机输入记录缺失，未创建空白替代。');const row=this._checkedDraft(raw);need(row.owner===last);draft=row.draft;}
  this._switchDraft(draft);}
 update(patch){this.check();need(plain(patch));this.draft={...this.draft,...copy(patch)};need(validDraft(this.draft));this._saveDraft();this.emit();}
 updateInput(key,value){need(typeof key==='string'&&typeof value==='string');this.update({inputs:{...this.draft.inputs,[key]:value}});}
 restoreDraft(key){this.check();need(this.guard(),'请先完整保存当前输入。');need(this.otherDrafts.some(x=>x.key===key));const row=this._checkedDraft(this.storage.getItem(key));this._switchDraft(row.draft);this.emit();}
 repairDraft(){this.check();this._saveDraft();if(this.saved)this.error='';this.emit();}
 async selectPlan(identity){return this.run(async epoch=>{need(!this.pending,'请先核对原申请。');need(this.list.some(x=>x.plan_id===identity));this.draft={...this.draft,selected_plan:identity};need(this._saveDraft());await this._read(epoch);});}
 guard(){try{if(!this.saved||!this.owner)return false;const row=this._checkedDraft(this.storage.getItem(this._draftKey()));if(canonical(row.draft)!==canonical(this.draft))return false;
  if(!this.pending)return true;const raw=canonical(this.pending),original=this.storage.getItem(this.journal.prefix+'original:'+this.pending.body.request_id),pointer=this.storage.getItem(this.journal.prefix+'pending');return original===raw&&(pointer===null||pointer===raw);
 }catch{return false;}}
 async run(fn){if(this.busy)return;const epoch=++this.epoch;this.busy=true;this.error='';this.emit();try{await fn(epoch);}catch(e){if(this.active&&this.current()&&epoch===this.epoch){this.error=safeError(e);try{this.pending=await this.journal.pending();this.abandoning=!!(this.pending&&await this.journal.abandonment(this.pending));}catch{}}}finally{if(this.active&&this.current()&&epoch===this.epoch){this.busy=false;this.emit();}}}
 async _read(epoch=this.epoch){
  const list=await this.access.read(this.base);this.check(epoch);need(plain(list)&&Array.isArray(list.plans)&&typeof list.task_completed==='boolean'&&list.cloud_calls===0&&list.device_actions===0&&list.plans.every(x=>uuid(x.plan_id)&&Number.isSafeInteger(x.version)&&typeof x.phase==='string'));
  this.list=list.plans;let selected=this.draft.selected_plan;
  if(!this.list.some(x=>x.plan_id===selected))selected=this.list.find(x=>['draft','in_progress','awaiting_acceptance'].includes(x.phase))?.plan_id||'';
  this.plan=null;if(selected){const plan=await this.access.read(this.base+'/'+selected);this.check(epoch);need(okPlan(plan)&&plan.plan_id===selected&&plan.goal_completed===list.task_completed);this.plan=plan;}
  if(this.draft.selected_plan!==selected){this.draft={...this.draft,selected_plan:selected};need(this._saveDraft());}
  this.context=null;this.contextError='';
  try{const context=await this.access.read(this.base+'/context');this.check(epoch);need(okContext(context),'原流程资料未能完整核对。');this.context=context;}
  catch(e){this.check(epoch);if(this.denied||[401,403].includes(e?.status))throw e;this.contextError='当前资料暂不能核对；已保存的续段和输入仍可查看。';}
 }

 async _lookup(p,epoch=this.epoch){
  const result=await this.access.read(this.base+'/operations/'+encodeURIComponent(p.body.request_id));this.check(epoch);
  need(plain(result)&&typeof result.found==='boolean'&&result.not_found_proves_unexecuted===false);
  if(!result.found){this.notice='尚未查到原申请，结果仍待核对；原编号和输入已保留。';return false;}
  const receipt=await this.journal.settle(p,result);this.check(epoch);this.pending=null;this.abandoning=false;
  if(receipt.abandoned){this.notice='原申请已安全终止，原编号不能再执行；输入仍保留。';return true;}
  this.draft={...this.draft,selected_plan:receipt.plan_id};need(this._saveDraft());this.notice='原申请与保存回执已核对。';return true;
 }
 async open(){return this.run(async epoch=>{this._openDraft();this.pending=await this.journal.pending();this.check(epoch);this.abandoning=!!(this.pending&&await this.journal.abandonment(this.pending));if(this.pending)await this._lookup(this.pending,epoch);await this._read(epoch);this.loaded=true;});}
 async refresh(){return this.run(async epoch=>{this.pending=await this.journal.pending();this.check(epoch);this.abandoning=!!(this.pending&&await this.journal.abandonment(this.pending));if(this.pending)await this._lookup(this.pending,epoch);await this._read(epoch);this.loaded=true;});}
 async _send(p,epoch){
  await this.journal.lock(async()=>{const current=await this.journal.pending();this.check(epoch);need(current&&canonical(current)===canonical(p),'原申请已变化，请先核对。');need(!await this.journal.abandonment(p),'已登记终止意向，不能重发原操作。');await this.access.identity();this.check(epoch);
   await this.access.read(p.path,{method:'POST',body:p.raw_body,headers:{'Content-Type':'application/json'}});this.check(epoch);});
  return this._lookup(p,epoch);
 }
 async repairOriginal(){return this.run(async epoch=>{const raw=this.storage.getItem(this.journal.prefix+'pending');const p=raw===null?await this.journal.pending():await this.journal.checked(raw);
  this.check(epoch);need(p,'没有可修复的本机原申请。');await this.journal.repair(p);this.check(epoch);this.pending=p;this.abandoning=!!await this.journal.abandonment(p);
  this.notice='本机原申请已完整读回，正在核对原编号。';await this._lookup(p,epoch);await this._read(epoch);this.loaded=true;
 });}
 async _sendAbandon(p,epoch){
  await this.journal.lock(async()=>{const current=await this.journal.pending();this.check(epoch);need(current&&canonical(current)===canonical(p),'原申请已变化，请先核对。');
   const intent=await this.journal.abandonment(p);need(intent,'终止申请的本机原件尚未保存。');await this.access.identity();this.check(epoch);
   await this.access.read(this.base+'/operations/'+encodeURIComponent(p.body.request_id)+'/abandon',{method:'POST',body:intent.raw,headers:{'Content-Type':'application/json'}});this.check(epoch);});
  return this._lookup(p,epoch);
 }
 async retryOriginal(){return this.run(async epoch=>{const p=await this.journal.pending();this.check(epoch);need(p,'没有待核对的原申请。');this.pending=p;if(await this._lookup(p,epoch)){await this._read(epoch);return;}
  need(['admin','operator'].includes(this.account.role),'当前账号无提交权限，原申请保留。');
  if(await this.journal.abandonment(p)){this.abandoning=true;await this._sendAbandon(p,epoch);}else await this._send(p,epoch);await this._read(epoch);});}
 async abandonOriginal(){return this.run(async epoch=>{const p=await this.journal.pending();this.check(epoch);need(p&&this.saved&&this.guard(),'请先保存输入并核对原申请。');this.pending=p;
  if(await this._lookup(p,epoch)){await this._read(epoch);return;}
  const reason=this.draft.inputs['abandon:'+p.body.request_id]||'';need(reason.trim(),'请填写终止原因。');
  await this.journal.markAbandon(p,reason);this.check(epoch);this.abandoning=true;this.emit();
  try{await this._sendAbandon(p,epoch);}catch(e){this.check(epoch);if(!await this._lookup(p,epoch))throw e;}await this._read(epoch);
 });}
 async create(){return this.run(async epoch=>{need(this.saved&&this.guard()&&!this.pending,'请先保存输入并核对原申请。');await this._read(epoch);const ctx=this.context;
  need(ctx.available&&ctx.all_results_reviewed&&['admin','operator'].includes(this.account.role));const selected=ctx.results.find(x=>x.execution_id===this.draft.execution_id&&x.current_data&&x.reviewed);need(selected,'请选择已复核且资料版本有效的办理结果。');
  need(!ctx.different_generation||this.draft.acknowledge_cross_version,'请先确认原建议和当前资料来自不同版本。');
  const request_id=this.crypto.randomUUID(),body={request_id,expected_task_version:ctx.task_version,execution_id:selected.execution_id,context_hash:ctx.context_hash,
   acknowledge_cross_version:this.draft.acknowledge_cross_version,reason:this.draft.reason};
  const preview=await this.access.read(this.base+'/preview',{method:'POST',body:canonical(body),headers:{'Content-Type':'application/json'}});this.check(epoch);
  need(preview?.schema==='formal-continuation-preview-v345'&&canonical(preview.request)===canonical(body)&&hash(preview.preview_hash),'预览依据未能核对。');
  const p=await this.journal.stage('create',null,{...body,preview_hash:preview.preview_hash});this.check(epoch);this.pending=p;this.emit();await this._send(p,epoch);await this._read(epoch);
 });}
 async command(operation,stepKey=null){return this.run(async epoch=>{need(this.saved&&this.guard()&&!this.pending,'请先保存输入并核对原申请。');await this._read(epoch);const plan=this.plan;need(okPlan(plan));
  if(operation!=='withdraw')need(this.context?.available,'当前资料尚不能核对，不能继续办理。');
  const perm={activate:'can_activate',withdraw:'can_withdraw',complete_step:'can_complete_step',return_step:'can_return_step'}[operation];need(perm&&plan.permissions[perm]===true,'当前步骤或权限已变化，请重新核对。');
  const reason={activate:'确认依据并开始人工续段。',withdraw:'撤回续段，保留已保存的步骤和原依据。',complete_step:'提交当前责任步骤的人工结果。',return_step:'退回步骤并保留原意见。'}[operation];
  const body={request_id:this.crypto.randomUUID(),expected_version:plan.version,reason};
  if(['complete_step','return_step'].includes(operation)){need(typeof stepKey==='string'&&stepKey);body.step_key=stepKey;}
  if(operation==='return_step'){body.reason=this.draft.inputs[plan.plan_id+':return:'+stepKey]||'';need(body.reason.trim(),'请填写退回原因。');}
  if(operation==='withdraw'){body.reason=this.draft.inputs[plan.plan_id+':withdraw']||'';need(body.reason.trim(),'请填写撤回原因。');}
  if(operation==='complete_step'){const key=plan.plan_id+':'+stepKey;body.result=this.draft.inputs[key]||'';need(body.result.trim(),'请填写本步骤的处理结果。');}
  const p=await this.journal.stage(operation,plan.plan_id,body);this.check(epoch);this.pending=p;this.emit();await this._send(p,epoch);await this._read(epoch);
 });}
 dispose(){this.active=false;this.epoch++;this.onChange=()=>{};}
}
