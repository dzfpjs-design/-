// Explicit consent for actual immutable packet text, never a cloud call.
export const TASK_PACKETS_BASE='/task-evidence-packets';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),copy=v=>JSON.parse(JSON.stringify(v));
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const need=(ok,message='原证据、模型或授权记录无法核对；原输入仍保留。')=>{if(!ok)throw Error(message);};
const integer=v=>Number.isSafeInteger(v)&&v>=1;
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const slot=id=>'packet:'+id;
export const evidenceConsentScopeKey=scope=>{need(exact(scope,['actor_id','workspace_id','task_id'])&&Object.values(scope).every(v=>typeof v==='string'&&UUID.test(v)));return [scope.actor_id,scope.workspace_id,scope.task_id].join(':');};
export function checkedConsentPacket(value,scope,{text=true}={}){
 const keys=['schema','id','task_id','actor_id','created_at','version','simulation_only','proposal_only','cloud_authorized_at_creation','task_title','context_status','evidence_status','source_summary','evidence','model_options','model_options_available',...(text?['text']:[])];
 need(exact(value,keys)&&value.schema==='workflow-task-evidence-packet-view-v199'&&UUID.test(value.id)&&value.task_id===scope.task_id&&UUID.test(value.actor_id)&&typeof value.created_at==='string'&&Number.isFinite(Date.parse(value.created_at))&&value.version===1&&typeof value.simulation_only==='boolean'&&value.proposal_only===true&&value.cloud_authorized_at_creation===false&&typeof value.task_title==='string'&&typeof value.context_status==='string'&&typeof value.evidence_status==='string'&&typeof value.model_options_available==='boolean');
 need(exact(value.source_summary,['object_count','observation_count','gap_count','issue_count'])&&Object.values(value.source_summary).every(v=>Number.isSafeInteger(v)&&v>=0));
 const file=value.evidence;need(exact(file,['id','sha256','media_type','size_bytes','version','cloud_model_ids'])&&UUID.test(file.id)&&SHA.test(file.sha256)&&file.media_type==='text/plain'&&integer(file.size_bytes)&&file.size_bytes<=20000&&integer(file.version)&&Array.isArray(file.cloud_model_ids)&&file.cloud_model_ids.every(v=>UUID.test(v))&&new Set(file.cloud_model_ids).size===file.cloud_model_ids.length);
 need(Array.isArray(value.model_options)&&value.model_options.length<=16);const models=new Set();
 for(const model of value.model_options){need(exact(model,['id','name','provider','model','content_hash','authorized'])&&UUID.test(model.id)&&!models.has(model.id)&&typeof model.name==='string'&&model.name.length>0&&['qwen','deepseek'].includes(model.provider)&&typeof model.model==='string'&&model.model.length>0&&SHA.test(model.content_hash)&&typeof model.authorized==='boolean'&&(!model.authorized||file.cloud_model_ids.includes(model.id)));models.add(model.id);}
 if(!value.model_options_available)need(value.model_options.length===0);
 if(text)need(typeof value.text==='string'&&new TextEncoder().encode(value.text).byteLength===file.size_bytes);
 return copy(value);
}

export class TaskEvidenceConsentController{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,crypto=globalThis.crypto,locks=globalThis.navigator?.locks,onChange=()=>{},onSaved=async()=>{}}){
  evidenceConsentScopeKey(scope);Object.assign(this,{api,scope:copy(scope),storage,session,crypto,locks,onChange,onSaved});
  this.prefix='ga-task-evidence-consent-v199:'+evidenceConsentScopeKey(scope)+':';this.outboxKey=this.prefix+'outbox';
  this.state={schema:'task-evidence-consent-draft-v199',scope:this.scope,selected:null,forms:{},history:[]};this.items=[];this.detail=null;this.pending=null;this.loaded=false;this.active=false;this.generation=0;this.busy=false;this.denied=false;this.verified=false;this.localSaved=false;this.protected=false;this.error='';this.notice='';
 }
 get readable(){return this.active&&this.verified&&!this.denied;}
 get form(){return this.state.selected?this.state.forms[slot(this.state.selected)]||null:null;}
 get model(){return this.detail?.model_options.find(value=>value.id===this.form?.model_id&&value.content_hash===this.form?.model_hash)||null;}
 get canAuthorize(){return !!(this.readable&&['admin','operator'].includes(this.role)&&!this.busy&&!this.pending&&!this.protected&&this.localSaved&&this.detail?.model_options_available===true&&this.form&&!this.form.stale&&this.model&&!this.model.authorized&&this.form.acknowledged===true&&/\S/u.test(this.form.reason)&&Array.from(this.form.reason).length<=2000);}
 notify(){this.onChange();}
 mount(){this.active=true;this.generation++;this.busy=false;this.verified=false;}
 dispose(){this.active=false;this.generation++;}
 sessionValues(){return Object.fromEntries(['user','workspace','csrf'].map(k=>[k,this.session.getItem('ga-setup-csrf'+(k==='csrf'?'':'-'+k))]));}
 conceal(){this.denied=true;this.verified=false;this.error='账号、工作区或证据权限已变化，原正文与输入已隐藏。';this.notify();}
 check(token=this.generation){need(this.active&&token===this.generation&&!this.denied,'此授权页已关闭；原输入保留。');if(this.pin&&!same(this.sessionValues(),this.pin)){this.conceal();throw Error(this.error);}}
 async identity(token=this.generation){this.check(token);if(!this.pin){const pin=this.sessionValues();need(pin.user===this.scope.actor_id&&pin.workspace===this.scope.workspace_id&&typeof pin.csrf==='string'&&pin.csrf.length>0);this.pin=pin;}try{const me=await this.api('/me',{headers:{'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.pin.csrf}});this.check(token);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id){this.conceal();throw Error(this.error);}this.role=me.role;this.verified=true;return me;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal();throw error;}}
 async request(path,options={},token=this.generation){await this.identity(token);try{const value=await this.api(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.pin.csrf}});this.check(token);await this.identity(token);return value;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal();throw error;}}
 write(key,value){const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机记录不能可靠读回，暂不提交授权。');}
 async hash(raw){return [...new Uint8Array(await this.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(v=>v.toString(16).padStart(2,'0')).join('');}
 initialize(){try{
  let tab=this.session.getItem('ga-task-evidence-consent-v199-tab');if(!tab){tab=this.crypto.randomUUID();this.session.setItem('ga-task-evidence-consent-v199-tab',tab);need(this.session.getItem('ga-task-evidence-consent-v199-tab')===tab);}need(UUID.test(tab));this.draftKey=this.prefix+'draft:'+tab;
  const raw=this.storage.getItem(this.draftKey);if(raw!==null){const value=JSON.parse(raw);need(exact(value,['schema','scope','selected','forms','history'])&&value.schema===this.state.schema&&same(value.scope,this.scope)&&(value.selected===null||UUID.test(value.selected))&&object(value.forms)&&Array.isArray(value.history));for(const[key,form]of Object.entries(value.forms)){need(key.startsWith('packet:')&&UUID.test(key.slice(7))&&object(form)&&typeof form.reason==='string'&&typeof form.acknowledged==='boolean'&&typeof form.stale==='boolean'&&(form.model_id===null||UUID.test(form.model_id))&&(form.model_hash===null||SHA.test(form.model_hash))&&integer(form.evidence_version)&&SHA.test(form.source_sha));}this.state=copy(value);}
  this.reloadPending();this.protected=false;this.persist();
 }catch(error){this.protected=true;this.localSaved=false;this.error='本机恢复记录不能安全读写；暂不授权，输入保留。'+error.message;}}
 persist(){if(this.protected)return false;try{need(this.draftKey);this.write(this.draftKey,this.state);if(this.pending)need(this.storage.getItem(this.outboxKey)===JSON.stringify(this.pending));this.localSaved=true;return true;}catch(error){this.localSaved=false;this.error='本机保存失败，原理由仍在本页；请重试保护后再授权。'+error.message;return false;}}
 safeToLeave(){if(this.busy)return false;return this.localSaved||this.persist();}
 checkedPending(p){need(object(p)&&p.schema==='task-evidence-consent-outbox-v199'&&same(p.scope,this.scope)&&UUID.test(p.nonce)&&UUID.test(p.packet_id)&&UUID.test(p.evidence_id)&&SHA.test(p.source_sha)&&UUID.test(p.model_id)&&SHA.test(p.model_hash)&&typeof p.raw==='string'&&SHA.test(p.sha256)&&exact(p.body,['model_profile_id','expected_version','reason'])&&p.body.model_profile_id===p.model_id&&integer(p.body.expected_version)&&typeof p.body.reason==='string'&&same(JSON.parse(p.raw),p.body));return copy(p);}
 reloadPending(){const raw=this.storage.getItem(this.outboxKey);if(raw===null){need(!this.pending,'另一窗口改变了原授权请求，本页原件仍保留。');return null;}const value=this.checkedPending(JSON.parse(raw));if(this.pending)need(value.raw===this.pending.raw&&value.sha256===this.pending.sha256);this.pending=value;return value;}
 async operation(run){need(!this.busy,'正在核对原授权记录，请稍候。');const token=this.generation;this.check(token);this.busy=true;this.error='';this.notify();try{return await run(token);}catch(error){if(this.active&&token===this.generation){this.error=error.message;this.notify();}throw error;}finally{if(this.active&&token===this.generation){this.busy=false;this.notify();}}}
 async packet(id,token){const value=checkedConsentPacket(await this.request(TASK_PACKETS_BASE+'/'+id,{},token),this.scope);need(value.id===id&&await this.hash(value.text)===value.evidence.sha256,'证据正文与原文件摘要不一致，未授权。');this.check(token);return value;}
 ingest(value){
  const key=slot(value.id),form=this.state.forms[key];if(form){need(form.source_sha===value.evidence.sha256,'原文件内容变化，旧输入仍保留，未继承授权。');form.stale=form.evidence_version!==value.evidence.version||!!form.model_id&&!value.model_options.some(v=>v.id===form.model_id&&v.content_hash===form.model_hash);}
  else this.state.forms[key]={model_id:null,model_hash:null,reason:'',acknowledged:false,stale:false,evidence_version:value.evidence.version,source_sha:value.evidence.sha256};
  this.state.selected=value.id;this.detail=value;this.persist();this.notify();return value;
 }
 async load(token){const value=await this.request(TASK_PACKETS_BASE+'?'+new URLSearchParams({task_id:this.scope.task_id}),{},token);need(exact(value,['schema','task_id','items'])&&value.schema==='workflow-task-evidence-packet-list-v199'&&value.task_id===this.scope.task_id&&Array.isArray(value.items));const ids=new Set();for(const item of value.items){checkedConsentPacket(item,this.scope,{text:false});need(!ids.has(item.id));ids.add(item.id);}this.items=copy(value.items);this.loaded=true;if(this.state.selected&&ids.has(this.state.selected))this.ingest(await this.packet(this.state.selected,token));else{this.detail=null;this.state.selected=null;this.persist();this.notify();}return value;}
 open(){return this.operation(async token=>{await this.identity(token);this.initialize();await this.load(token);if(this.pending)await this.recoverOriginal(token);});}
 refresh(){return this.operation(token=>this.load(token));}
 select(id){return this.operation(async token=>{need(UUID.test(id)&&this.items.some(v=>v.id===id));if(!this.localSaved)need(this.persist());return this.ingest(await this.packet(id,token));});}
 edit(change){this.check();need(this.readable&&!this.busy&&!this.pending&&this.form&&Object.keys(change).every(k=>['model_id','reason','acknowledged'].includes(k)));const form=this.form;if(Object.hasOwn(change,'model_id')){need(change.model_id===''||this.detail.model_options_available&&this.detail.model_options.some(v=>v.id===change.model_id));const model=this.detail.model_options.find(v=>v.id===change.model_id);form.model_id=model?.id??null;form.model_hash=model?.content_hash??null;form.acknowledged=false;}if(Object.hasOwn(change,'reason')){need(typeof change.reason==='string');form.reason=change.reason;form.acknowledged=false;}if(Object.hasOwn(change,'acknowledged')){need(typeof change.acknowledged==='boolean');form.acknowledged=change.acknowledged;}this.persist();this.notify();}
 useLatest(){this.check();need(!this.busy&&!this.pending&&this.form?.stale);this.write(this.prefix+'retained:'+this.crypto.randomUUID(),{scope:this.scope,packet_id:this.detail.id,form:copy(this.form)});this.form.evidence_version=this.detail.evidence.version;this.form.stale=!!this.form.model_id&&!this.model;this.form.acknowledged=false;this.persist();this.notify();}
 async locked(run){need(this.locks?.request,'当前浏览器不能保证多窗口授权记录一致，原理由保留。');return this.locks.request(this.prefix+'authorize',{mode:'exclusive'},run);}
 authorize(){need(this.canAuthorize,'请先核对原文、模型、理由和明确授权勾选。');return this.operation(token=>this.locked(async()=>{
  await this.identity(token);need(['admin','operator'].includes(this.role)&&!this.reloadPending()&&this.localSaved&&!this.protected);
  const before=await this.packet(this.detail.id,token),form=this.form,model=before.model_options.find(v=>v.id===form.model_id&&v.content_hash===form.model_hash);
  need(before.model_options_available&&model&&!model.authorized&&before.evidence.sha256===form.source_sha);
  if(before.evidence.version!==form.evidence_version){this.ingest(before);throw Error('原件授权版本已变化，请核对新状态后再决定；原理由保留。');}
  need(form.acknowledged&&/\S/u.test(form.reason)&&Array.from(form.reason).length<=2000);
  const body={model_profile_id:model.id,expected_version:before.evidence.version,reason:form.reason},raw=JSON.stringify(body);
  const p={schema:'task-evidence-consent-outbox-v199',scope:copy(this.scope),nonce:this.crypto.randomUUID(),packet_id:before.id,evidence_id:before.evidence.id,source_sha:before.evidence.sha256,model_id:model.id,model_hash:model.content_hash,body,raw,sha256:await this.hash(raw)};this.check(token);this.checkedPending(p);
  this.write(this.prefix+'original:'+p.nonce,p);this.write(this.outboxKey,p);this.pending=p;need(this.persist());need(await this.hash(p.raw)===p.sha256);
  try{await this.request('/evidence/'+p.evidence_id+'/authorize',{method:'POST',body:p.raw},token);}catch(error){this.notice='原授权结果尚未核对；原正文、模型、理由与请求已保留。仅重新读取这份证据的授权状态，不自动重发。';this.notify();throw error;}
  return this.recoverOriginal(token);
 }));}
 recover(){return this.operation(token=>this.locked(()=>this.recoverOriginal(token)));}
 async recoverOriginal(token){
  const p=this.reloadPending();need(p&&await this.hash(p.raw)===p.sha256);const value=await this.packet(p.packet_id,token);need(value.evidence.id===p.evidence_id&&value.evidence.sha256===p.source_sha);
  const model=value.model_options.find(v=>v.id===p.model_id&&v.content_hash===p.model_hash);
  if(!value.model_options_available||!model||model.authorized!==true||!value.evidence.cloud_model_ids.includes(p.model_id)){
   this.ingest(value);this.notice='当前仍不能确认这份原件已授权给原模型；原请求和理由保留。当前接口没有独立请求日志，不据此推断未提交，也不自动重发。';this.notify();return {authorized:false};
  }
  const receipt={schema:'task-evidence-consent-current-state-v199',packet_id:p.packet_id,evidence_id:p.evidence_id,model_id:p.model_id,source_sha:p.source_sha,evidence_version:value.evidence.version,authorized:true,original_request_application_proven:false};
  this.write(this.prefix+'confirmed:'+p.nonce,{original:p,current_state:receipt});
  if(!this.state.history.some(v=>v.nonce===p.nonce))this.state.history.push({nonce:p.nonce,reason:p.body.reason,model_name:model.name,model:model.model,source_sha:p.source_sha});need(this.persist());
  need(this.storage.getItem(this.outboxKey)===JSON.stringify(this.pending));this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);this.pending=null;
  this.ingest(value);this.form.evidence_version=value.evidence.version;this.form.stale=false;this.form.acknowledged=false;this.persist();this.notice='已核对：当前这份原件已授权给所选模型，未重复提交。当前状态不能单独证明由哪次请求写入；模型尚未被本页面调用。';this.notify();this.check(token);await this.onSaved({kind:'evidence_consent',packet:value,current_state:receipt});return receipt;
 }
 retryLocal(){this.check();const saved=this.persist();this.notify();return saved;}
 exportRecord(){return JSON.stringify({schema:'task-evidence-consent-recovery-copy-v199',scope:this.scope,draft:this.state,pending:this.pending},null,2);}
}
export const createTaskEvidenceConsentController=options=>new TaskEvidenceConsentController(options);
