import {json as runtimeJSON} from './runtime-api.js';
import {needScope,needPrefix,needUUID,needHash,needAssert as need,needCopy as copy,needCanonical as canonical,needSHA,checkedNeedOptions,checkedNeedView,checkedNeedCopy,compatibleNeedCopy,blankNeedDraft,checkedNeedDraft,checkedNeedForm,hasNeedHumanWork,copyNeedDraft,needBody,makeNeedOriginal,checkedNeedOriginal,checkedNeedBody,needMatchesOriginal,parseNeedLocal} from './workspace-need-v246-protocol.mjs';
import {needRequestLookupPath,checkedNeedRequestRecovery,needRecoveryMatchesView} from './workspace-need-recovery-v248.mjs';
export class WorkspaceNeedControllerV246{
 constructor({account,instanceId,api=runtimeJSON,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,scopeAvailable=()=>true,onChange=()=>{}}){
  this.scope=needScope(account,instanceId);this.prefix=needPrefix(this.scope);this.base='/workspace/instances/'+instanceId+'/data-needs';this.legacyKey='ga-data-need:'+account.workspace_id+':'+account.username+':'+instanceId;
  Object.assign(this,{api,storage,session,locks,crypto,scopeAvailable,onChange,role:account.role});this.token=session.getItem('ga-setup-csrf');this.active=true;this.suspended=false;this.epoch=0;this.started=false;this.loaded=false;this.authenticated=false;this.denied=false;this.busy=false;this.localSaved=false;
  this.draft=blankNeedDraft(this.scope);this.draftRaw=null;this.detachedKey=null;this.history=[];this.pending=null;this.lastSaved=null;this.options=null;this.optionsCurrent=false;this.items=[];this.listCurrent=false;this.truncated=false;this.detail=null;this.copyIntent=null;this.legacy=null;this.notFound=false;this.error='';this.notice='';this.requestProof=null;
 }
 view(){return {scope_key:this.prefix,loaded:this.loaded,authenticated:this.authenticated,denied:this.denied,busy:this.busy,localSaved:this.localSaved,stageFailedBeforePost:!!this.stageFailedBeforePost,detached:!!this.detachedKey,draft:copy(this.draft),history:copy(this.history),pending:this.pending?copy(this.pending):null,lastSaved:this.lastSaved?copy(this.lastSaved):null,submitted:!!this.lastSaved&&this.lastSaved.draft_raw===JSON.stringify(this.draft),options:this.options?copy(this.options):null,optionsCurrent:this.optionsCurrent,items:copy(this.items),listCurrent:this.listCurrent,truncated:this.truncated,detail:this.detail?copy(this.detail):null,copyIntent:this.copyIntent?copy(this.copyIntent):null,legacy:this.legacy?copy(this.legacy):null,notFound:this.notFound,error:this.error,notice:this.notice,requestProof:this.requestProof?copy(this.requestProof):null};}
 emit(){if(this.active&&!this.suspended)this.onChange(this.view());}
 deny(){this.denied=true;this.authenticated=false;this.options=null;this.detail=null;this.items=[];this.emit();}
 check(epoch=this.epoch){need(this.active&&!this.suspended&&!this.denied&&epoch===this.epoch&&this.scopeAvailable(),'账号、工作区或实例已变化；原问题、草稿与申请保留。');if(!this.token||this.session.getItem('ga-setup-csrf')!==this.token||this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id){this.deny();throw Error('登录范围已变化，原资料需求已隐藏并保留。');}}
 headers(){return {'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.token};}
 async identity(epoch=this.epoch){this.check(epoch);try{const actor=await this.api('/me',{cache:'no-store',headers:this.headers()});this.check(epoch);if(actor.user_id!==this.scope.actor_id||actor.workspace_id!==this.scope.workspace_id||actor.role!==this.role){this.deny();throw Error('实际账号、工作区或权限已变化，原稿保留。');}this.authenticated=true;return actor;}catch(e){if([401,403].includes(e.status))this.deny();throw e;}}
 async request(path,options={}){const epoch=this.epoch;await this.identity(epoch);const value=await this.api(path,{...options,cache:'no-store',headers:{...options.headers,...this.headers()}});this.check(epoch);await this.identity(epoch);return value;}
 write(key,value){this.check();const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机原稿或原申请未完整读回；当前输入保留，未提交。');return raw;}
 saveDraft(){checkedNeedDraft(this.draft,this.scope);need(this.storage.getItem(this.prefix+'form')===this.draftRaw,'另一页面已修改新建表；当前输入保留，请先另存副本。');this.draftRaw=this.write(this.prefix+'form',this.draft);this.localSaved=true;this.detachedKey=null;}
 readHistory(){const rows=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix+'draft:'))rows.push({key,draft:checkedNeedDraft(parseNeedLocal(this.storage.getItem(key)),this.scope)});}this.history=rows;}
 archive(draft=this.draft){const key=this.prefix+'draft:'+this.crypto.randomUUID();this.write(key,checkedNeedDraft(copy(draft),this.scope));this.readHistory();return key;}
 receipt(p){const raw=this.storage.getItem(this.prefix+'received:'+p.body.request_id);if(raw===null)return null;const v=parseNeedLocal(raw);need(v&&Object.keys(v).sort().join('|')===['schema','scope','request_id','body_hash','query_id','context_hash','descriptor_hash','draft_raw'].sort().join('|')&&v.schema==='workspace-need-receipt-v246'&&canonical(v.scope)===canonical(this.scope)&&v.request_id===p.body.request_id&&v.body_hash===p.body_hash&&needUUID(v.query_id)&&needHash(v.context_hash)&&needHash(v.descriptor_hash)&&v.draft_raw===JSON.stringify(p.draft));return v;}
 original(p=this.pending){need(p);checkedNeedOriginal(p,this.scope);const raw=JSON.stringify(p);need(this.storage.getItem(this.prefix+'original:'+p.body.request_id)===raw,'原申请与独立原件不一致，未重发或换号。');need(this.storage.getItem(this.prefix+'pending')===raw||this.receipt(p),'原申请指针尚不能核对，原文保留。');return p;}
 async checkedOriginal(p=this.pending){p=this.original(p);need(await needSHA(p.raw,this.crypto)===p.sha256&&await needSHA(canonical(p.body),this.crypto)===p.body_hash,'原申请正文或摘要不同，未重发。');this.check();return p;}
 async restorePending(){
  const raw=this.storage.getItem(this.prefix+'pending');if(raw!==null)this.pending=checkedNeedOriginal(parseNeedLocal(raw),this.scope);const unresolved=[];
  for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(!key?.startsWith(this.prefix+'original:'))continue;const p=checkedNeedOriginal(parseNeedLocal(this.storage.getItem(key)),this.scope);need(await needSHA(p.raw,this.crypto)===p.sha256&&await needSHA(canonical(p.body),this.crypto)===p.body_hash);if(!this.receipt(p))unresolved.push(p);}
  need(unresolved.length<=1&&(!this.pending||unresolved.every(p=>p.body.request_id===this.pending.body.request_id)),'有不同原申请尚待核对，未生成新的请求号。');
  if(!this.pending&&unresolved.length){this.pending=unresolved[0];this.write(this.prefix+'pending',this.pending);}if(this.pending)await this.checkedOriginal();
  const last=this.storage.getItem(this.prefix+'last');if(last!==null){const p=checkedNeedOriginal(parseNeedLocal(last),this.scope);this.lastSaved=this.receipt(p);need(this.lastSaved);}
 }
 async readLegacy(){const raw=this.storage.getItem(this.legacyKey);if(raw===null)return;const sha=await needSHA(raw,this.crypto);this.check();const backup={schema:'workspace-need-legacy-backup-v246',scope:copy(this.scope),source_key:this.legacyKey,raw,sha256:sha};this.write(this.prefix+'legacy:'+sha,backup);let value;try{value=parseNeedLocal(raw);}catch{this.legacy={sha256:sha,readable:false,pending:true,purpose:'',body:null};return;}this.legacy={sha256:sha,readable:true,pending:!!value.pending,purpose:typeof value.form?.purpose==='string'?value.form.purpose:'',body:null};try{if(value.form){const mirror={...blankNeedDraft(this.scope),form:copy(checkedNeedForm(value.form)),release_id:value.pending?.release_id??null},key=this.prefix+'draft:legacy-'+sha;checkedNeedDraft(mirror,this.scope);const prior=this.storage.getItem(key);if(prior===null)this.write(key,mirror);else need(prior===JSON.stringify(mirror));this.readHistory();}if(value.pending)this.legacy.body=checkedNeedBody({source:'observations',confirmation:'confirmed',max_age_hours:24,...value.pending});}catch(e){this.legacy.readable=false;this.legacy.issue=e.message;}const settled=this.storage.getItem(this.prefix+'legacy-settled:'+sha);if(settled!==null){const proof=parseNeedLocal(settled);need(this.legacy.body&&proof.sha256===sha&&canonical(proof.scope)===canonical(this.scope)&&needUUID(proof.query_id)&&proof.request_id===this.legacy.body.request_id&&proof.body_hash===await needSHA(canonical(this.legacy.body),this.crypto));this.legacy.pending=false;}}
 async readOptions(){this.optionsCurrent=false;const value=checkedNeedOptions(await this.request(this.base+'/options'));this.options=value;this.optionsCurrent=true;if(this.draft.release_id===null&&!hasNeedHumanWork(this.draft))this.draft={...this.draft,release_id:value.release_id};this.emit();return value;}
 async readList(){this.listCurrent=false;const raw=await this.request(this.base);need(raw&&Array.isArray(raw.items)&&typeof raw.truncated==='boolean');const rows=[];for(const item of raw.items)rows.push(await checkedNeedView(item,this.scope,this.crypto));this.check();this.items=rows;this.truncated=raw.truncated;this.listCurrent=true;this.emit();return rows;}
 async start(){
  need(!this.busy);this.busy=true;this.emit();let restored=false;
  try{
   await this.identity();this.error='';this.draftRaw=this.storage.getItem(this.prefix+'form');
   this.draft=this.draftRaw===null?blankNeedDraft(this.scope):checkedNeedDraft(parseNeedLocal(this.draftRaw),this.scope);
   this.readHistory();if(this.stageFailedBeforePost)this.pending=null;await this.restorePending();await this.readLegacy();
   restored=true;this.localSaved=true;this.loaded=true;this.stageFailedBeforePost=false;
   const errors=[];
   try{if(this.pending)await this.lock(()=>this.lookupOriginalReadOnly());else if(this.lastSaved)await this.readLastSaved();}catch(error){errors.push(error.message);}
   if(this.error&&!errors.includes(this.error))errors.push(this.error);
   try{await this.readOptions();await this.readList();}catch(error){errors.push(error.message);}
   this.error=errors.join('；');
  }catch(e){this.error=e.message;if(!restored)this.localSaved=false;}
  finally{this.busy=false;this.emit();}
 }
 edit(patch){try{this.check();need(this.loaded&&!this.busy&&!this.pending&&!this.copyIntent&&!this.detachedKey);const wasBlank=!hasNeedHumanWork(this.draft);this.draft={...this.draft,form:{...this.draft.form,...patch}};if(this.draft.release_id===null&&wasBlank&&this.optionsCurrent)this.draft.release_id=this.options.release_id;this.saveDraft();this.error='';}catch(e){this.localSaved=false;this.error=e.message;}finally{this.emit();}}
 async action(fn){this.check();need(!this.busy,'正在核对原需求，请等待。');this.busy=true;this.error='';this.notice='';this.emit();try{return await fn();}catch(e){if(this.active&&!this.suspended&&!this.denied)this.error=e.message;throw e;}finally{this.busy=false;this.emit();}}
 lock(fn){need(this.locks?.request,'当前浏览器不能保护多窗口原稿；原输入保留，暂不提交。');return this.locks.request(this.prefix+'command-lock',{mode:'exclusive'},async()=>{this.check();return fn();});}
 saveAgain(){return this.action(async()=>{await this.identity();this.saveDraft();this.notice='当前新建表已在本机完整读回。';});}
 retainDraft(){return this.action(()=>this.lock(async()=>{await this.identity();need(this.loaded);this.detachedKey=this.archive();this.localSaved=true;this.notice='当前人稿已独立保留，可以离开；没有覆盖另一页，也没有提交新需求。';}));}
 restoreDraft(key){return this.action(()=>this.lock(async()=>{await this.identity();need(!this.pending&&!this.copyIntent&&typeof key==='string'&&key.startsWith(this.prefix+'draft:'));const saved=checkedNeedDraft(parseNeedLocal(this.storage.getItem(key)),this.scope),currentRaw=this.storage.getItem(this.prefix+'form');if(currentRaw!==null)this.archive(checkedNeedDraft(parseNeedLocal(currentRaw),this.scope));this.archive();this.draft=copy(saved);this.draftRaw=currentRaw;this.detachedKey=this.archive();try{this.saveDraft();}catch(e){this.localSaved=true;throw e;}this.notice='已恢复所选人稿；原稿与另一页面稿都另存保留。提交前仍须核对当前范围。';}));}
 newDraft(){return this.action(()=>this.lock(async()=>{await this.identity();need(!this.pending&&!this.copyIntent&&this.localSaved);this.archive();this.draft={...blankNeedDraft(this.scope),release_id:this.optionsCurrent?this.options.release_id:null};try{this.saveDraft();}catch(e){this.localSaved=false;throw e;}this.notice='原稿已保留，现在是新的填写区；尚未提交。';}));}
 refresh(){return this.action(async()=>{await this.readOptions();await this.readList();if(this.detail)await this.selectValue(this.detail.id);});}
 async selectValue(id){const value=await checkedNeedView(await this.request(this.base+'/'+id),this.scope,this.crypto);this.check();need(value.id===id);this.detail=value;this.emit();return value;}
 select(id){return this.action(()=>this.selectValue(id));}
 previewCopy(id){return this.action(async()=>{need(!this.pending&&!this.detachedKey&&this.localSaved&&!this.legacy?.pending,'请先保存当前人稿并核对原申请，未用复制条件替换。');const options=await this.readOptions(),view=await this.selectValue(id),descriptor=await checkedNeedCopy(view.copy_descriptor,this.scope,this.crypto);this.copyIntent={descriptor,hasWork:hasNeedHumanWork(this.draft)};try{compatibleNeedCopy(descriptor,options);}finally{this.emit();}this.notice='以下只是原条件的复制预览；采用前仍会复核当前字段定义，不会自动提交。';});}
 cancelCopy(){this.check();need(!this.busy);this.copyIntent=null;this.emit();}
 applyCopy(choice){return this.action(()=>this.lock(async()=>{need(['use','save_only'].includes(choice)&&this.copyIntent&&!this.pending&&this.localSaved);const original=this.copyIntent.descriptor,options=await this.readOptions(),latest=await this.selectValue(original.source.query_id);need(latest.copy_descriptor.descriptor_hash===original.descriptor_hash,'原需求或当前定义已经变化，预览与当前人稿保留，请重新预览。');compatibleNeedCopy(original,options);const next=copyNeedDraft(original,this.scope,{options,draft:this.draft});if(choice==='save_only'){this.archive(next);this.copyIntent=null;this.notice='原条件已另存一份人稿，当前填写区保持原样，尚未提交。';return;}if(hasNeedHumanWork(this.draft))this.archive();this.draft=next;this.detachedKey=this.archive();try{this.saveDraft();}catch(e){this.localSaved=true;throw e;}this.copyIntent=null;this.notice='原条件已填入新建表，旧需求与原人稿保留；请核对后自行保存新需求。';}));}
 async lookupReceipt(p){
  const raw=this.storage.getItem(this.prefix+'lookup:'+p.body.request_id);if(raw===null)return null;
  const saved=parseNeedLocal(raw);
  need(saved&&Object.keys(saved).sort().join('|')===['schema','scope','raw_sha256','recovery'].sort().join('|')&&saved.schema==='workspace-need-lookup-v248'&&canonical(saved.scope)===canonical(this.scope)&&saved.raw_sha256===p.sha256,'原需求恢复回执与本机原申请不一致，原件保留。');
  const recovery=await checkedNeedRequestRecovery(saved.recovery,this.scope,p,this.crypto);this.check();need(recovery.found);return recovery;
 }
 async readOriginalValue(p,id,proof=null){
  const value=await checkedNeedView(await this.request(this.base+'/'+id),this.scope,this.crypto);this.check();need(value.id===id);needMatchesOriginal(value,p);
  if(proof)needRecoveryMatchesView(proof,value,p);return value;
 }
 async readLastSaved(){
  const p=checkedNeedOriginal(parseNeedLocal(this.storage.getItem(this.prefix+'last')),this.scope);await this.checkedOriginal(p);
  const receipt=this.receipt(p),recovery=await this.lookupReceipt(p),value=await this.readOriginalValue(p,receipt.query_id,recovery?.request);
  this.detail=value;this.requestProof=recovery?.request||null;this.emit();return value;
 }
 async accept(p,value,recovery=null){
  value=await checkedNeedView(value,this.scope,this.crypto);needMatchesOriginal(value,p);await this.checkedOriginal(p);
  if(recovery){recovery=await checkedNeedRequestRecovery(recovery,this.scope,p,this.crypto);need(recovery.found);needRecoveryMatchesView(recovery.request,value,p);this.check();this.write(this.prefix+'lookup:'+p.body.request_id,{schema:'workspace-need-lookup-v248',scope:copy(this.scope),raw_sha256:p.sha256,recovery});}
  const receipt={schema:'workspace-need-receipt-v246',scope:copy(this.scope),request_id:p.body.request_id,body_hash:p.body_hash,query_id:value.id,context_hash:value.copy_descriptor.source.context_hash,descriptor_hash:value.copy_descriptor.descriptor_hash,draft_raw:JSON.stringify(p.draft)};
  this.write(this.prefix+'received:'+p.body.request_id,receipt);this.write(this.prefix+'last',p);this.lastSaved=receipt;this.requestProof=recovery?.request||null;
  this.items=[value,...this.items.filter(x=>x.id!==value.id)];this.detail=value;
  const pointer=this.storage.getItem(this.prefix+'pending');if(pointer===JSON.stringify(p)){this.storage.removeItem(this.prefix+'pending');need(this.storage.getItem(this.prefix+'pending')===null,'需求已保存，本机等待尚未结清，请继续核对原号。');}
  this.pending=null;const other=this.storage.getItem(this.prefix+'pending');if(other!==null){this.pending=checkedNeedOriginal(parseNeedLocal(other),this.scope);await this.checkedOriginal();}
  this.notFound=false;this.notice='新需求与完整条件已在服务器保存；原问题与其他人稿没有改写。';
  try{this.detail=await this.readOriginalValue(p,value.id,this.requestProof);}catch(e){this.detail=value;this.error='需求已保存，原证据暂未读回：'+e.message;}
  this.emit();return value;
 }
 async post(p){await this.checkedOriginal(p);return this.accept(p,await this.request(this.base,{method:'POST',body:p.raw}));}
 create(){return this.action(()=>this.lock(async()=>{need(this.loaded&&this.localSaved&&!this.detachedKey&&!this.copyIntent&&!this.legacy?.pending&&['admin','operator'].includes(this.role)&&!(this.lastSaved?.draft_raw===JSON.stringify(this.draft)));need(this.storage.getItem(this.prefix+'form')===this.draftRaw,'另一页面修改了人稿，当前稿保留，未提交。');await this.restorePending();need(!this.pending,'原申请仍待核对，不另生成请求号。');const options=await this.readOptions();if(this.draft.origin){const origin=await checkedNeedCopy(this.draft.origin,this.scope,this.crypto);compatibleNeedCopy(origin,options);const latest=await this.selectValue(origin.source.query_id);need(latest.copy_descriptor.descriptor_hash===origin.descriptor_hash,'复制来源或当前定义已变化，原稿保留，请重新预览。');}const body=needBody(this.draft,options,this.crypto.randomUUID()),p=await makeNeedOriginal(this.scope,body,this.crypto,this.draft);this.check();try{this.write(this.prefix+'original:'+body.request_id,p);this.write(this.prefix+'pending',p);}catch(e){this.pending=p;this.localSaved=false;this.stageFailedBeforePost=true;throw e;}this.pending=p;this.emit();return this.post(p);}));}
 async lookupOriginalReadOnly(p=null){
  p=await this.checkedOriginal(p||this.pending);const known=this.receipt(p);
  if(known){const saved=await this.lookupReceipt(p);return this.accept(p,await this.readOriginalValue(p,known.query_id,saved?.request),saved);}
  const recovery=await checkedNeedRequestRecovery(await this.request(needRequestLookupPath(this.scope,p)),this.scope,p,this.crypto);this.check();
  if(!recovery.found){this.notFound=true;this.notice='此刻尚未找到上次需求的保存记录，结果仍未知。原条件继续保留，没有重新提交或创建另一份需求。';this.emit();return null;}
  const value=await this.readOriginalValue(p,recovery.request.query_id,recovery.request),accepted=await this.accept(p,value,recovery);
  this.notice=recovery.request.evidence_level==='typed_body_receipt'?'已核对上次提交的保存回执并找回原需求；恢复只读取已有记录，没有重新发起查询。':'已按原需求记录找回。旧版没有独立保存完整提交回执，本机原始输入仍保留；没有补造旧回执或重新查询。';
  this.emit();return accepted;
 }
 recover(retry=false){return this.action(()=>this.lock(async()=>{const p=await this.checkedOriginal(),found=await this.lookupOriginalReadOnly(p);if(found)return found;if(retry){need(['admin','operator'].includes(this.role));return this.post(p);}return null;}));}
 recoverLegacy(){return this.action(async()=>{need(this.legacy?.pending&&this.legacy.body);const body=this.legacy.body,bodyHash=await needSHA(canonical(body),this.crypto),items=await this.readList(),found=items.find(v=>v.copy_descriptor.source.request_id===body.request_id&&v.copy_descriptor.source.body_hash===bodyHash);need(found,'旧版原请求尚未在当前清单核对到；原缓存与独立副本保留，未重新签名或发出请求。');this.write(this.prefix+'legacy-settled:'+this.legacy.sha256,{scope:copy(this.scope),sha256:this.legacy.sha256,query_id:found.id,request_id:body.request_id,body_hash:bodyHash});this.legacy.pending=false;this.detail=await this.selectValue(found.id);this.notice='旧版原号与完整条件已由服务器记录证明保存，旧缓存原文仍保留，没有重新提交。';});}
 guard(){try{this.check();if(!this.loaded||!this.localSaved||this.busy)return false;if(this.detachedKey){if(this.storage.getItem(this.detachedKey)!==JSON.stringify(this.draft))return false;}else if(this.storage.getItem(this.prefix+'form')!==this.draftRaw)return false;if(this.pending)this.original();return true;}catch{return false;}}
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.onChange=()=>{};}
}
