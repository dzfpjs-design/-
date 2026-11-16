import {json as runtimeJSON} from './runtime-api.js';
import {deliveryScope,deliveryPrefix,deliveryBase,deliveryCanonical as canonical,deliveryCopy as copy,deliveryNeed as need,deliverySHA,checkedDeliveryContext,checkedDeliveryOriginal,makeDeliveryOriginal,checkedDeliveryResult,checkedDeliveryRecovery,deliveryCommandRecord,checkedDeliveryCommandRecord,blankDeliveryDraft,checkedDeliveryDraft,parseDeliveryLocal} from './data-delivery-v245-protocol.mjs';

export class DataDeliveryControllerV245{
 constructor({account,instanceId,observationId,entityId,draftChannel='observation',api=runtimeJSON,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,scopeAvailable=()=>true,onChange=()=>{}}){
  this.scope=deliveryScope(account,instanceId,observationId,entityId);this.prefix=deliveryPrefix(this.scope);this.base=deliveryBase(this.scope);need(/^[a-z0-9-]{1,40}$/.test(draftChannel));
  Object.assign(this,{api,storage,session,locks,crypto,scopeAvailable,onChange,role:account.role});this.token=session.getItem('ga-setup-csrf');
  this.draftKey=this.prefix+'draft:'+draftChannel;this.draft=blankDeliveryDraft(this.scope);this.draftRaw=null;this.retainedDrafts=[];this.detachedDraftKey=null;
  this.active=true;this.suspended=false;this.epoch=0;this.started=false;this.loaded=false;this.authenticated=false;this.denied=false;this.busy=false;this.localSaved=false;this.pending=null;this.context=null;this.lastCommand=null;this.error='';this.notice='';
 }
 view(){return {scope_key:this.prefix,loaded:this.loaded,authenticated:this.authenticated,denied:this.denied,busy:this.busy,localSaved:this.localSaved,stageFailedBeforePost:!!this.stageFailedBeforePost,detachedDraft:!!this.detachedDraftKey,retainedDrafts:copy(this.retainedDrafts),draft:copy(this.draft),pending:this.pending?copy(this.pending):null,context:this.context?copy(this.context):null,lastCommand:this.lastCommand?copy(this.lastCommand):null,error:this.error,notice:this.notice};}
 emit(){if(this.active&&!this.suspended)this.onChange(this.view());}
 deny(){this.denied=true;this.authenticated=false;this.context=null;this.emit();}
 check(epoch=this.epoch){need(this.active&&!this.suspended&&!this.denied&&epoch===this.epoch&&this.scopeAvailable(),'账号、工作区或所选观测已变化；原说明和申请保留。');if(!this.token||this.session.getItem('ga-setup-csrf')!==this.token||this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id){this.deny();throw Error('登录范围已变化，原观测和收件申请已隐藏。');}}
 headers(){return {'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.token};}
 async identity(epoch=this.epoch){this.check(epoch);try{const actor=await this.api('/me',{cache:'no-store',headers:this.headers()});this.check(epoch);if(actor.user_id!==this.scope.actor_id||actor.workspace_id!==this.scope.workspace_id||actor.role!==this.role){this.deny();throw Error('当前实际账号、工作区或权限已变化，原申请保留。');}this.authenticated=true;return actor;}catch(e){if([401,403].includes(e.status))this.deny();throw e;}}
 async request(path,options={}){const epoch=this.epoch;await this.identity(epoch);const value=await this.api(path,{...options,cache:'no-store',headers:{...options.headers,...this.headers()}});this.check(epoch);await this.identity(epoch);return value;}
 write(key,value){this.check();const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机说明或原申请未完整读回；未丢弃原输入或另起申请。');return raw;}
 command(p){const raw=this.storage.getItem(this.prefix+'command:'+p.body.request_id);if(raw===null)return null;const value=checkedDeliveryCommandRecord(parseDeliveryLocal(raw),this.scope);need(canonical(value.original)===canonical(p));return value;}
 original(record=this.pending){need(record);const p=checkedDeliveryOriginal(record,this.scope),raw=JSON.stringify(p);need(this.storage.getItem(this.prefix+'original:'+p.body.request_id)===raw,'独立原件与原申请不一致，未重发或换号。');const pointer=this.storage.getItem(this.prefix+'pending');need(pointer===raw||this.command(p),'原申请等待记录尚不能核对，未重发、覆盖或换号。');return p;}
 async checkedOriginal(record=this.pending){const p=this.original(record);need(await deliverySHA(p.raw,this.crypto)===p.sha256,'原申请正文与摘要不同，原文保留，未重发。');this.check();return p;}
 readRetainedDrafts(){const rows=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix+'retained-draft:'))rows.push({key,draft:checkedDeliveryDraft(parseDeliveryLocal(this.storage.getItem(key)),this.scope)});}this.retainedDrafts=rows;}
 async restoreLocal({restoreDraft=true}={}){
  const pointer=this.storage.getItem(this.prefix+'pending');if(pointer!==null)this.pending=checkedDeliveryOriginal(parseDeliveryLocal(pointer),this.scope);
  const unresolved=[];let latest=null;
  for(let i=0;i<this.storage.length;i++){
   const key=this.storage.key(i);if(!key?.startsWith(this.prefix+'original:'))continue;
   const p=checkedDeliveryOriginal(parseDeliveryLocal(this.storage.getItem(key)),this.scope);need(await deliverySHA(p.raw,this.crypto)===p.sha256);
   const command=this.command(p);if(command)latest=command;else unresolved.push(p);
  }
  need(unresolved.length<=1&&(!this.pending||unresolved.every(p=>p.body.request_id===this.pending.body.request_id)),'还有不同原申请尚待核对，未另起重试。');
  if(!this.pending&&unresolved.length){this.pending=unresolved[0];this.write(this.prefix+'pending',this.pending);}
  if(this.pending){await this.checkedOriginal();this.lastCommand=this.command(this.pending);}
  else this.lastCommand=latest;
  this.readRetainedDrafts();if(restoreDraft){this.detachedDraftKey=null;this.draftRaw=this.storage.getItem(this.draftKey);this.draft=this.draftRaw===null?blankDeliveryDraft(this.scope):checkedDeliveryDraft(parseDeliveryLocal(this.draftRaw),this.scope);}
 }
 async start(){
  need(!this.busy,'正在读取原收件记录，请等待。');this.busy=true;this.emit();
  let restored=false;try{await this.identity();this.error='';if(this.stageFailedBeforePost)this.pending=null;await this.restoreLocal();restored=true;this.loaded=true;this.localSaved=true;this.stageFailedBeforePost=false;await this.readCurrent();}
  catch(e){this.error=e.message;if(!restored)this.localSaved=false;}finally{this.busy=false;this.emit();}
 }
 saveDraft(){this.check();checkedDeliveryDraft(this.draft,this.scope);need(this.storage.getItem(this.draftKey)===this.draftRaw,'另一页面已修改这份说明，当前输入保留，未覆盖其他人稿。');this.draftRaw=this.write(this.draftKey,this.draft);this.localSaved=true;}
 edit(patch){
  try{this.check();need(this.loaded&&!this.busy&&!this.pending&&!this.detachedDraftKey);if('reason'in patch)this.draft={...this.draft,reason:patch.reason,consent:false,confirmation_hash:null};else if('consent'in patch){need(!patch.consent||this.context?.state==='blocked'&&this.context.can_retry);this.draft={...this.draft,consent:patch.consent,confirmation_hash:patch.consent?this.context.context_hash:null};}this.saveDraft();this.error='';}
  catch(e){this.localSaved=false;this.error=e.message;}finally{this.emit();}
 }
 async saveAgain(){return this.action(async()=>{await this.identity();this.saveDraft();this.notice='当前说明已在本机完整读回；提交仍须核对现有状态并明确确认。';});}
 retainDraft(){return this.action(()=>this.lock(async()=>{await this.identity();need(this.loaded);const draft=checkedDeliveryDraft({...copy(this.draft),consent:false,confirmation_hash:null},this.scope),key=this.prefix+'retained-draft:'+this.crypto.randomUUID();this.write(key,draft);this.draft=draft;this.detachedDraftKey=key;this.localSaved=true;this.readRetainedDrafts();this.notice='当前说明已另存为独立副本，可以返回观测；没有覆盖其他窗口的说明，没有新发收件申请。';}));}
 useRetainedDraft(key){return this.action(()=>this.lock(async()=>{await this.identity();need(this.loaded&&!this.pending&&typeof key==='string'&&key.startsWith(this.prefix+'retained-draft:'));const chosen=checkedDeliveryDraft(parseDeliveryLocal(this.storage.getItem(key)),this.scope),currentRaw=this.storage.getItem(this.draftKey),current=currentRaw===null?null:checkedDeliveryDraft(parseDeliveryLocal(currentRaw),this.scope);if(current)this.write(this.prefix+'retained-draft:'+this.crypto.randomUUID(),{...copy(current),consent:false,confirmation_hash:null});const draft={...copy(chosen),consent:false,confirmation_hash:null},backup=this.prefix+'retained-draft:'+this.crypto.randomUUID();this.write(backup,draft);this.draft=draft;this.draftRaw=currentRaw;this.detachedDraftKey=backup;try{this.saveDraft();this.detachedDraftKey=null;}catch(e){this.localSaved=true;throw e;}finally{this.readRetainedDrafts();}this.notice='已将保留的说明作为当前草稿，原有说明另存副本；需要重新核对当前收件并明确确认后才能提交。';}));}
 async readCurrent(value){
  this.context=null;this.emit();const current=await checkedDeliveryContext(value===undefined?await this.request(this.base):value,this.scope,this.crypto);this.check();this.context=current;
  if(!this.pending&&this.draft.consent&&this.draft.confirmation_hash!==current.context_hash){this.draft={...this.draft,consent:false,confirmation_hash:null};try{this.saveDraft();}catch(e){this.localSaved=false;throw e;}}
  this.emit();return current;
 }
 async action(fn){this.check();need(!this.busy,'原收件申请正在核对，请等待。');this.busy=true;this.error='';this.notice='';this.emit();try{return await fn();}catch(e){if(this.active&&!this.suspended&&!this.denied)this.error=e.message;throw e;}finally{this.busy=false;this.emit();}}
 async lock(fn){need(this.locks?.request,'浏览器不能保护多窗口原申请；说明保留，暂不提交。');return this.locks.request(this.prefix+'command-lock',{mode:'exclusive'},async()=>{this.check();return fn();});}
 refresh(){return this.action(()=>this.readCurrent());}
 remember(p,result){const record=deliveryCommandRecord(p,result);this.write(this.prefix+'command:'+p.body.request_id,record);this.lastCommand=record;this.emit();return record;}
 async settle(p){
  await this.checkedOriginal(p);this.draft={...this.draft,consent:false,confirmation_hash:null};try{this.saveDraft();}catch(e){this.localSaved=false;throw e;}
  const pointer=this.storage.getItem(this.prefix+'pending');if(pointer===JSON.stringify(p)){this.storage.removeItem(this.prefix+'pending');need(this.storage.getItem(this.prefix+'pending')===null,'服务器原申请已保存，本机等待记录尚未结清；请继续核对原号。');}
  this.pending=null;const other=this.storage.getItem(this.prefix+'pending');if(other!==null){this.pending=checkedDeliveryOriginal(parseDeliveryLocal(other),this.scope);await this.checkedOriginal();}
 }
 async lookup(p){
  await this.checkedOriginal(p);this.context=null;this.emit();const recovery=checkedDeliveryRecovery(await this.request(this.base+'/requests/'+p.body.request_id+'?expected_sha256='+p.sha256),p);
  if(recovery.found)this.remember(p,recovery.result);else need(!this.command(p),'服务器暂未读到已保存的原申请，历史回执与原文保留，未另起重试。');
  const current=await this.readCurrent(recovery.current);
  if(recovery.found){await this.settle(p);this.notice=current.state==='received'?'原重试申请已保存，当前独立数据收件回执也已核对。':current.state==='blocked_integrity'?'原重试申请已保存；当前收件依据存在完整性问题，尚不能认定已接收。':'原重试申请已保存；数据管理仍需完成实际收件，申请保存不代表已接收。';}
  else this.notice='尚未查到原号申请；送达结果仍未知，原号、原文与说明保留，不据此另起申请。';
  this.emit();return {found:recovery.found,current};
 }
 async post(p){await this.checkedOriginal(p);this.context=null;this.emit();const result=checkedDeliveryResult(await this.request(this.base+'/retry',{method:'POST',body:p.raw}),p);this.remember(p,result);return this.lookup(p);}
 submit(){return this.action(()=>this.lock(async()=>{
  need(this.loaded&&this.localSaved&&!this.pending&&!this.detachedDraftKey&&['admin','operator'].includes(this.role));
  need(this.storage.getItem(this.draftKey)===this.draftRaw,'另一页面已修改这份说明，原输入保留，未采用另一份正文。');await this.restoreLocal({restoreDraft:false});need(!this.pending,'已有原申请尚待核对，未生成新的请求号。');
  need(this.draft.consent&&this.draft.reason.trim()&&this.context?.state==='blocked'&&this.context.can_retry&&this.draft.confirmation_hash===this.context.context_hash,'请填写原因并确认当前拒收记录；未自动扩大到采集或模型处理。');
  const expected=this.draft.confirmation_hash,current=await this.readCurrent();need(current.state==='blocked'&&current.can_retry&&current.context_hash===expected&&this.draft.consent,'原收件状态已变化，说明保留，请重新核对并明确确认。');
  const p=await makeDeliveryOriginal(this.scope,current,this.draft.reason,this.crypto.randomUUID(),this.crypto);this.check();need(this.storage.getItem(this.prefix+'pending')===null);
  try{this.write(this.prefix+'original:'+p.body.request_id,p);this.write(this.prefix+'pending',p);}catch(e){this.pending=p;this.localSaved=false;this.stageFailedBeforePost=true;throw e;}
  this.pending=p;this.lastCommand=null;this.emit();return this.post(p);
 }));}
 recover(retry=false){return this.action(()=>this.lock(async()=>{
  const p=await this.checkedOriginal(),result=await this.lookup(p);if(result.found||!retry)return result;
  need(['admin','operator'].includes(this.role)&&result.current.state==='blocked'&&result.current.can_retry&&result.current.context_hash===p.body.expected_context_hash,'当前不能重发原申请；原号与原文保留，请继续核对，不另换编号。');
  return this.post(p);
 }));}
 guard(){try{this.check();if(!this.loaded||!this.localSaved||this.busy)return false;if(this.detachedDraftKey){if(this.storage.getItem(this.detachedDraftKey)!==JSON.stringify(this.draft))return false;}else if(this.storage.getItem(this.draftKey)!==this.draftRaw)return false;if(this.pending)this.original();return true;}catch{return false;}}
 async prepareLeave(){this.check();need(this.authenticated&&this.guard(),'本机说明或原申请尚未完整保存，当前输入保留，请先重试保存。');if(this.pending)await this.checkedOriginal();return true;}
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.onChange=()=>{};}
}
