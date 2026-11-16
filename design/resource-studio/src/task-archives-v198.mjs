// Local drafts and exact original requests are isolated by actor/workspace/Task.
// Authenticated server inventories establish source integrity, never this UI.
export const ARCHIVES_BASE='/task-archives';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,HASH=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const need=(ok,message='档案或原保存记录无法核对；本页输入与原请求仍保留。')=>{if(!ok)throw Error(message);};
const copy=v=>JSON.parse(JSON.stringify(v));
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const fields=['record_type','title','summary'];
const metadata=value=>Object.fromEntries(fields.map(key=>[key,value[key]]));
const flags=value=>value?.proposal_only===true&&value.human_confirmed===false&&value.formal_archive===false&&value.entity_state_updated===false;
const integer=v=>Number.isSafeInteger(v)&&v>=1;
export function taskArchiveScope(scope){need(exact(scope,['actor_id','workspace_id','task_id'])&&Object.values(scope).every(v=>UUID.test(v)));return {actor_id:scope.actor_id,workspace_id:scope.workspace_id,task_id:scope.task_id};}
export const taskArchiveScopeKey=scope=>Object.values(taskArchiveScope(scope)).join(':');
function formValues(value){need(object(value)&&fields.every((key,i)=>typeof value[key]==='string'&&Array.from(value[key]).length<=[80,300,8000][i]));return metadata(value);}
function inputValues(value){const result=formValues(value);need(fields.every(key=>/\S/u.test(result[key])),'请填写档案类型、标题和候选说明。');return result;}
function receipt(value,scope,pending){need(exact(value,['schema','archive_id','version','version_hash','task_id','status','simulation_only','proposal_only','human_confirmed','formal_archive','entity_state_updated'])&&value.schema==='workflow-task-archive-receipt-v198'&&UUID.test(value.archive_id)&&value.task_id===scope.task_id&&integer(value.version)&&HASH.test(value.version_hash)&&value.status==='candidate'&&typeof value.simulation_only==='boolean'&&flags(value));if(pending)need(value.archive_id===pending.archive_id&&value.version===pending.body.expected_version+1);return value;}
export function checkedArchiveList(value,scope){need(exact(value,['schema','task_id','items','current_source_checked'])&&value.schema==='workflow-task-archive-list-v198'&&value.task_id===scope.task_id&&value.current_source_checked===false&&Array.isArray(value.items)&&value.items.length<=100);const ids=new Set();for(const row of value.items){need(object(row)&&UUID.test(row.id)&&!ids.has(row.id)&&row.task_id===scope.task_id&&integer(row.version)&&HASH.test(row.version_hash)&&row.status==='candidate'&&typeof row.simulation_only==='boolean'&&Number.isSafeInteger(row.issue_count)&&row.issue_count>=0&&flags(row));formValues(row);ids.add(row.id);}return value;}
export function checkedArchiveView(value,scope,id){
 need(object(value)&&value.schema==='workflow-task-archive-view-v198'&&value.id===id&&UUID.test(value.id)&&value.task_id===scope.task_id&&value.status==='candidate'&&integer(value.version)&&HASH.test(value.version_hash)&&HASH.test(value.value_hash)&&UUID.test(value.actor_id)&&typeof value.saved_at==='string'&&value.current_source_checked===false&&flags(value));
 const v=value.value;
 need(object(v)&&v.schema==='workflow-task-archive-value-v198'&&v.basis?.task_id===scope.task_id&&v.summary_verification==='unconfirmed_proposal'&&typeof v.simulation_only==='boolean'&&['author_self_review','engineering_simulation'].includes(v.review_mode)&&v.simulation_only===(v.review_mode==='engineering_simulation')&&flags(v)&&Array.isArray(v.sources)&&v.sources.length<=512&&Array.isArray(v.issues));formValues(v);
 for(const source of v.sources){
  need(object(source)&&['human_product','invocation','data_run','evidence'].includes(source.kind)&&typeof source.id==='string'&&typeof source.label==='string'&&HASH.test(source.source_hash)&&object(source.snapshot)&&source.snapshot.task_id===scope.task_id);
  const snap=source.snapshot;
  if(['human_product','invocation'].includes(source.kind))need(Object.hasOwn(snap,'output'));
  if(source.kind==='data_run')need(typeof snap.question==='string'&&object(snap.data));
  if(source.kind==='evidence')need(typeof snap.media_type==='string'&&Number.isSafeInteger(snap.size_bytes)&&snap.size_bytes>=0&&integer(snap.version));
 }
 for(const issue of v.issues){need(object(issue)&&typeof issue.code==='string');if(issue.assessment!==undefined)need(object(issue.assessment)&&typeof issue.assessment.reason==='string'&&typeof issue.assessment.assessment==='string'&&Array.isArray(issue.assessment.missing_information)&&issue.assessment.missing_information.every(item=>typeof item==='string'));}
 need(Array.isArray(value.history)&&value.history.length>=1&&value.history.every((row,index)=>object(row)&&row.version===index+1&&HASH.test(row.version_hash)&&HASH.test(row.value_hash)&&typeof row.saved_at==='string')&&value.history.some(row=>row.version===value.version&&row.version_hash===value.version_hash&&row.value_hash===value.value_hash));
 return value;
}

export class TaskArchivesController{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,crypto=globalThis.crypto,locks=globalThis.navigator?.locks,onChange=()=>{},onSaved=async()=>true}){
  Object.assign(this,{api,scope:taskArchiveScope(scope),storage,session,crypto,locks,onChange,onSaved});this.prefix='ga-task-archives-v198:'+taskArchiveScopeKey(scope)+':';this.shared=this.prefix+'outbox';this.pointer=this.prefix+'active-draft';this.state={schema:'task-archive-draft-v198',scope:this.scope,forms:{},selected:null,version:null};this.items=[];this.view=null;this.currentVersion=null;this.pending=null;this.busy=false;this.active=false;this.generation=0;this.identityStatus='checking';this.denied=false;this.localSaved=false;this.storageBlocked=false;this.pendingUnreadable=false;this.storageError='';this.error='';this.notice='';this.filter={record_type:'',q:''};
 }
 get readable(){return this.active&&!this.denied&&this.identityStatus==='verified';}
 get canEdit(){return this.readable&&['admin','operator'].includes(this.role);}
 get form(){return this.state.forms[this.state.selected]||null;}
 notify(){this.onChange();}
 mount(){this.active=true;this.generation++;this.busy=false;this.loading=true;this.identityStatus=this.denied?'denied':'checking';this.opening=null;}
 dispose(){this.active=false;this.generation++;this.opening=null;}
 check(token=this.generation){need(this.active&&token===this.generation&&!this.denied,'当前档案页已关闭或账号已变化，原记录保留。');}
 conceal(message){this.denied=true;this.identityStatus='denied';this.error=message;this.notify();}
 async identity(token=this.generation){this.check(token);try{const me=await this.api('/me');this.check(token);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||!['admin','operator','viewer'].includes(me?.role)){this.conceal('账号、工作区或权限已变化，原档案与输入已隐藏。');throw Error(this.error);}this.role=me.role;this.identityStatus='verified';this.notify();return me;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal('当前会话已失效，原档案与输入已隐藏。');throw error;}}
 async request(path,options={},token=this.generation){await this.identity(token);this.check(token);try{const result=await this.api(path,options);this.check(token);await this.identity(token);this.check(token);return result;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal('当前会话或档案权限已变化，原内容已隐藏。');throw error;}}
 write(key,value){const raw=typeof value==='string'?value:JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机保存后未能读回，暂不提交服务器。');}
 persist(){this.localSaved=false;try{this.check();need(this.journal);need(!this.pendingUnreadable);this.write(this.journal,this.state);this.localSaved=true;this.storageBlocked=false;this.storageError='';return true;}catch(error){this.storageBlocked=true;this.storageError='本机保存失败或原记录不能核对，输入仍在本页；请保留本页或导出输入，暂不能提交。';return false;}}
 guard(){if(!this.active||this.denied)return true;if(this.busy)return false;if(!Object.values(this.state.forms).some(form=>form.dirty))return true;return this.localSaved||this.persist();}
 async hash(raw){need(this.crypto?.subtle,'浏览器不能可靠核对原请求，输入保留，暂不能提交。');const bytes=await this.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));return [...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');}
 checkedPending(value){need(object(value)&&value.schema==='task-archive-outbox-v198'&&same(value.scope,this.scope)&&UUID.test(value.archive_id)&&UUID.test(value.body?.request_id)&&value.body.schema==='workflow-task-archive-revision-v198'&&exact(value.body,['schema','request_id','expected_version','source_version_hash',...fields])&&integer(value.body.expected_version)&&HASH.test(value.body.source_version_hash)&&typeof value.raw==='string'&&same(JSON.parse(value.raw),value.body)&&HASH.test(value.sha256));inputValues(value.body);if(value.not_found!==undefined)need(value.not_found===true);if(value.direct_result!==undefined)receipt(value.direct_result,this.scope,value);if(value.rejection!==undefined)need(object(value.rejection)&&value.rejection.code==='workflow_task_archive_version_conflict'&&value.rejection.not_applied===true&&value.rejection.request_key===value.body.request_id&&value.rejection.body_sha256===value.sha256);return value;}
 reloadPending(){const raw=this.storage.getItem(this.shared);this.pending=raw===null?null:this.checkedPending(JSON.parse(raw));return this.pending;}
 async open(){
  if(this.opening)return this.opening;
  const token=this.generation;
  this.opening=(async()=>{
   await this.identity(token);
   // A local storage fault must block writes, never authenticated historical GETs.
   try{
    if(!this.journal){
     let key=this.session.getItem(this.pointer);
     if(key)need(key.startsWith(this.prefix+'draft:'),'原档案输入范围不符，未覆盖。');
     else{need(this.crypto?.randomUUID);key=this.prefix+'draft:'+this.crypto.randomUUID();this.session.setItem(this.pointer,key);need(this.session.getItem(this.pointer)===key);}
     this.journal=key;const raw=this.storage.getItem(key);
     if(raw!==null){const data=JSON.parse(raw);need(object(data)&&data.schema==='task-archive-draft-v198'&&same(data.scope,this.scope)&&object(data.forms)&&(data.selected===null||UUID.test(data.selected))&&(data.version===null||integer(data.version)));for(const [id,form]of Object.entries(data.forms)){need(UUID.test(id)&&object(form)&&integer(form.version)&&HASH.test(form.version_hash)&&typeof form.dirty==='boolean');formValues(form.values);formValues(form.baseline);}this.state=data;}
    }
    this.reloadPending();
    if(this.pending){need(await this.hash(this.pending.raw)===this.pending.sha256);this.check(token);}
    this.pendingUnreadable=false;this.persist();
   }catch(error){this.check(token);this.pendingUnreadable=true;this.localSaved=false;this.storageBlocked=true;this.storageError='本机原保存记录暂不能核对，已保留原内容。可读取服务器档案，暂不能提交新修订。';}
   await this.refresh(token);
   const selected=this.state.selected||this.items[0]?.id;
   if(selected)await this.select(selected,this.state.version,token);
   this.check(token);return true;
  })().catch(error=>{if(this.active&&token===this.generation){this.opening=null;this.error=error.detail?.message||error.message;this.notify();}throw error;}).finally(()=>{if(this.active&&token===this.generation){this.loading=false;this.notify();}});
  return this.opening;
 }
 async operation(run){this.check();const token=this.generation;need(!this.busy,'正在处理档案，请稍候。');this.busy=true;this.error='';this.notify();try{return await run(token);}catch(error){if(this.active&&token===this.generation){this.error=error.detail?.message||error.message;this.notify();}throw error;}finally{if(this.active&&token===this.generation){this.busy=false;this.notify();}}}
 async refresh(token=this.generation){const params=new URLSearchParams({task_id:this.scope.task_id});if(this.filter.record_type)params.set('record_type',this.filter.record_type);if(this.filter.q)params.set('q',this.filter.q);const data=checkedArchiveList(await this.request(ARCHIVES_BASE+'?'+params,{},token),this.scope);this.items=copy(data.items);this.listLoaded=true;this.notify();return data;}
 async select(id,version=null,token=this.generation){need(UUID.test(id)&&(version===null||integer(version)));if(!this.localSaved)this.persist();const path=ARCHIVES_BASE+'/'+id+(version===null?'':'/versions/'+version);const view=checkedArchiveView(await this.request(path,{},token),this.scope,id);this.check(token);this.state.selected=id;this.state.version=version;this.view=view;const current=version===null;this.currentVersion=current?view.version:(this.items.find(row=>row.id===id)?.version||view.history.at(-1)?.version);if(current){const form=this.state.forms[id];if(!form||!form.dirty){this.state.forms[id]={version:view.version,version_hash:view.version_hash,baseline:metadata(view.value),values:metadata(view.value),dirty:false,conflict:false};}else form.conflict=form.version!==view.version||form.version_hash!==view.version_hash;}this.persist();this.notify();return view;}
 edit(change){this.check();need(this.canEdit&&!this.loading&&!this.busy&&!this.pending&&!this.pendingUnreadable&&this.state.version===null&&this.form,'当前只读、原请求待核对或正在处理，输入未提交。');need(Object.keys(change).every(key=>fields.includes(key)));this.form.values=formValues({...this.form.values,...change});this.form.dirty=!same(this.form.values,this.form.baseline);this.persist();this.notify();}
 retained(){this.check();const rows=[];for(let index=0;index<this.storage.length;index++){const key=this.storage.key(index);if(!key?.startsWith(this.prefix+'retained:'))continue;const row=JSON.parse(this.storage.getItem(key));need(same(row.scope,this.scope)&&UUID.test(row.archive_id)&&object(row.form));formValues(row.form.values);if(row.archive_id===this.state.selected)rows.push(copy(row.form));}return rows;}
 async useCurrent(){return this.operation(async token=>{need(this.canEdit&&!this.pending&&!this.pendingUnreadable);const id=this.state.selected,form=this.form;need(form);const view=checkedArchiveView(await this.request(ARCHIVES_BASE+'/'+id,{},token),this.scope,id);this.check(token);if(form.dirty)this.write(this.prefix+'retained:'+this.crypto.randomUUID(),{scope:this.scope,archive_id:id,form:copy(form)});this.state.forms[id]={version:view.version,version_hash:view.version_hash,baseline:metadata(view.value),values:metadata(view.value),dirty:false,conflict:false};this.view=view;this.currentVersion=view.version;this.state.version=null;this.persist();this.notice='先前输入已另行保留，当前显示服务器最新版。';this.notify();return view;});}
 async locked(run){need(this.locks?.request,'当前浏览器不能保证多窗口保存一致，输入保留，暂不能提交。');return this.locks.request(this.prefix+'save',{mode:'exclusive'},run);}
 async save(){return this.operation(token=>this.locked(async()=>{await this.identity(token);need(this.canEdit&&!this.pendingUnreadable&&!this.reloadPending()&&this.localSaved&&this.form?.dirty&&!this.form.conflict&&this.state.version===null,'请先核对当前版本并保护原输入。');const values=inputValues(this.form.values),body={schema:'workflow-task-archive-revision-v198',request_id:this.crypto.randomUUID(),expected_version:this.form.version,source_version_hash:this.form.version_hash,...values},raw=JSON.stringify(body);need(new TextEncoder().encode(raw).byteLength<=32768,'本次文字超过保存容量，请缩短文字；原输入仍保留。');const sha256=await this.hash(raw);this.check(token);const pending={schema:'task-archive-outbox-v198',scope:copy(this.scope),archive_id:this.state.selected,body,raw,sha256};this.write(this.prefix+'original:'+body.request_id,pending);this.write(this.shared,pending);this.pending=pending;need(this.persist(),this.storageError);return this.postOriginal(token);}));}
 async postOriginal(token=this.generation){const p=this.checkedPending(this.pending);need(await this.hash(p.raw)===p.sha256);this.check(token);await this.identity(token);need(this.canEdit);try{const result=receipt(await this.request(ARCHIVES_BASE+'/'+p.archive_id+'/revisions',{method:'POST',body:p.raw},token),this.scope,p);this.check(token);this.pending={...p,direct_result:copy(result)};this.write(this.shared,this.pending);return await this.recoverOriginal(token);}catch(error){this.check(token);const d=error.detail;if(d?.code==='workflow_task_archive_version_conflict'&&d.not_applied===true&&d.request_key===p.body.request_id&&d.body_sha256===p.sha256){this.pending={...p,rejection:copy(d)};this.write(this.shared,this.pending);}throw error;}}
 async recoverOriginal(token=this.generation,{readOnly=false}={}){
  const p=this.reloadPending();need(p,'当前没有待核对原请求。');need(await this.hash(p.raw)===p.sha256);this.check(token);
  const params=new URLSearchParams({request_key:p.body.request_id,expected_sha256:p.sha256});
  const result=await this.request(ARCHIVES_BASE+'/requests/original?'+params,{},token);
  need(exact(result,['schema','request_key','found','body_sha256','result'])&&result.schema==='workflow-task-archive-original-result-v198'&&result.request_key===p.body.request_id&&typeof result.found==='boolean');
  if(!result.found){need(result.body_sha256===null&&result.result===null);this.pending={...p,not_found:true};if(!readOnly)this.write(this.shared,this.pending);this.notice='尚未查到原请求；原编号和内容保留，不代表未提交。';this.notify();return result;}
  need(result.body_sha256===p.sha256);receipt(result.result,this.scope,p);if(p.direct_result)need(same(p.direct_result,result.result));
  const own=checkedArchiveView(await this.request(ARCHIVES_BASE+'/'+p.archive_id+'/versions/'+result.result.version,{},token),this.scope,p.archive_id);
  need(own.version_hash===result.result.version_hash&&same(metadata(own.value),metadata(p.body)),'服务器保存版本与本次原输入不一致，原请求保留。');this.check(token);
  if(readOnly){this.view=own;this.state.selected=p.archive_id;this.state.version=own.version;this.pending={...p,direct_result:copy(result.result)};this.notice='已查到原保存版本。本机恢复记录暂不能可靠更新，原请求继续保留。';this.notify();return result;}
  this.write(this.prefix+'completed:'+p.body.request_id,{original:p,result:result.result});
  this.state.forms[p.archive_id]={version:own.version,version_hash:own.version_hash,baseline:metadata(own.value),values:metadata(own.value),dirty:false,conflict:false};this.state.selected=p.archive_id;this.state.version=null;this.view=own;this.currentVersion=own.version;
  need(this.persist(),this.storageError);need(this.storage.getItem(this.shared)===JSON.stringify(p),'另一窗口已更改原请求，保留双方记录。');this.storage.removeItem(this.shared);need(this.storage.getItem(this.shared)===null);this.pending=null;
  this.notice='本次修订已保存为新版本，旧版和来源记录保留；尚未正式确认。';await this.refresh(token);this.check(token);
  const latest=this.items.find(row=>row.id===p.archive_id);if(latest&&(latest.version!==own.version||latest.version_hash!==own.version_hash)){this.form.conflict=true;this.currentVersion=latest.version;this.persist();this.notice='本次修订已保存；服务器还已有后续版本，请对照后再编辑。';}
  await this.onSaved(copy(result.result));this.check(token);this.notify();return result;
 }
 async recover(){return this.operation(token=>this.locks?.request?this.locked(()=>this.recoverOriginal(token)):this.recoverOriginal(token,{readOnly:true}));}
 async retrySame(){return this.operation(token=>this.locked(async()=>{await this.identity(token);need(this.canEdit);const p=this.reloadPending();need(p?.not_found&&!p.direct_result&&!p.rejection,'请先只核对原请求；已收到响应或明确被拒的原请求不能重新提交。');need(await this.hash(p.raw)===p.sha256);this.check(token);return this.postOriginal(token);}));}
 async archiveRejected(){return this.operation(token=>this.locked(async()=>{await this.identity(token);need(this.canEdit);const p=this.reloadPending();need(p?.rejection?.not_applied===true&&p.rejection.code==='workflow_task_archive_version_conflict'&&p.rejection.request_key===p.body.request_id&&p.rejection.body_sha256===p.sha256);const result=await this.recoverOriginal(token);if(result.found)return result;this.check(token);this.write(this.prefix+'rejected:'+p.body.request_id,this.pending);need(this.storage.getItem(this.shared)===JSON.stringify(this.pending));this.storage.removeItem(this.shared);need(this.storage.getItem(this.shared)===null);this.pending=null;this.notice='被拒原请求和本页输入已保留。请查看最新版后再决定如何修改。';await this.select(p.archive_id,null,token);this.notify();}));}
}
export const createTaskArchivesController=options=>new TaskArchivesController(options);
