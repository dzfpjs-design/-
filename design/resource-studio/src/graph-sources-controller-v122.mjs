// Explicit registration of existing processing capabilities. No device or Task grant.
import {enumerateGraphSourceRecovery,readGraphSourceRecoveryCandidate,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH} from './graph-sources-recovery-v123.mjs';
const BASE='/setup/graph-event-producers';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[a-f0-9]{64}$/,VERSION=/^\d+\.\d+\.\d+$/;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const exact=(x,keys)=>object(x)&&Object.keys(x).sort().join(',')===keys.split(',').sort().join(',');
const safeText=(x,max=1000)=>typeof x==='string'&&x.length<=max&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(x);
const text=(x,max=1000)=>safeText(x,max)&&x.trim().length>0;
const integer=x=>Number.isSafeInteger(x)&&x>=1;
const need=(x,message='来源记录无法核对，原输入和原请求保留。')=>{if(!x){const error=Error(message);error.code='GRAPH_SOURCE_INPUT';throw error;}};
const clone=x=>JSON.parse(JSON.stringify(x));
const stable=x=>JSON.stringify(object(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,JSON.parse(stable(x[k]))])):Array.isArray(x)?x.map(v=>JSON.parse(stable(v))):x);
const same=(a,b)=>stable(a)===stable(b);
export const GRAPH_SOURCE_ADAPTERS=Object.freeze({'processed-observation-v1':'已处理的观测结果','anomaly-candidate-v1':'待核实的异常'});
const blank=()=>({label:'',adapter_id:'processed-observation-v1',source:null,extractor:null,selected_registration:null,reasons:{}});
function scopeValue(scope){need(exact(scope,'actor_id,workspace_id')&&UUID.test(scope.actor_id)&&UUID.test(scope.workspace_id),'请先核对当前登录账号和工作区。');return clone(scope);}
function command(value,kind){
 if(kind==='register'){
  need(exact(value,'schema,request_id,label,adapter_id,source_id,source_hash,extractor_id,extractor_hash')&&value.schema==='graph-event-producer-command-v1'
   &&UUID.test(value.request_id)&&text(value.label,200)&&Object.hasOwn(GRAPH_SOURCE_ADAPTERS,value.adapter_id)
   &&UUID.test(value.source_id)&&UUID.test(value.extractor_id)&&value.source_id!==value.extractor_id&&SHA.test(value.source_hash)&&SHA.test(value.extractor_hash));
 }else{
  need(kind==='revoke'&&exact(value,'schema,request_id,producer_id,producer_version,contract_hash,reason')&&value.schema==='graph-event-producer-revocation-v2'
   &&UUID.test(value.request_id)&&UUID.test(value.producer_id)&&integer(value.producer_version)&&SHA.test(value.contract_hash)
   &&text(value.reason)&&value.reason===value.reason.trim());
 }
 return value;
}
export async function graphSourceCommandHash(scope,kind,body,crypto=globalThis.crypto){
 scopeValue(scope);command(body,kind);
 const value={schema:kind==='register'?'graph-event-producer-command-hash-v1':'graph-event-producer-revocation-command-hash-v2',
  workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:body};
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(value)));
 return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');
}
function retirementCommand(body){
 need(exact(body,'schema,request_id,kind,original,reason')&&body.schema==='graph-event-source-retirement-v1'&&UUID.test(body.request_id)
  &&['register','revoke'].includes(body.kind)&&text(body.reason)&&body.reason===body.reason.trim());
 command(body.original,body.kind);need(body.request_id!==body.original.request_id);return body;
}
export async function graphSourceRetirementHash(scope,body,crypto=globalThis.crypto){
 scopeValue(scope);retirementCommand(body);const value={schema:'graph-event-source-retirement-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:body};
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(value)));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');
}
export function checkGraphSourceCapability(value,kind){
 need(object(value)&&Object.keys(value).every(k=>['id','name','description','kind','content_hash','release_version'].includes(k))
  &&UUID.test(value.id)&&text(value.name,300)&&(value.description===undefined||value.description===null||safeText(value.description,4000))
  &&SHA.test(value.content_hash)&&VERSION.test(value.release_version)
  &&(kind==='source'?value.kind==='source':['tool','specialist_model','foundation_model'].includes(value.kind)));
 return clone(value);
}
function contract(value){
 need(exact(value,'schema,adapter_id,event_kind,correlation_kind,source_id,source_hash,source_release_version,extractor_id,extractor_hash,extractor_release_version,result_schema,output_schema,output_schema_hash,contract_hash')
  &&value.schema==='graph-event-observation-source-contract-v1'&&Object.hasOwn(GRAPH_SOURCE_ADAPTERS,value.adapter_id)
  &&value.event_kind===({'processed-observation-v1':'observation.data','anomaly-candidate-v1':'perception.anomaly'})[value.adapter_id]
  &&value.correlation_kind==='entity_id'&&UUID.test(value.source_id)&&UUID.test(value.extractor_id)&&value.source_id!==value.extractor_id
  &&SHA.test(value.source_hash)&&SHA.test(value.extractor_hash)&&VERSION.test(value.source_release_version)&&VERSION.test(value.extractor_release_version)
  &&object(value.result_schema)&&object(value.output_schema)&&SHA.test(value.output_schema_hash)&&SHA.test(value.contract_hash));
 return value;
}
function contractMatches(value,body){
 contract(value);need(['adapter_id','source_id','source_hash','extractor_id','extractor_hash'].every(k=>value[k]===body[k]));
}
export function checkGraphSourceRegistration(row,{catalog=false}={}){
 const keys='id,label,status,version,contract_hash,contract,registration,execution_granted,physical_state_verified';
 need(exact(row,keys+(catalog?',source_name,extractor_name,current_availability,receipt_verified':''))&&UUID.test(row.id)&&text(row.label,200)
  &&['active','revoked'].includes(row.status)&&integer(row.version)&&SHA.test(row.contract_hash)
  &&row.execution_granted===false&&row.physical_state_verified===false);
 contract(row.contract);const r=row.registration;need(exact(r,'request_id,actor_id,command_hash,command')&&UUID.test(r.request_id)&&UUID.test(r.actor_id)&&SHA.test(r.command_hash));
 command(r.command,'register');contractMatches(row.contract,r.command);need(r.command.request_id===r.request_id&&r.command.label===row.label);
 if(catalog){
  const a=row.current_availability;
  need((row.source_name===null||text(row.source_name,300))&&(row.extractor_name===null||text(row.extractor_name,300))&&row.receipt_verified===false
   &&exact(a,'available,reason_code,label')&&typeof a.available==='boolean'&&(a.reason_code===null||text(a.reason_code,100))&&text(a.label,1000));
 }
 return row;
}
function formValue(value){
 need(exact(value,'label,adapter_id,source,extractor,selected_registration,reasons')&&safeText(value.label,200)
  &&Object.hasOwn(GRAPH_SOURCE_ADAPTERS,value.adapter_id)&&(value.selected_registration===null||UUID.test(value.selected_registration))&&object(value.reasons));
 if(value.source!==null)checkGraphSourceCapability(value.source,'source');if(value.extractor!==null)checkGraphSourceCapability(value.extractor,'extractor');
 for(const [id,reason] of Object.entries(value.reasons))need(UUID.test(id)&&safeText(reason));return clone(value);
}
export function graphSourceResultFields(schema){
 const result=[],labels={string:'文字',integer:'整数',number:'数值',boolean:'是或否',object:'结构化结果',array:'多项结果',null:'空值'};
 function visit(spec,label,depth,required){
  if(!object(spec)||result.length>=40)return;
  const kind=Array.isArray(spec.type)?spec.type.filter(x=>x!=='null').join(' / '):spec.type;
  if(kind==='object'&&object(spec.properties)&&depth<3&&Object.keys(spec.properties).length){
   Object.entries(spec.properties).forEach(([key,child],index)=>{const name=text(child?.title,200)?child.title:!UUID.test(key)&&!SHA.test(key)&&key.length<60?key:'结果字段 '+(index+1);visit(child,label?label+' · '+name:name,depth+1,Array.isArray(spec.required)&&spec.required.includes(key));});
  }else result.push({label:label||'处理结果',type:labels[kind]||'按已发布的结果定义',required:!!required});
 }
 visit(schema,'',0,false);return result;
}

export class GraphSourcesControllerV122{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,crypto=globalThis.crypto,onChange=()=>{}}){
  Object.assign(this,{api,scope:scopeValue(scope),storage,session,crypto,onChange});
  this.prefix='ga-graph-sources-v122:'+this.scope.workspace_id+':'+this.scope.actor_id+':';this.form=blank();this.preview=null;this.pending=null;this.pendingNotFound=false;
  this.pages={source:{items:[],next_after:null,loaded:false},extractor:{items:[],next_after:null,loaded:false},registrations:{items:[],next_after:null,loaded:false}};
  this.detail=null;this.error='';this.notice='';this.busy=false;this.localSaved=true;this.canRegister=false;this.disposed=false;this.corrupt=false;
  this.retirement=null;this.retirementNotFound=false;this.recovery={items:[],next_offset:null,problem_count:0,loaded:false};
  this.identityStatus='unverified';this.identityRequested=false;this.identityEpoch=0;this.formOwner=crypto.randomUUID();this.formGeneration=0;
  try{
   this.sessionPin={actor:session.getItem('ga-setup-csrf-user'),workspace:session.getItem('ga-setup-csrf-workspace'),token:session.getItem('ga-setup-csrf')};
   need(this.sessionPin.actor===this.scope.actor_id&&this.sessionPin.workspace===this.scope.workspace_id&&text(this.sessionPin.token));
   this.windowID=session.getItem(this.prefix+'window');if(this.windowID===null){this.windowID=crypto.randomUUID();this._write(session,this.prefix+'window',this.windowID);}need(UUID.test(this.windowID));
   const headRaw=session.getItem(this.prefix+'form-head');
   if(headRaw!==null){
    const head=JSON.parse(headRaw);need(exact(head,'schema,scope,window_id,owner,generation,key,form')&&head.schema==='graph-source-form-head-v122'
     &&same(head.scope,this.scope)&&head.window_id===this.windowID&&UUID.test(head.owner)&&integer(head.generation)
     &&head.key===this.prefix+'form:slot:'+head.owner+':'+(head.generation%2));
    // A duplicated browser tab carries this complete independent snapshot.
    // Its old local slot may have been reused by the original tab since then.
    this.form=formValue(head.form);
    const local=storage.getItem(head.key);let matches=false;
    if(local!==null)try{const row=JSON.parse(local);matches=row.schema==='graph-source-form-slot-v122'&&same(row.scope,this.scope)
     &&row.window_id===this.windowID&&row.owner===head.owner&&row.generation===head.generation&&same(formValue(row.form),this.form);}catch{}
    if(!matches)this.notice='本页按独立保留的完整副本恢复；原页保存槽的其他内容保留，不会覆盖。';
   }else{
    // Read old immutable v122 form records without rewriting or collecting them.
    const pointer=session.getItem(this.prefix+'form');if(pointer!==null){const row=JSON.parse(storage.getItem(pointer));need(row?.schema==='graph-source-form-v122'&&same(row.scope,this.scope)&&row.window_id===this.windowID&&pointer.startsWith(this.prefix+'form:'));this.form=formValue(row.form);}
   }
   this.entryForm=formValue(this.form);
   const pending=session.getItem(this.prefix+'pending');if(pending!==null){need(pending.startsWith(this.prefix+'outbox:'));this.pending=this._readPending(pending);}
   const retiring=session.getItem(this.prefix+'retirement');if(retiring!==null)this.retirement=this._readRetirement(retiring);
  }catch{this.corrupt=true;this.localSaved=false;this.error='本机输入或原请求暂无法读取，未自动覆盖；请保留此浏览器记录并核对登录或存储。';}
 }
 notify(){if(!this.disposed)this.onChange(this);}
 dispose(){this.disposed=true;}
 active(allowCorrupt=false){
  need(!this.disposed&&(!this.corrupt||allowCorrupt),'此页账号、恢复记录或保存范围需核对，原输入保留。');
  const matches=this.session.getItem('ga-setup-csrf-user')===this.scope.actor_id&&this.session.getItem('ga-setup-csrf-workspace')===this.scope.workspace_id
   &&this.session.getItem('ga-setup-csrf')===this.sessionPin.token;
  if(!matches){this.identityStatus='concealed';this.notify();}need(matches,'登录账号或工作区已变化，原输入与原请求保留。');
 }
 _write(storage,key,raw){
  if(storage===this.storage&&raw.length>GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH)throw Object.assign(Error('本页输入超过单份可恢复范围，当前输入保留；请缩短说明或分批保存，暂未提交。'),{code:'GRAPH_SOURCE_TOO_LARGE'});
  storage.setItem(key,raw);need(storage.getItem(key)===raw,'本机原请求未能完整保存并读回，未向服务器提交。');
 }
 _once(key,value){const raw=JSON.stringify(value),prior=this.storage.getItem(key);need(prior===null||prior===raw,'本机原请求已有不同内容，未覆盖原记录。');this._write(this.storage,key,raw);}
 _completion(key,value){
  const prior=this.storage.getItem(key);if(prior!==null){need(same(JSON.parse(prior),value),'本机原回执提示与服务器结果不同，原记录保留。');return;}
  this._write(this.storage,key,JSON.stringify(value));
 }
 _pendingOrigin(){const value=this.session.getItem(this.prefix+'pending-origin-window')||this.windowID;need(UUID.test(value));return value;}
 _readPending(key,origin=this._pendingOrigin()){
  const p=JSON.parse(this.storage.getItem(key));need(exact(p,'schema,scope,window_id,kind,body,exact_body,command_hash,preview_contract_hash')
   &&p.schema==='graph-source-outbox-v122'&&same(p.scope,this.scope)&&p.window_id===origin&&UUID.test(origin)&&['register','revoke'].includes(p.kind)
   &&SHA.test(p.command_hash)&&typeof p.exact_body==='string'&&JSON.stringify(p.body)===p.exact_body
   &&(p.kind==='register'?SHA.test(p.preview_contract_hash):p.preview_contract_hash===null));
  command(p.body,p.kind);need(key===this.prefix+'outbox:'+p.body.request_id);return p;
 }
 _readRetirement(key,origin=this.session.getItem(this.prefix+'retirement-origin-window')||this.windowID){
  const r=JSON.parse(this.storage.getItem(key));need(exact(r,'schema,scope,window_id,body,exact_body,command_hash,original_window_id')
   &&r.schema==='graph-source-retirement-outbox-v123'&&same(r.scope,this.scope)&&r.window_id===origin&&UUID.test(origin)&&UUID.test(r.original_window_id)
   &&SHA.test(r.command_hash)&&r.exact_body===JSON.stringify(r.body));retirementCommand(r.body);need(key===this.prefix+'retire-outbox:'+r.body.request_id);return r;
 }
 async request(path,options={}){this.active(path==='/me');try{const value=await this.api(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.sessionPin.token}});this.active(path==='/me');return value;}
  catch(error){if(error?.status===401||error?.status===403){this.identityStatus='concealed';this.notify();}throw error;}}
 async identity(){
  const epoch=this.identityEpoch;this.identityStatus='checking';this.notify();
  try{
   const me=await this.request('/me');
   need(epoch===this.identityEpoch&&!this.identityRequested,'登录核对已更新，原输入与原请求保留；正在重新核对。');
   need(me.user_id===this.scope.actor_id&&me.workspace_id===this.scope.workspace_id,'登录账号或工作区已变化，原请求保留。');
   this.identityStatus='verified';this.notify();return me;
  }
  catch(error){if(epoch===this.identityEpoch&&!this.identityRequested)this.identityStatus='concealed';this.notify();throw error;}
 }
 async _run(callback,{identityOnly=false}={}){this.active(identityOnly);need(!this.busy,'请先等待当前操作完成，原输入保留。');this.busy=true;this.error='';this.notify();try{await this.identity();return await callback();}
  catch(error){if(!this.disposed)this.error=['GRAPH_SOURCE_INPUT','GRAPH_SOURCE_TOO_LARGE','GRAPH_SOURCE_RECOVERY_CHANGED','GRAPH_SOURCE_RECOVERY_STORAGE','GRAPH_SOURCE_RECOVERY_RECORD'].includes(error?.code)?error.message:error?.status===403?'当前账号不能执行这项操作，原输入和原请求保留。':error?.status===409?'来源状态或发布版本已变化；原请求保留，请先核对原结果，新的登记需重新预览。':'本次核对或保存尚未完成，原输入和原请求保留。请检查连接与本机存储后继续核对。';throw error;}
  finally{this.busy=false;this.notify();if(this.identityRequested&&!this.disposed){this.identityRequested=false;this.recheckIdentity().catch(()=>{});}}}
 recheckIdentity(){
  this.identityEpoch++;this.identityStatus='checking';this.notify();
  if(this.busy){this.identityRequested=true;return Promise.resolve(false);}
  return this._run(async()=>true,{identityOnly:true});
 }
 persistForm(){
  try{
   this.active();const form=formValue(this.form),generation=this.formGeneration+1,key=this.prefix+'form:slot:'+this.formOwner+':'+(generation%2);
   // Preserve the activation's entry snapshot forever. Reuse only this fresh
   // owner's slots; no previous owner or immutable request is ever collected.
   this._once(this.prefix+'form:entry:'+this.formOwner,{schema:'graph-source-form-entry-v122',scope:this.scope,window_id:this.windowID,owner:this.formOwner,form:this.entryForm});
   const prior=this.storage.getItem(key);
   if(prior!==null){const row=JSON.parse(prior);need(exact(row,'schema,scope,window_id,owner,generation,form')&&row.schema==='graph-source-form-slot-v122'
    &&same(row.scope,this.scope)&&row.window_id===this.windowID&&row.owner===this.formOwner&&integer(row.generation)&&row.generation<=generation);formValue(row.form);}
   const row={schema:'graph-source-form-slot-v122',scope:this.scope,window_id:this.windowID,owner:this.formOwner,generation,form};
   need(this.storage.getItem(key)===prior,'本页保存槽已有其他内容，原稿保留。');this._write(this.storage,key,JSON.stringify(row));
   this._write(this.session,this.prefix+'form-head',JSON.stringify({schema:'graph-source-form-head-v122',scope:this.scope,window_id:this.windowID,owner:this.formOwner,generation,key,form}));
   this.formGeneration=generation;this.localSaved=true;return true;
  }
  catch(error){this.localSaved=false;this.error=error?.code==='GRAPH_SOURCE_TOO_LARGE'?error.message:'本页输入尚未完整保存到本机，请保留页面并重试保存；暂不能提交或离开。';return false;}
 }
 edit(patch){try{this.active();this.form=formValue({...this.form,...patch});this.preview=null;this.notice='';this.persistForm();}catch{this.error='输入或登录范围需核对，原稿保留。';}this.notify();}
 saveLocal(){const result=this.persistForm();this.notify();return result;}
 chooseCapability(kind,id){const item=this.pages[kind]?.items.find(x=>x.id===id);need(['source','extractor'].includes(kind)&&item);this.edit({[kind]:clone(item)});}
 async _load(kind,append=false){
  const page=this.pages[kind];need(page&&(!append||page.next_after));
  const path=BASE+(kind==='registrations'?'/catalog/registrations':'/catalog/capabilities?kind='+kind);
  const query=(path.includes('?')?'&':'?')+'limit=20'+(append?'&after='+encodeURIComponent(page.next_after):'');
  const value=await this.request(path+query);need(exact(value,'items,next_after,can_register')&&Array.isArray(value.items)&&value.items.length<=20
   &&(value.next_after===null||UUID.test(value.next_after))&&typeof value.can_register==='boolean');
  const items=value.items.map(item=>kind==='registrations'?checkGraphSourceRegistration(item,{catalog:true}):checkGraphSourceCapability(item,kind));
  need(new Set(items.map(x=>x.id)).size===items.length&&(!append||value.next_after!==page.next_after));
  const merged=append?[...page.items]:[];for(const item of items){const at=merged.findIndex(x=>x.id===item.id);if(at<0)merged.push(item);else merged[at]=item;}
  this.pages[kind]={items:merged,next_after:value.next_after,loaded:true};this.canRegister=value.can_register;this.notify();return items;
 }
 _discoverRecovery(more=false){
  const offset=more?this.recovery.next_offset:0;need(offset!==null,'本次本机记录已读取完。');
  const page=enumerateGraphSourceRecovery({storage:this.storage,scope:this.scope,offset,limit:20,maxScan:200});
  this.recovery={items:more?[...this.recovery.items,...page.items]:page.items,next_offset:page.next_offset,problem_count:(more?this.recovery.problem_count:0)+page.problem_count,loaded:true};this.notify();return this.recovery;
 }
 discoverRecovery(more=false){return this._run(async()=>this._discoverRecovery(more));}
 open(){return this._run(async()=>{this._discoverRecovery();await Promise.all(['source','extractor','registrations'].map(k=>this._load(k)));if(this.form.selected_registration)await this._detail(this.form.selected_registration);});}
 loadMore(kind){return this._run(async()=>this._load(kind,true));}
 refresh(){return this.open();}
 async _detail(id){need(UUID.test(id));const value=await this.request(BASE+'/catalog/registrations/'+id);checkGraphSourceRegistration(value,{catalog:true});need(value.id===id);this.detail=value;return value;}
 selectRegistration(id){return this._run(async()=>{const value=await this._detail(id);this.form.selected_registration=id;this.preview=null;need(this.persistForm());return value;});}
 setReason(reason){need(this.detail);this.edit({reasons:{...this.form.reasons,[this.detail.id]:reason}});}
 previewRegistration(){return this._run(async()=>{
  need(this.canRegister&&!this.pending&&!this.retirement&&this.persistForm(),'请先保存本页输入并核对原请求。');const f=this.form;
  need(text(f.label,200)&&f.source&&f.extractor,'请填写结果名称，并分别选择来源与处理能力。');
  const body={schema:'graph-event-producer-command-v1',request_id:this.crypto.randomUUID(),label:f.label,adapter_id:f.adapter_id,
   source_id:f.source.id,source_hash:f.source.content_hash,extractor_id:f.extractor.id,extractor_hash:f.extractor.content_hash};
  command(body,'register');const exact_body=JSON.stringify(body),form_signature=stable(f);await this.identity();
  const value=await this.request(BASE+'/preview',{method:'POST',body:exact_body});need(exact(value,'contract,contract_hash,command_hash,execution_granted,meaning')
   &&SHA.test(value.contract_hash)&&SHA.test(value.command_hash)&&value.execution_granted===false&&text(value.meaning,2000));
  contractMatches(value.contract,body);need(value.command_hash===await graphSourceCommandHash(this.scope,'register',body,this.crypto));
  need(value.contract.source_release_version===f.source.release_version&&value.contract.extractor_release_version===f.extractor.release_version);
  need(stable(this.form)===form_signature,'预览时输入已变化，请用当前输入重新预览。');
  this.preview={body,exact_body,command_hash:value.command_hash,contract_hash:value.contract_hash,contract:value.contract,form_signature};return value;
 });}
 async _validatePending(p){command(p.body,p.kind);need(p.command_hash===await graphSourceCommandHash(this.scope,p.kind,p.body,this.crypto));
  need(this.session.getItem(this.prefix+'pending')===this.prefix+'outbox:'+p.body.request_id&&same(this._readPending(this.prefix+'outbox:'+p.body.request_id),p));}
 async _stage(kind,body,previewContractHash=null){
  need(!this.pending&&this.localSaved);command(body,kind);
  const p={schema:'graph-source-outbox-v122',scope:clone(this.scope),window_id:this.windowID,kind,body:clone(body),exact_body:JSON.stringify(body),
   command_hash:await graphSourceCommandHash(this.scope,kind,body,this.crypto),preview_contract_hash:previewContractHash};
  this.active();const key=this.prefix+'outbox:'+body.request_id;this._once(key,p);this._write(this.session,this.prefix+'pending-origin-window',p.window_id);this._write(this.session,this.prefix+'pending',key);
  this.pending=p;this.pendingNotFound=false;await this._validatePending(p);this.notify();return p;
 }
 async _registered(row,p){
  checkGraphSourceRegistration(row);const r=row.registration;
  need(r.actor_id===this.scope.actor_id&&r.request_id===p.body.request_id&&r.command_hash===p.command_hash&&same(r.command,p.body)
   &&row.contract_hash===p.preview_contract_hash);return row;
 }
 _revoked(receipt,p){
  need(exact(receipt,'id,request_id,actor_id,producer_id,command_hash,command,result')&&UUID.test(receipt.id)&&receipt.request_id===p.body.request_id
   &&receipt.actor_id===this.scope.actor_id&&receipt.producer_id===p.body.producer_id&&receipt.command_hash===p.command_hash&&same(receipt.command,p.body)
   &&exact(receipt.result,'status,version,contract_hash')&&receipt.result.status==='revoked'&&receipt.result.version===p.body.producer_version+1&&receipt.result.contract_hash===p.body.contract_hash);return receipt;
 }
 async _finish(p,receipt){
  this.active();await this._validatePending(p);
  // The producer's availability/status can legitimately change after saving.
  // This marker only fences stale local sends; GET still verifies real history.
  need(this.storage.getItem(this.prefix+'retired:'+p.body.request_id)===null);
  this._completion(this.prefix+'completed:'+p.body.request_id,{schema:'graph-source-completed-v122',scope:clone(this.scope),window_id:p.window_id,kind:p.kind,command_hash:p.command_hash,exact_body:p.exact_body,...(p.kind==='revoke'?{receipt}: {})});
  this.active();need(this.session.getItem(this.prefix+'pending')===this.prefix+'outbox:'+p.body.request_id);
  this.session.removeItem(this.prefix+'pending');need(this.session.getItem(this.prefix+'pending')===null,'原结果已保存，本机结清尚未完成；请继续核对原请求。');this.session.removeItem(this.prefix+'pending-origin-window');
  this.pending=null;this.pendingNotFound=false;this.preview=null;this.notice=p.kind==='register'?'登记已保存。请返回业务与流程，刷新后明确选择这项结果来源。':'已停止此结果来源。业务流程不能继续接收它的新结果；正在等待该来源的任务可能暂停。已保存的记录保留，物理设备不会关闭。';
  await this._refreshSaved(p,receipt);
  return receipt;
 }
 async _refreshSaved(p,receipt){
  const id=p.kind==='register'?receipt.id:receipt.producer_id;
  // A saved stop receipt must immediately remove active stop controls, even
  // when the independent current-projection GET fails after successful save.
  if(p.kind==='revoke'){
   const stopped=row=>row.id===id?{...row,status:'revoked',version:Math.max(row.version,receipt.result.version),current_availability:{available:false,reason_code:'registration_stopped',label:'停止回执已保存；当前记录可刷新核对。'}}:row;
   this.pages.registrations.items=this.pages.registrations.items.map(stopped);if(this.detail)this.detail=stopped(this.detail);
  }
  try{
   const row=await this._detail(id);this.pages.registrations.items=[row,...this.pages.registrations.items.filter(item=>item.id!==id)];
   this.pages.registrations.loaded=true;
  }catch{if(!this.disposed)this.notice+=' 当前列表暂未刷新，原保存结果已核对；可刷新记录继续查看。';}
 }
 async _send(p){
  need(!this.retirement,'这次原操作正在核对停止结果，不能同时重发原操作。');await this.identity();await this._validatePending(p);need(this.storage.getItem(this.prefix+'completed:'+p.body.request_id)===null&&this.storage.getItem(this.prefix+'retired:'+p.body.request_id)===null,'原请求已有本机终态提示，请先核对原结果。');
  this.pendingNotFound=false;
  if(p.kind==='register'){
   const value=await this.request(BASE,{method:'POST',body:p.exact_body});need(exact(value,'producer,created')&&typeof value.created==='boolean');
   return this._finish(p,await this._registered(value.producer,p));
  }
  const value=await this.request(BASE+'/revocations',{method:'POST',body:p.exact_body});need(exact(value,'receipt,replayed')&&typeof value.replayed==='boolean');return this._finish(p,this._revoked(value.receipt,p));
 }
 register(){return this._run(async()=>{need(this.canRegister&&this.preview&&!this.pending&&!this.retirement&&this.persistForm());const v=this.preview;
  need(v.form_signature===stable(this.form)&&v.exact_body===JSON.stringify(v.body));const p=await this._stage('register',v.body,v.contract_hash);return this._send(p);});}
 revoke(){return this._run(async()=>{need(this.canRegister&&!this.pending&&!this.retirement&&this.detail?.status==='active'&&this.persistForm());
  const row=this.detail,reason=(this.form.reasons[row.id]||'').trim();need(text(reason),'请说明停止供应业务结果的原因。');
  const body={schema:'graph-event-producer-revocation-v2',request_id:this.crypto.randomUUID(),producer_id:row.id,producer_version:row.version,contract_hash:row.contract_hash,reason};
  const p=await this._stage('revoke',body);return this._send(p);
 });}
 recover(){return this._run(async()=>{need(this.pending);return this._lookupSource(this.pending);});}
 resend(){return this._run(async()=>{need(this.pending&&this.pendingNotFound&&!this.retirement,'请先只核对原请求，明确尚未找到且没有待核对的停止操作后再按原内容重试。');return this._send(this.pending);});}
 async _sourceOutbox(requestID,origin){
  const p=this._readPending(this.prefix+'outbox:'+requestID,origin);need(p.command_hash===await graphSourceCommandHash(this.scope,p.kind,p.body,this.crypto));return p;
 }
 _linkPending(p){
  this.active();need(!this.pending||same(this.pending,p),'本页还有另一项原请求待核对，请先处理；没有覆盖原请求。');
  need(!this.retirement||(this.retirement.body.kind===p.kind&&same(this.retirement.body.original,p.body)),'本页还有另一项停止操作待核对，原内容保留。');
  const key=this.prefix+'outbox:'+p.body.request_id;need(same(this._readPending(key,p.window_id),p));
  this._write(this.session,this.prefix+'pending-origin-window',p.window_id);this._write(this.session,this.prefix+'pending',key);
  this.pending=this._readPending(key);this.pendingNotFound=false;this.preview=null;this.notify();
 }
 async _validateRetirement(r){
  this.active();need(r.command_hash===await graphSourceRetirementHash(this.scope,r.body,this.crypto));
  const key=this.prefix+'retire-outbox:'+r.body.request_id;need(this.session.getItem(this.prefix+'retirement')===key&&same(this._readRetirement(key),r));
 }
 _linkRetirement(r){
  this.active();need(!this.retirement||same(this.retirement,r),'本页还有另一项停止操作待核对，没有覆盖原内容。');
  const key=this.prefix+'retire-outbox:'+r.body.request_id;need(same(this._readRetirement(key,r.window_id),r));
  this._write(this.session,this.prefix+'retirement-origin-window',r.window_id);this._write(this.session,this.prefix+'retirement',key);
  this.retirement=this._readRetirement(key);this.retirementNotFound=false;this.notify();
 }
 async restoreRecovery(candidate){return this._run(async()=>{
  const selected=readGraphSourceRecoveryCandidate({storage:this.storage,scope:this.scope,candidate}),record=selected.record;
  if(selected.kind==='draft'){
   const restored=formValue(record.form);need(this.persistForm(),'当前输入尚未保存，未替换本页人稿。');
   this.active();this.formOwner=this.crypto.randomUUID();this.formGeneration=0;this.entryForm=formValue(restored);this.form=restored;this.preview=null;
   need(this.persistForm(),'已保留当前人稿，所选稿仍在本页；请恢复存储后重试保存。');this.notice='已恢复所选本机稿，进入前的人稿另行保留；没有自动登记或采用。';return this.form;
  }
  need(this.persistForm(),'当前输入尚未保存，未替换原请求核对入口。');
  if(selected.kind==='request'){
   const body=record.body||JSON.parse(record.exact_body),p=await this._sourceOutbox(body.request_id,record.window_id);
   need(p.command_hash===record.command_hash&&(record.exact_body===p.exact_body)&&(!record.kind||record.kind===p.kind));
   readGraphSourceRecoveryCandidate({storage:this.storage,scope:this.scope,candidate});this._linkPending(p);return this._lookupSource(p);
  }
  need(selected.kind==='retirement');const body=record.body||JSON.parse(record.exact_body),key=this.prefix+'retire-outbox:'+body.request_id;
  const r=this._readRetirement(key,record.window_id);need(r.exact_body===record.exact_body&&r.command_hash===record.command_hash&&r.command_hash===await graphSourceRetirementHash(this.scope,r.body,this.crypto));
  const p=await this._sourceOutbox(r.body.original.request_id,r.original_window_id);need(p.kind===r.body.kind&&same(p.body,r.body.original));
  readGraphSourceRecoveryCandidate({storage:this.storage,scope:this.scope,candidate});this._linkPending(p);this._linkRetirement(r);return this._lookupRetirement(r);
 });}
 async _checkedRetirementReceipt(receipt,p,r=null){
  need(exact(receipt,'id,request_id,actor_id,kind,original_request_id,original_command_hash,command_hash,command,result')&&UUID.test(receipt.id)&&UUID.test(receipt.request_id)
   &&receipt.actor_id===this.scope.actor_id&&receipt.kind===p.kind&&receipt.original_request_id===p.body.request_id&&receipt.original_command_hash===p.command_hash
   &&SHA.test(receipt.command_hash)&&exact(receipt.result,'outcome,kind,original_request_id,original_command_hash')&&receipt.result.outcome==='retired'
   &&receipt.result.kind===p.kind&&receipt.result.original_request_id===p.body.request_id&&receipt.result.original_command_hash===p.command_hash);
  retirementCommand(receipt.command);need(receipt.command.request_id===receipt.request_id&&receipt.command.kind===p.kind&&same(receipt.command.original,p.body)
   &&receipt.command_hash===await graphSourceRetirementHash(this.scope,receipt.command,this.crypto));
  if(r)need(same(receipt.command,r.body)&&receipt.command_hash===r.command_hash&&receipt.request_id===r.body.request_id);return receipt;
 }
 async _clearRetirement(r){
  await this._validateRetirement(r);this.session.removeItem(this.prefix+'retirement');need(this.session.getItem(this.prefix+'retirement')===null);
  this.session.removeItem(this.prefix+'retirement-origin-window');this.retirement=null;this.retirementNotFound=false;
 }
 async _finishRetiredSource(p,receipt){
  await this._validatePending(p);await this._checkedRetirementReceipt(receipt,p);need(this.storage.getItem(this.prefix+'completed:'+p.body.request_id)===null);
  this._completion(this.prefix+'retired:'+p.body.request_id,{schema:'graph-source-retired-v123',scope:clone(this.scope),window_id:p.window_id,kind:p.kind,command_hash:p.command_hash,exact_body:p.exact_body,retirement:receipt});
  this.active();need(this.session.getItem(this.prefix+'pending')===this.prefix+'outbox:'+p.body.request_id);this.session.removeItem(this.prefix+'pending');need(this.session.getItem(this.prefix+'pending')===null);
  this.session.removeItem(this.prefix+'pending-origin-window');this.pending=null;this.pendingNotFound=false;this.preview=null;
  this.notice=p.kind==='register'?'这次未完成的登记操作已停止，原稿和记录保留；没有据此新增结果来源。':'这次未完成的停止来源操作已结束，原稿和记录保留；来源的当前状态需另行核对。';
 }
 async _finishRetirement(r,receipt,p){
  await this._validateRetirement(r);await this._checkedRetirementReceipt(receipt,p,r);
  this._completion(this.prefix+'retire-completed:'+r.body.request_id,{schema:'graph-source-retirement-completed-v123',scope:clone(this.scope),window_id:r.window_id,command_hash:r.command_hash,exact_body:r.exact_body,receipt});
  await this._finishRetiredSource(p,receipt);await this._clearRetirement(r);return receipt;
 }
 async _lookupSource(p){
  this.pendingNotFound=false;await this._validatePending(p);
  const value=await this.request(BASE+'/command-requests/'+p.kind+'/'+encodeURIComponent(p.body.request_id));
  need(exact(value,'schema,kind,request_id,outcome,command_hash,saved,retirement')&&value.schema==='graph-event-source-request-state-v1'&&value.kind===p.kind&&value.request_id===p.body.request_id&&['not_found','saved','retired'].includes(value.outcome));
  if(value.outcome==='not_found'){
   need(value.command_hash===null&&value.saved===null&&value.retirement===null);this.pendingNotFound=true;this.notice='尚未找到原操作。可继续核对、按原内容重试，或明确停止这次未完成的操作。';return value;
  }
  need(value.command_hash===p.command_hash);const r=this.retirement;
  if(value.outcome==='saved'){
   need(value.retirement===null);const saved=p.kind==='register'?await this._registered(value.saved,p):this._revoked(value.saved,p);await this._finish(p,saved);
   if(r&&r.body.kind===p.kind&&same(r.body.original,p.body)){await this._clearRetirement(r);this.notice+=' 原操作已经保存，这次未完成操作不能再停止；没有记录停止成功。';}
   return value;
  }
  need(value.saved===null);const receipt=await this._checkedRetirementReceipt(value.retirement,p);
  if(r&&same(r.body,receipt.command))await this._finishRetirement(r,receipt,p);
  else{await this._finishRetiredSource(p,receipt);if(r&&r.body.kind===p.kind&&same(r.body.original,p.body))await this._clearRetirement(r);}
  return value;
 }
 setRetirementReason(reason){need(this.pending);this.edit({reasons:{...this.form.reasons,[this.pending.body.request_id]:reason}});}
 retireOriginal(){return this._run(async()=>{
  const p=this.pending;need(p&&this.pendingNotFound&&!this.retirement&&this.persistForm(),'请先核对原操作确实尚未找到，再说明停止原因。');await this._validatePending(p);
  const reason=(this.form.reasons[p.body.request_id]||'').trim();need(text(reason),'请说明停止这次未完成操作的原因。');
  const body={schema:'graph-event-source-retirement-v1',request_id:this.crypto.randomUUID(),kind:p.kind,original:clone(p.body),reason};
  const r={schema:'graph-source-retirement-outbox-v123',scope:clone(this.scope),window_id:this.windowID,body,exact_body:JSON.stringify(body),command_hash:await graphSourceRetirementHash(this.scope,body,this.crypto),original_window_id:p.window_id};
  this.active();const key=this.prefix+'retire-outbox:'+body.request_id;this._once(key,r);this._linkRetirement(r);return this._sendRetirement(r,p);
 });}
 async _retirementSource(r){
  const p=await this._sourceOutbox(r.body.original.request_id,r.original_window_id);need(p.kind===r.body.kind&&same(p.body,r.body.original));this._linkPending(p);return p;
 }
 async _sendRetirement(r,p){
  await this.identity();await this._validateRetirement(r);await this._validatePending(p);need(this.storage.getItem(this.prefix+'retire-completed:'+r.body.request_id)===null,'这次停止已有本机回执提示，请只核对原结果。');this.retirementNotFound=false;
  try{
   const value=await this.request(BASE+'/command-retirements',{method:'POST',body:r.exact_body});need(exact(value,'retirement,replayed')&&typeof value.replayed==='boolean');return this._finishRetirement(r,value.retirement,p);
  }catch(error){
   if(error?.status===409){const current=await this._lookupSource(p);if(current.outcome!=='not_found')return current;}
   throw error;
  }
 }
 async _lookupRetirement(r){
  this.retirementNotFound=false;await this._validateRetirement(r);const p=await this._retirementSource(r);
  const value=await this.request(BASE+'/command-retirements/requests/'+encodeURIComponent(r.body.request_id));need(exact(value,'found,retirement')&&typeof value.found==='boolean');
  if(value.found)return this._finishRetirement(r,value.retirement,p);
  need(value.retirement===null);const original=await this._lookupSource(p);if(original.outcome==='not_found'){this.retirementNotFound=true;this.notice='尚未找到这次停止或原操作，可继续核对，或明确按原内容重试停止；不会改发另一项操作。';}return original;
 }
 recoverRetirement(){return this._run(async()=>{need(this.retirement);return this._lookupRetirement(this.retirement);});}
 resendRetirement(){return this._run(async()=>{need(this.retirement&&this.retirementNotFound,'请先核对这次停止和原操作确实尚未找到。');const r=this.retirement,p=await this._retirementSource(r);return this._sendRetirement(r,p);});}
}
