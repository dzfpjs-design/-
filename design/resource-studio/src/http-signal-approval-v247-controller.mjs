import {json as runtimeJSON} from './runtime-api.js';
import {httpApprovalScope,httpApprovalPrefix,httpApprovalCanonical as canonical,httpApprovalCopy as copy,httpApprovalNeed as need,httpApprovalBase,httpApprovalUUID,parseHTTPApprovalLocal,blankHTTPApprovalDraft,checkedHTTPApprovalDraft,meaningfulHTTPApprovalDraft,checkedHTTPApprovalOptions,checkedHTTPApprovalRoutes,checkedHTTPApprovalOccurrence,checkedHTTPApprovalOccurrences,httpApprovalEnableBody,makeHTTPApprovalOriginal,checkedHTTPApprovalOriginal,checkedHTTPApprovalResult,checkedHTTPApprovalRecovery} from './http-signal-approval-v247-protocol.mjs';

export class HTTPSignalApprovalControllerV247{
 constructor({account,instanceId,releaseID,enabled=true,api=runtimeJSON,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,scopeAvailable=()=>true,onChange=()=>{}}){
  this.scope=httpApprovalScope(account,instanceId);
  this.prefix=httpApprovalPrefix(this.scope);
  Object.assign(this,{account,releaseID,enabled,api,storage,session,locks,crypto,scopeAvailable,onChange});
  this.role=account.role;
  this.token=session.getItem('ga-setup-csrf');
  this.active=true;this.suspended=false;this.epoch=0;this.started=false;
  this.loaded=false;this.authenticated=false;this.denied=false;this.busy=false;this.localSaved=false;
  this.base=httpApprovalBase(this.scope);
  this.error='';this.notice='';this.options=null;this.optionsCurrent=false;this.listing=null;
  this.draft=blankHTTPApprovalDraft(this.scope);this.draftRaw=null;this.detachedKey=null;this.history=[];
  this.pending=null;this.pendingNotFound=false;this.completed=[];this.occurrences=[];this.nextCursor=null;this.occurrencesCurrent=false;this.stageFailedBeforePost=false;
 }
 check(epoch=this.epoch){
  need(this.active&&!this.suspended&&!this.denied&&epoch===this.epoch&&this.scopeAvailable(),'账号、工作区或实例已变化；原授权、人稿及原申请仍保留。');
  if(!this.token||this.session.getItem('ga-setup-csrf')!==this.token||this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id){
   this.deny();throw Error('登录范围已变化；原授权与输入已隐藏并保留。');
  }
 }
 deny(){this.denied=true;this.authenticated=false;this.options=null;this.listing=null;this.occurrences=[];this.emit();}
 headers(){return {'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.token};}
 async identity(epoch=this.epoch){
  this.check(epoch);
  try{
   const actor=await this.api('/me',{cache:'no-store',headers:this.headers()});
   this.check(epoch);
   if(actor.user_id!==this.scope.actor_id||actor.workspace_id!==this.scope.workspace_id||actor.role!==this.role){this.deny();throw Error('实际账号、工作区或权限已变化，原授权输入已隐藏。');}
   this.authenticated=true;return actor;
  }catch(error){if([401,403].includes(error.status))this.deny();throw error;}
 }
 async request(path,options={}){
  const epoch=this.epoch;await this.identity(epoch);
  const result=await this.api(path,{...options,cache:'no-store',headers:{...options.headers,...this.headers()}});
  this.check(epoch);await this.identity(epoch);return result;
 }
 write(key,value,{expected,immutable=false}={}){
  this.check();const raw=typeof value==='string'?value:JSON.stringify(value),prior=this.storage.getItem(key);
  if(expected!==undefined)need(prior===expected,'另一页面已更新本机原稿，当前输入保留，未覆盖。');
  if(immutable&&prior!==null){need(prior===raw,'原申请已固定另一份正文，未覆盖或换号。');return raw;}
  this.storage.setItem(key,raw);
  need(this.storage.getItem(key)===raw,'本机原稿尚未完整读回，当前输入保留，未发送新授权。');
  return raw;
 }
 lock(callback){need(this.locks?.request,'当前浏览器暂不能保护多窗口原申请；输入保留，未提交新授权。');return this.locks.request(this.prefix+'command-lock',{mode:'exclusive'},async()=>{this.check();return callback();});}
 async action(callback){
  this.check();need(!this.busy,'正在核对原请求，请等待当前操作。');this.busy=true;this.error='';this.notice='';this.emit();
  try{return await callback();}
  catch(error){if(this.active&&!this.suspended&&!this.denied)this.error=error.message;throw error;}
  finally{this.busy=false;this.emit();}
 }
 emit(){if(this.active&&!this.suspended)this.onChange(this.view());}
 view(){return {scope_key:this.prefix,loaded:this.loaded,authenticated:this.authenticated,denied:this.denied,busy:this.busy,localSaved:this.localSaved,error:this.error,notice:this.notice,options:this.options?copy(this.options):null,optionsCurrent:this.optionsCurrent,listing:this.listing?copy(this.listing):null,draft:copy(this.draft),detached:!!this.detachedKey,history:copy(this.history),pending:this.pending?copy(this.pending):null,pendingNotFound:this.pendingNotFound,completed:copy(this.completed),occurrences:copy(this.occurrences),nextCursor:this.nextCursor,occurrencesCurrent:this.occurrencesCurrent,stageFailedBeforePost:this.stageFailedBeforePost};}
 saveDraft(){
  checkedHTTPApprovalDraft(this.draft,this.scope);
  this.draftRaw=this.write(this.prefix+'form',this.draft,{expected:this.draftRaw});
  this.detachedKey=null;this.localSaved=true;
 }
 readHistory(){
  const rows=[];
  for(let index=0;index<this.storage.length;index++){
   const key=this.storage.key(index);if(!key?.startsWith(this.prefix+'draft:'))continue;
   rows.push({key,draft:checkedHTTPApprovalDraft(parseHTTPApprovalLocal(this.storage.getItem(key)),this.scope)});
  }
  this.history=rows;
 }
 archive(draft=this.draft){const key=this.prefix+'draft:'+this.crypto.randomUUID();this.write(key,checkedHTTPApprovalDraft(copy(draft),this.scope),{immutable:true});this.readHistory();return key;}
 async savedResult(original){
  const raw=this.storage.getItem(this.prefix+'saved:'+original.body.request_id);if(raw===null)return null;
  const record=parseHTTPApprovalLocal(raw);
  need(record.schema==='http-signal-approval-saved-v247'&&canonical(record.scope)===canonical(this.scope)&&record.raw_sha256===original.raw_sha256);
  return checkedHTTPApprovalResult(record.result,this.scope,original,this.crypto);
 }
 async original(value=this.pending){
  need(value,'没有可核对的原请求，未生成新编号。');
  const original=await checkedHTTPApprovalOriginal(value,this.scope,this.crypto);this.check();
  need(this.storage.getItem(this.prefix+'original:'+original.body.request_id)===JSON.stringify(original),'本机原请求与独立原件不同，未重发或换号。');
  need(this.storage.getItem(this.prefix+'pending')===JSON.stringify(original)||await this.savedResult(original),'上次操作的恢复记录尚未核对，原条件保留。');
  this.check();return original;
 }
 async restorePending(){
  const pointer=this.storage.getItem(this.prefix+'pending');this.pending=pointer===null?null:await checkedHTTPApprovalOriginal(parseHTTPApprovalLocal(pointer),this.scope,this.crypto);
  const unresolved=[],completed=[];
  for(let index=0;index<this.storage.length;index++){
   const key=this.storage.key(index);if(!key?.startsWith(this.prefix+'original:'))continue;
   const original=await checkedHTTPApprovalOriginal(parseHTTPApprovalLocal(this.storage.getItem(key)),this.scope,this.crypto);this.check();
   need(key===this.prefix+'original:'+original.body.request_id);
   const saved=await this.savedResult(original);if(saved)completed.push(saved);else unresolved.push(original);
  }
  need(unresolved.length<=1&&(!this.pending||unresolved.every(row=>row.body.request_id===this.pending.body.request_id)),'本机有不同原申请待核对；原件保留，未另起授权。');
  if(!this.pending&&unresolved.length){this.pending=unresolved[0];this.write(this.prefix+'pending',this.pending);}
  if(this.pending)await this.original();this.completed=completed;
 }
 async readOptions(){
  this.optionsCurrent=false;const options=await checkedHTTPApprovalOptions(await this.request(this.base+'/options'),this.scope,this.crypto);this.check();this.options=options;this.optionsCurrent=true;
  if(this.draft.pins===null&&!meaningfulHTTPApprovalDraft(this.draft)){
   this.draft={...this.draft,pins:{instance_version:options.instance_version,release_id:options.release_id,options_hash:options.options_hash},form:{...this.draft.form,valid_for_hours:String(options.limits.valid_for_hours_default),max_tasks:String(options.limits.max_tasks_default)}};
   try{this.saveDraft();}catch(error){this.localSaved=false;this.error=error.message;}
  }
  this.emit();return options;
 }
 async readRoutes(){const value=checkedHTTPApprovalRoutes(await this.request(this.base),this.scope);this.listing=value;this.emit();return value;}
 mergeOccurrences(rows){const byID=new Map(this.occurrences.map(row=>[row.id,row]));for(const row of rows){const previous=byID.get(row.id);if(!previous||previous.version<=row.version)byID.set(row.id,row);}this.occurrences=[...byID.values()].sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at)||b.id.localeCompare(a.id));}
 async readHead(){
  this.occurrencesCurrent=false;const page=checkedHTTPApprovalOccurrences(await this.request(this.base+'/occurrences?limit=20'),this.scope);
  this.mergeOccurrences(page.items);this.nextCursor=page.next_cursor;this.occurrencesCurrent=true;this.emit();return page;
 }
 async refreshReads(){
  const results=await Promise.allSettled([this.readOptions(),this.readRoutes(),this.readHead()]);this.check();
  const failures=results.filter(result=>result.status==='rejected');if(failures.length)throw Error(failures.map(result=>result.reason.message).join('；'));
 }
 async start(){
  need(!this.busy);this.busy=true;this.emit();let restored=false;
  try{
   await this.identity();this.error='';this.draftRaw=this.storage.getItem(this.prefix+'form');
   this.draft=this.draftRaw===null?blankHTTPApprovalDraft(this.scope):checkedHTTPApprovalDraft(parseHTTPApprovalLocal(this.draftRaw),this.scope);
   this.readHistory();await this.restorePending();this.loaded=true;this.localSaved=true;this.stageFailedBeforePost=false;restored=true;
   if(this.draftRaw===null)this.saveDraft();await this.refreshReads();
  }catch(error){this.error=error.message;if(!restored)this.localSaved=false;}
  finally{this.busy=false;this.emit();}
 }
 edit(patch){
  try{
   this.check();need(this.loaded&&!this.busy&&!this.pending&&!this.detachedKey&&['admin','operator'].includes(this.role));
   need(this.enabled||Object.keys(patch).every(key=>['pause_reason','retry_reason'].includes(key)),'当前实例不能修改新启用范围；暂停或重新检查的理由仍可保存。');
   this.draft={...this.draft,form:{...this.draft.form,...patch,...(Object.keys(patch).length===1&&Object.hasOwn(patch,'confirmed')?{}:{confirmed:false})}};
   this.saveDraft();this.error='';this.notice='';
  }catch(error){this.localSaved=false;this.error=error.message;}
  finally{this.emit();}
 }
 saveAgain(){return this.action(async()=>{await this.identity();this.saveDraft();this.notice='当前授权人稿已在本机完整读回；尚未取得服务器启用回执。';});}
 retainDraft(){return this.action(()=>this.lock(async()=>{await this.identity();need(this.loaded);this.detachedKey=this.archive();this.localSaved=true;this.notice='当前人稿已独立保留，可以离开；未覆盖其他页面，也未提交新授权。';}));}
 restoreDraft(key){return this.action(()=>this.lock(async()=>{
  await this.identity();need(!this.pending&&typeof key==='string'&&key.startsWith(this.prefix+'draft:'));
  const saved=checkedHTTPApprovalDraft(parseHTTPApprovalLocal(this.storage.getItem(key)),this.scope),current=this.storage.getItem(this.prefix+'form');
  if(current!==null)this.archive(checkedHTTPApprovalDraft(parseHTTPApprovalLocal(current),this.scope));this.archive();
  this.draft={...copy(saved),form:{...copy(saved.form),confirmed:false}};this.draftRaw=current;this.detachedKey=this.archive();
  try{this.saveDraft();}catch(error){this.localSaved=true;throw error;}
  this.notice='已恢复独立人稿，两份原稿仍保留；采用当前目录并重新确认后才可提交。';
 }));}
 adoptOptions(){return this.action(async()=>{
  await this.identity();need(this.optionsCurrent&&this.options&&!this.pending&&this.localSaved&&!this.detachedKey);
  this.draft={...this.draft,pins:{instance_version:this.options.instance_version,release_id:this.options.release_id,options_hash:this.options.options_hash},form:{...this.draft.form,confirmed:false}};
  try{this.saveDraft();}catch(error){this.localSaved=false;throw error;}
  this.notice='已明确采用当前目录；原来源、业务和对象选择仍保留，请核对不可用项并重新确认。';
 });}
 refresh(){return this.action(()=>this.refreshReads());}
 loadMore(){return this.action(async()=>{need(this.nextCursor,'已经没有下一页发生记录。');const page=checkedHTTPApprovalOccurrences(await this.request(this.base+'/occurrences?limit=20&after='+this.nextCursor),this.scope);this.mergeOccurrences(page.items);this.nextCursor=page.next_cursor;this.emit();});}
 readOccurrence(id){return this.action(async()=>{need(httpApprovalUUID(id));const value=checkedHTTPApprovalOccurrence(await this.request(this.base+'/occurrences/'+id));need(value.id===id);this.mergeOccurrences([value]);return value;});}
 async stage(operation,targetId,body){
  await this.restorePending();need(!this.pending,'原请求仍待核对，不能另生成授权、暂停或恢复编号。');
  const original=await makeHTTPApprovalOriginal(this.scope,operation,targetId,body,this.crypto);this.check();
  try{this.write(this.prefix+'original:'+body.request_id,original,{immutable:true});this.write(this.prefix+'pending',original,{expected:null});}
  catch(error){this.pending=original;this.localSaved=false;this.stageFailedBeforePost=true;throw error;}
  this.pending=original;this.pendingNotFound=false;this.emit();return original;
 }
 path(original){return original.operation==='enable'?this.base+'/enable':original.operation==='pause'?this.base+'/'+original.target_id+'/pause':this.base+'/occurrences/'+original.target_id+'/retry';}
 async accept(original,result,current=null){
  result=await checkedHTTPApprovalResult(result,this.scope,original,this.crypto);await this.original(original);
  this.write(this.prefix+'saved:'+original.body.request_id,{schema:'http-signal-approval-saved-v247',scope:copy(this.scope),raw_sha256:original.raw_sha256,result});
  this.completed=[result,...this.completed.filter(row=>row.receipt.request_id!==original.body.request_id)];
  if(this.storage.getItem(this.prefix+'pending')===JSON.stringify(original)){
   this.storage.removeItem(this.prefix+'pending');need(this.storage.getItem(this.prefix+'pending')===null,'服务器已保存，但本机恢复记录尚未更新；请继续核对上次操作结果。');
  }
  this.pending=null;await this.restorePending();this.pendingNotFound=false;this.optionsCurrent=false;
  const route=current||result.route;
  if(this.listing)this.listing={...this.listing,instance_version:Math.max(this.listing.instance_version,result.instance_version),routes:[route,...this.listing.routes.filter(row=>row.id!==route.id)]};
  this.notice=original.operation==='enable'?'启用决定已保存；之后的新异常须等数据管理智能体接收完成，再建立正式审批草稿，没有自动审批或启动。':original.operation==='pause'?'暂停决定已保存；已有任务继续保留，后续草稿不再自动建立。':'重新检查这条异常的请求已保存；请继续查看数据接收及草稿状态，没有重新采集或调用模型。';
  try{await Promise.all([this.readRoutes(),this.readHead()]);}catch(error){this.error='原操作已保存，当前进度暂未全部读回：'+error.message;}
  this.emit();return result;
 }
 async post(original){await this.original(original);need(['admin','operator'].includes(this.role),'当前账号只读，原申请保留。');return this.accept(original,await this.request(this.path(original),{method:'POST',body:original.raw}));}
 enable(){return this.action(()=>this.lock(async()=>{
  need(this.loaded&&this.enabled&&this.localSaved&&!this.detachedKey&&['admin','operator'].includes(this.role));
  need(this.storage.getItem(this.prefix+'form')===this.draftRaw,'另一页面修改了人稿；原输入保留，未提交。');
  const options=await this.readOptions(),body=httpApprovalEnableBody(this.draft,options,this.releaseID,this.crypto.randomUUID());
  this.saveDraft();return this.post(await this.stage('enable',null,body));
 }));}
 pause(routeID){return this.action(()=>this.lock(async()=>{
  need(this.loaded&&this.localSaved&&!this.detachedKey&&['admin','operator'].includes(this.role));const listing=await this.readRoutes(),route=listing.routes.find(row=>row.id===routeID);
  need(route?.status==='active','原授权当前不再启用；保留暂停理由，请核对服务器状态。');
  const body={schema:'signal-approval-pause-v247',request_id:this.crypto.randomUUID(),expected_version:listing.instance_version,reason:this.draft.form.pause_reason};
  this.saveDraft();return this.post(await this.stage('pause',routeID,body));
 }));}
 retry(occurrenceID){return this.action(()=>this.lock(async()=>{
  need(this.loaded&&this.localSaved&&!this.detachedKey&&['admin','operator'].includes(this.role));
  const value=checkedHTTPApprovalOccurrence(await this.request(this.base+'/occurrences/'+occurrenceID));need(value.id===occurrenceID&&!value.task_id,'此条异常已有对应业务任务；请直接打开原任务。');
  this.mergeOccurrences([value]);
  const body={schema:'signal-approval-retry-v247',request_id:this.crypto.randomUUID(),expected_version:value.version,reason:this.draft.form.retry_reason};
  this.saveDraft();return this.post(await this.stage('retry',occurrenceID,body));
 }));}
 recover(allowPost=false){return this.action(()=>this.lock(async()=>{
  const original=await this.original(),reply=await checkedHTTPApprovalRecovery(await this.request(this.base+'/requests/'+original.body.request_id+'?raw_sha256='+original.raw_sha256),this.scope,original,this.crypto);
  if(reply.found)return this.accept(original,reply.result,reply.current);
  this.pendingNotFound=true;this.notice='此刻尚未查到上次操作结果，是否保存仍未知；原条件继续保留。只有你明确重试才会再次提交同一次操作。';this.emit();
  if(allowPost)return this.post(original);return null;
 }));}
 async openTask(occurrenceID,onViewTask){
  need(this.guard(),'当前人稿或原请求尚未可靠保留，未切换任务。');
  const epoch=this.epoch,value=await this.readOccurrence(occurrenceID);this.check(epoch);
  need(value.task_id&&typeof onViewTask==='function','此条异常尚未建立业务任务，请继续查看数据接收和保存进度。');
  const checkEntry=()=>{this.check(epoch);need(this.guard(),'切换期间新增输入尚未保存，原页面与原任务保留。');const current=this.occurrences.find(row=>row.id===occurrenceID);need(current?.task_id===value.task_id,'此条异常对应的任务已变化，未打开其他任务。');};
  checkEntry();await this.identity(epoch);checkEntry();await onViewTask(value.task_id,checkEntry);checkEntry();
 }
 guard(){
  try{
   this.check();if(!this.loaded||!this.localSaved||this.busy)return false;
   if(this.detachedKey){if(this.storage.getItem(this.detachedKey)!==JSON.stringify(this.draft))return false;}
   else if(this.storage.getItem(this.prefix+'form')!==this.draftRaw)return false;
   if(this.pending){const raw=JSON.stringify(this.pending);if(this.storage.getItem(this.prefix+'original:'+this.pending.body.request_id)!==raw||this.storage.getItem(this.prefix+'pending')!==raw)return false;}
   return true;
  }catch{return false;}
 }
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.onChange=()=>{};}
}
