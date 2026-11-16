import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {graphWireRequestJSON,graphWireCanonical} from './full-semantic-graph-wire.mjs';
import {GRAPH_ROUTE_MAX_RECORD,graphRouteScope,graphRouteBase,graphRoutePrefix,graphRouteUUID,graphRouteEqual,graphRouteNeed,emptyGraphRouteForm,checkGraphRouteForm,routeInputDraft,buildGraphRoutePreview,graphRouteEnableCommand,graphRouteRecord,checkGraphRouteRecord,checkGraphRouteOptions,checkGraphRoutePreview,checkGraphRouteListing,checkGraphRouteSaved} from './graph-signal-routing-v134.mjs';

const clone=value=>JSON.parse(JSON.stringify(value));
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const meaningful=form=>form.source_id||form.extractor_id||form.business_id||form.entity_ids.length||form.purpose||form.reason||form.pause_reason||Object.values(form.input_draft?.values||{}).some(value=>value.mode!=='unset'||value.raw);
const safeIssue=error=>error?.graphRouteSafe?error.message:'操作尚未核对，原输入与请求仍保留；请读取原回执或重新核对当前范围。';
const local=callback=>{try{return callback();}catch(error){if(error instanceof Error&&typeof error.message==='string'&&error.message.length<=2000)error.graphRouteSafe=true;throw error;}};

export class GraphSignalRoutingControllerV134{
 constructor({api,scope,account,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{}}){
  Object.assign(this,{api,scope:graphRouteScope(scope),account,storage,session,locks,crypto,onChange});
  this.base=graphRouteBase(this.scope);this.prefix=graphRoutePrefix(this.scope);this.pendingKey=this.prefix+'pending';this.pointerKey=this.prefix+'working';
  let scoped=null;this.send=(path,options)=>{if(!scoped)scoped=scopedPreparationRequester({user_id:this.scope.actor_id,workspace_id:this.scope.workspace_id},api,session);return scoped(path,options);};
  this.form=emptyGraphRouteForm();this.owner=crypto.randomUUID();this.generation=0;this.slotRaw=new Map();
  this.options=null;this.listing=null;this.preview=null;this.pending=null;this.pendingNotFound=false;this.retained=[];this.completed=[];this.requests=[];this.localProblems=0;
  this.busy=false;this.localSaved=true;this.error='';this.notice='';this.identityStatus='unverified';this.identityEpoch=0;this.identityRequested=false;this.disposed=false;this.opened=false;this.corrupt=false;this.releaseID=null;this.enabled=true;
 }
 notify(){if(!this.disposed)this.onChange();}
 check(epoch=this.identityEpoch){graphRouteNeed(!this.disposed&&epoch===this.identityEpoch,'账号或页面核对范围已变化，原输入和原请求仍保留。');}
 async identity(epoch=this.identityEpoch,write=false){
  try{const me=await this.send('/me');this.check(epoch);
   graphRouteNeed(me.user_id===this.scope.actor_id&&me.workspace_id===this.scope.workspace_id&&me.role===this.account.role&&(!write||['admin','operator'].includes(me.role)),'当前登录或权限与本页不一致，原账号输入已隐藏并保留。');
   this.identityStatus='verified';return me;
  }catch(error){if(epoch===this.identityEpoch&&!this.disposed){this.identityEpoch++;this.identityStatus='error';this.error='当前账号与保存范围尚未核对，原输入和请求暂时隐藏；请重新核对登录。';this.notify();}throw error;}
 }
 async request(path,options={},epoch=this.identityEpoch){await this.identity(epoch,!!options.method&&options.method!=='GET');const value=await this.send(path,options);await this.identity(epoch);this.check(epoch);return value;}
 async action(callback){
  graphRouteNeed(!this.busy,'请先等待当前操作核对完成。');this.busy=true;this.error='';const epoch=this.identityEpoch;this.notify();
  try{return await callback(epoch);}catch(error){if(epoch===this.identityEpoch&&!this.disposed){this.error=safeIssue(error);if(error?.status===401||error?.status===403||/登录或权限/.test(error?.message||''))this.identityStatus='error';}throw error;}
  finally{this.busy=false;this.notify();if(this.identityRequested&&!this.disposed){this.identityRequested=false;void this.recheckIdentity().catch(()=>{});}}
 }
 async recheckIdentity(){
  this.identityEpoch++;this.identityStatus='checking';this.notify();
  if(this.busy){this.identityRequested=true;return;}
  const epoch=this.identityEpoch;
  try{await this.identity(epoch);if(!this.opened)this.openStorage();this.notify();}
  catch(error){if(epoch===this.identityEpoch){this.identityStatus='error';this.error='当前账号与保存范围尚未核对，原输入和请求暂时隐藏；请重新核对登录。';this.notify();}throw error;}
 }
 setEnvironment({releaseID,enabled=true}){if(this.releaseID!==releaseID||this.enabled!==enabled){this.releaseID=releaseID;this.enabled=enabled;this.preview=null;this.form={...this.form,confirmed:false};this.notify();}}
 writable(){return this.identityStatus==='verified'&&this.enabled&&['admin','operator'].includes(this.account.role);}
 write(key,value,expected=undefined,immutable=false){
  const raw=typeof value==='string'?value:JSON.stringify(value);graphRouteNeed(raw.length<=GRAPH_ROUTE_MAX_RECORD,'这份说明或模板超过本机可恢复大小；输入仍在页面，请缩短说明后重试保存。');
  const previous=this.storage.getItem(key);if(expected!==undefined)graphRouteNeed(previous===expected,'另一窗口已改变本机恢复记录，当前输入保留，未覆盖。');
  if(immutable&&previous!==null){graphRouteNeed(previous===raw,'原请求已经绑定另一份内容，未覆盖。');return raw;}
  this.storage.setItem(key,raw);graphRouteNeed(this.storage.getItem(key)===raw,'本机保存尚未读回确认，请勿离开；未发送新的授权。');return raw;
 }
 readForm(record){
  graphRouteNeed(object(record)&&Object.keys(record).sort().join('|')==='form|generation|owner|schema|scope'&&record.schema==='signal-graph-routing-form-v134'&&graphRouteEqual(record.scope,this.scope)&&graphRouteUUID(record.owner)&&Number.isSafeInteger(record.generation)&&record.generation>=1);
  checkGraphRouteForm(record.form);return record;
 }
 persist(){
  this.check();graphRouteNeed(!this.corrupt,'本机当前草稿记录无法核对；原记录保留，请先找回可核对的草稿。');
  this.localSaved=false;
  try{
   const record={schema:'signal-graph-routing-form-v134',scope:this.scope,owner:this.owner,generation:++this.generation,form:checkGraphRouteForm(this.form)},slot=this.generation%2,key=this.prefix+'form:'+this.owner+':'+slot;
   const raw=this.write(key,record,this.slotRaw.has(slot)?this.slotRaw.get(slot):null);this.slotRaw.set(slot,raw);
   // A copied session owns its complete snapshot, not a pointer to a mutable
   // slot in the other tab. Every activation forks a new local slot owner.
   this.session.setItem(this.pointerKey,raw);graphRouteNeed(this.session.getItem(this.pointerKey)===raw,'会话恢复记录未能保存，当前输入仍保留，请勿离开。');
   this.localSaved=true;return true;
  }catch(error){this.localSaved=false;this.error=error.graphRouteSafe?error.message:'本机保存空间暂不可用，当前输入仅在页面内存；请勿离开，恢复存储后重试保存。';throw error;}
  finally{this.notify();}
 }
 openStorage(){
  if(this.opened)return;
  const raw=this.session.getItem(this.pointerKey);
  this.opened=true;
  if(raw!==null){try{this.form=clone(this.readForm(JSON.parse(raw)).form);}catch{this.corrupt=true;this.localSaved=false;this.error='本机当前草稿无法核对，原记录没有覆盖；可找回下方独立保存的草稿。';}}
  if(!this.corrupt)try{this.persist();}catch{}
  this.refreshLocal();
 }
 refreshLocal(){
  this.check();const forms=new Map(),completed=[],requests=[];let problems=0;
  for(let index=0;index<this.storage.length;index++){
   const key=this.storage.key(index);if(!key?.startsWith(this.prefix))continue;
   try{
    if(key.startsWith(this.prefix+'form:')){const raw=this.storage.getItem(key);graphRouteNeed(raw.length<=GRAPH_ROUTE_MAX_RECORD);const record=this.readForm(JSON.parse(raw));if(!forms.has(record.owner)||forms.get(record.owner).generation<record.generation)forms.set(record.owner,{...record,key,raw});}
    if(key.startsWith(this.prefix+'completed:'))completed.push({key,record:JSON.parse(this.storage.getItem(key))});
    if(key.startsWith(this.prefix+'request:')){const raw=this.storage.getItem(key);graphRouteNeed(raw.length<=GRAPH_ROUTE_MAX_RECORD);const record=checkGraphRouteRecord(JSON.parse(raw),this.scope).record;graphRouteNeed(key===this.prefix+'request:'+record.body.request_id);requests.push({key,raw,record});}
   }catch{problems++;}
  }
  this.retained=[...forms.values()].filter(row=>row.owner!==this.owner&&meaningful(row.form));this.completed=completed;this.requests=requests;this.localProblems=problems;
  const pendingRaw=this.storage.getItem(this.pendingKey);
  this.pending=pendingRaw===null?null:checkGraphRouteRecord(JSON.parse(pendingRaw),this.scope).record;
  if(this.pending)graphRouteNeed(this.storage.getItem(this.prefix+'request:'+this.pending.body.request_id)===pendingRaw,'原请求指针与独立正文不一致，原记录保留。');
 }
 async open(){
  this.disposed=false;await this.recheckIdentity();return this.load();
 }
 async load(){return this.action(async epoch=>{
  this.preview=null;this.form={...this.form,confirmed:false};this.notify();
  await this.identity(epoch);if(!this.opened)this.openStorage();
  const [options,listing]=await Promise.all([this.request(this.base+'/options',{},epoch),this.request(this.base,{},epoch)]);this.check(epoch);
  const checkedOptions=await checkGraphRouteOptions(options,this.scope);this.check(epoch);const checkedListing=await checkGraphRouteListing(listing,this.scope);this.check(epoch);this.options=checkedOptions;this.listing=checkedListing;this.refreshLocal();this.notify();return this.listing;
 });}
 edit(key,value){graphRouteNeed(this.writable()&&!this.busy&&!this.corrupt,'请先核对登录、找回损坏前的草稿并等待当前请求结束。');graphRouteNeed(Object.hasOwn(this.form,key));this.form={...this.form,[key]:clone(value),confirmed:key==='confirmed'?value:false};if(key!=='confirmed')this.preview=null;try{this.persist();}catch{}this.notify();}
 rememberInput(){if(this.form.input_draft){graphRouteNeed(this.form.input_history.length<50,'本页已保存50份不同输入模板；原稿保留，请恢复已有模板或在独立页面继续。');this.form={...this.form,input_history:[...this.form.input_history,{business_id:this.form.business_id,input_draft:clone(this.form.input_draft),field_labels:clone(this.form.field_labels),entity_input_fields:[...this.form.entity_input_fields]}]};}}
 selectBusiness(identity){
  graphRouteNeed(this.writable()&&!this.busy&&!this.corrupt);const business=this.options?.businesses.find(row=>row.id===identity);graphRouteNeed(business?.available&&business.input_metadata,business?.reason||'请先读取当前可用业务。');this.rememberInput();
  const labels=Object.fromEntries(Object.entries(business.input_metadata.user_input_schema.properties).map(([key,spec])=>[key,spec.title||'业务输入']));
  this.form={...this.form,business_id:identity,entity_input_fields:[],input_draft:routeInputDraft(this.scope,business,[]),field_labels:labels,confirmed:false};this.preview=null;try{this.persist();}catch{}this.notify();
 }
 resetInput(){const business=this.options?.businesses.find(row=>row.id===this.form.business_id);graphRouteNeed(this.writable()&&!this.busy&&!this.corrupt&&business?.available);this.rememberInput();this.form={...this.form,input_draft:routeInputDraft(this.scope,business,this.form.entity_input_fields),confirmed:false};this.preview=null;try{this.persist();}catch{}this.notify();}
 restoreInput(index){graphRouteNeed(this.writable()&&!this.busy&&!this.corrupt);const row=this.form.input_history[index];graphRouteNeed(row);this.rememberInput();this.form={...this.form,business_id:row.business_id,entity_input_fields:[...row.entity_input_fields],input_draft:clone(row.input_draft),field_labels:clone(row.field_labels),confirmed:false};this.preview=null;try{this.persist();}catch{}this.notify();}
 setEntityFields(fields){
  graphRouteNeed(this.writable()&&!this.busy&&!this.corrupt);const business=this.options?.businesses.find(row=>row.id===this.form.business_id);graphRouteNeed(business,'请先选择当前完整流程。');this.rememberInput();
  this.form={...this.form,entity_input_fields:[...fields],input_draft:routeInputDraft(this.scope,business,fields,this.form.input_draft),confirmed:false};this.preview=null;try{this.persist();}catch{}this.notify();
 }
 restoreForm(candidate){
  graphRouteNeed(this.writable()&&!this.busy&&!this.pending,'请先核对原请求；恢复草稿不会替换未知授权。');
  const raw=this.storage.getItem(candidate.key);graphRouteNeed(raw===candidate.raw,'原草稿已有变化，请重新读取本机记录。');const record=this.readForm(JSON.parse(raw));
  if(this.corrupt){const old=this.session.getItem(this.pointerKey);if(old!==null)this.write(this.prefix+'unreadable:'+this.crypto.randomUUID(),old,undefined,true);}else this.persist();
  this.owner=this.crypto.randomUUID();this.slotRaw=new Map();this.generation=0;this.corrupt=false;this.form={...clone(record.form),confirmed:false};this.preview=null;this.persist();this.notice='已恢复原输入，当前稿与原页副本均保留；请重新预览，尚未启用。';this.refreshLocal();this.notify();
 }
 async makePreview(){return this.action(async epoch=>{
  graphRouteNeed(this.writable()&&!this.pending&&this.localSaved&&this.options&&this.options.can_enable&&this.options.release_id===this.releaseID,'请先保存本机输入、核对原请求并读取当前启用版本的范围。');this.persist();const generation=this.generation;
  const command=local(()=>buildGraphRoutePreview(this.form,this.options,this.scope)),exact=graphWireRequestJSON(command);
  const response=await this.request(this.base+'/preview',{method:'POST',body:exact},epoch);this.check(epoch);graphRouteNeed(this.generation===generation,'预览期间输入已有变化，当前稿保留，请重新预览。');
  const checked=await checkGraphRoutePreview(response,command,this.scope);this.check(epoch);graphRouteNeed(this.generation===generation&&this.options?.release_id===this.releaseID&&this.enabled,'预览期间版本或输入已有变化，原稿保留，请重新读取当前范围再预览。');this.preview=checked;this.form={...this.form,confirmed:false};this.persist();this.notify();return this.preview;
 });}
 async lock(callback){graphRouteNeed(typeof this.locks?.request==='function','浏览器缺少多窗口保存保护；仍可查看与保留人稿，不能发送新的授权。');return this.locks.request(this.prefix+'command',{mode:'exclusive'},callback);}
 stage(action,routeID,body){const record=graphRouteRecord(this.scope,action,routeID,body),raw=JSON.stringify(record);graphRouteNeed(this.storage.getItem(this.pendingKey)===null,'另一窗口已有原请求，未建立替代编号。');this.write(this.prefix+'request:'+body.request_id,raw,undefined,true);this.write(this.pendingKey,raw,null);this.pending=record;this.pendingNotFound=false;this.notify();return record;}
 async enable(){return this.action(async epoch=>this.lock(async()=>{
  await this.identity(epoch,true);graphRouteNeed(this.writable()&&this.localSaved&&this.form.confirmed&&this.preview&&this.options?.release_id===this.releaseID,'请先保存输入、预览完整范围并明确同意。');this.refreshLocal();graphRouteNeed(!this.pending,'原请求仍待核对，不能另起编号。');
  const fresh=buildGraphRoutePreview(this.form,this.options,this.scope);graphRouteNeed(graphRouteEqual(fresh,this.preview.original_command),'本页输入或当前范围已有变化，请重新预览。');this.persist();
  const body=graphRouteEnableCommand(this.preview,this.crypto.randomUUID()),record=this.stage('enable',null,body);return this.sendOriginal(record,epoch);
 }));}
 async pause(route){return this.action(async epoch=>this.lock(async()=>{
  await this.identity(epoch,true);graphRouteNeed(this.writable()&&this.localSaved,'请先保存暂停说明并核对当前账号。');this.refreshLocal();graphRouteNeed(!this.pending&&this.listing,'请先核对原请求并读取当前授权。');this.persist();
  const body={schema:'signal-graph-routing-pause-command-v134',request_id:this.crypto.randomUUID(),expected_version:this.listing.instance_version,reason:this.form.pause_reason};
  const record=this.stage('pause',route.id,body);return this.sendOriginal(record,epoch);
 }));}
 original(){this.refreshLocal();graphRouteNeed(this.pending,'没有待核对的原授权请求。');return this.pending;}
 async restoreRequest(candidate){return this.action(async epoch=>this.lock(async()=>{await this.identity(epoch);graphRouteNeed(this.storage.getItem(candidate.key)===candidate.raw,'原请求记录已有变化，请重新读取本机历史。');const record=checkGraphRouteRecord(JSON.parse(candidate.raw),this.scope).record;graphRouteNeed(candidate.key===this.prefix+'request:'+record.body.request_id);this.refreshLocal();graphRouteNeed(!this.pending||JSON.stringify(this.pending)===candidate.raw,'另一个原请求尚待核对，不能替换。');if(!this.pending)this.write(this.pendingKey,candidate.raw,null);this.pending=record;this.pendingNotFound=false;return this.lookup(record,epoch);}));}
 async lookup(record,epoch){
  const response=await this.request(this.base+'/requests/'+record.body.request_id,{},epoch);
  const saved=await checkGraphRouteSaved(response,record,this.scope);this.check(epoch);
  if(saved===null){this.pendingNotFound=true;this.notice='服务器暂未查到原请求；原编号和内容保留，不会自动重发。若原范围已变化，重试也可能被拒绝；请保留记录核对，不能用另一编号替代未知操作。';this.notify();return {found:false};}
  const completed={schema:'signal-graph-routing-completed-v134',scope:this.scope,action:record.action,route_id:record.route_id,exact_body:record.exact_body,receipt_canonical:graphWireCanonical(saved.receipt)};
  const key=this.prefix+'completed:'+record.body.request_id,existing=this.storage.getItem(key);if(existing!==null)graphRouteNeed(graphRouteEqual(JSON.parse(existing),completed),'本机已保存回执与原请求不一致，原记录保留。');else this.write(key,completed,undefined,true);
  const raw=this.storage.getItem(this.pendingKey);if(raw!==null){graphRouteNeed(raw===JSON.stringify(record),'另一窗口已有不同原请求，未清除。');this.storage.removeItem(this.pendingKey);graphRouteNeed(this.storage.getItem(this.pendingKey)===null);}
  this.pending=null;this.pendingNotFound=false;this.preview=null;this.form={...this.form,confirmed:false};this.listing=saved;this.notice=record.action==='enable'?'原完整流程授权已保存；当前是否仍有效请看下方授权状态，新异常仍须实际交接与执行核对。':'原暂停决定已保存；新的发起与原任务后续推进受该限制，已有输入和结果保留。';this.refreshLocal();this.notify();
  // The immutable receipt has already settled. Read-refresh failure must never
  // change it to unknown or issue the mutation a second time.
  try{const [rawOptions,rawList]=await Promise.all([this.request(this.base+'/options',{},epoch),this.request(this.base,{},epoch)]);const options=await checkGraphRouteOptions(rawOptions,this.scope),listing=await checkGraphRouteListing(rawList,this.scope);this.check(epoch);this.options=options;this.listing=listing;}catch{this.check(epoch);this.error='原操作已保存，当前授权状态暂未刷新；请重新读取，不会重发已保存操作。';}this.notify();return {found:true,response:saved};
 }
 async sendOriginal(record,epoch){
  const checked=checkGraphRouteRecord(record,this.scope),found=await this.lookup(record,epoch);if(found.found)return found;
  await this.identity(epoch,true);this.check(epoch);graphRouteNeed(this.writable()&&JSON.stringify(this.original())===JSON.stringify(record));
  const path=record.action==='enable'?this.base+'/enable':this.base+'/'+record.route_id+'/pause';
  this.pendingNotFound=false;this.notify();
  try{await this.request(path,{method:'POST',body:record.exact_body},epoch);}catch(error){this.notice='原请求尚未核对，请保留原编号并只读取原回执；不会自动改内容或重复发起。';throw error;}
  return this.lookup(record,epoch);
 }
 async recover(){return this.action(async epoch=>this.lock(async()=>this.lookup(this.original(),epoch)));}
 async resend(){return this.action(async epoch=>this.lock(async()=>{graphRouteNeed(this.pendingNotFound,'请先读取原请求，明确未找到后才能按原内容重发。');return this.sendOriginal(this.original(),epoch);}));}
 guard(){try{if(!this.localSaved)this.persist();return this.localSaved;}catch{return false;}}
 dispose(){this.disposed=true;this.identityEpoch++;}
}
export const createGraphSignalRoutingController=options=>new GraphSignalRoutingControllerV134(options);
