import {verifyGraphWire,graphWireCanonical} from './full-semantic-graph-wire.mjs';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {NativeGraphSourceJournalV136,nativeSourceScopeV136} from './native-graph-sources-journal-v136.mjs';
import {emptyNativeSourceFormV136,nativeSourceFormV136,nativeSourceOutboxV136,nativeSourceCommandV136,nativeSourceCommandHashV136,nativeSourceTrimV136,checkNativeSourceOptionsV136,checkNativeSourceProducerV136,checkNativeSourcePreviewV136,checkNativeSourceReceiptV136,checkNativeSourceStateV136} from './native-graph-sources-v136.mjs';
import {enumerateNativeSourceRecoveryV136,readNativeSourceRecoveryV136} from './native-graph-sources-recovery-v136.mjs';
const need=(value,message='原输入和原请求保留，请重新核对后继续。')=>{if(!value)throw Error(message);};
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===keys.split(',').sort().join(',');
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const blankPage=()=>({items:[],loaded:false,next_after:null});
export class NativeGraphSourcesControllerV136{
 constructor({api,account,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{}}){
  this.scope=nativeSourceScopeV136(scope);need(account?.user_id===scope.actor_id&&account?.workspace_id===scope.workspace_id&&typeof account.role==='string');
  this.request=scopedPreparationRequester(account,api,session);this.role=account.role;this.crypto=crypto;this.onChange=onChange;
  this.base='/workspace/instances/'+scope.instance_id+'/graph-event-native-producers';
  this.journal=new NativeGraphSourceJournalV136({scope,storage,session,locks,crypto,checkForm:nativeSourceFormV136,checkRequest:nativeSourceOutboxV136});
  this.active=true;this.suspended=false;this.identityEpoch=0;this.operationEpoch=null;this.identityStatus='checking';this.busy=false;this.opened=false;
  this.form=emptyNativeSourceFormV136();this.localSaved=true;this.corrupt=false;this.preview=null;this.detail=null;
  this.pendingRecord=null;this.retirementRecord=null;this.pendingNotFound=false;this.retirementNotFound=false;
  this.options=null;this.registrations=blankPage();this.error='';this.notice='';this.recovery={loaded:false,items:[],problem_count:0,next_offset:null};
 }
 get pending(){return this.pendingRecord?.value||null;} get retirement(){return this.retirementRecord?.value||null;}
 get canRegister(){return this.options?.can_register===true&&this.role==='admin';}
 get protectedWork(){return this.busy||!this.localSaved;}
 changed(){if(this.active&&!this.suspended)this.onChange(this);}
 current(epoch=this.operationEpoch??this.identityEpoch){need(this.active&&!this.suspended&&epoch===this.identityEpoch,'页面身份或实例范围已变化，原稿与请求仍在原范围保留。');}
 dispose(){this.active=false;this.suspended=true;this.identityEpoch++;this.onChange=()=>{};}
 conceal(){if(!this.active)return;this.identityEpoch++;this.identityStatus='concealed';this.changed();}
 async authorize(epoch=this.operationEpoch??this.identityEpoch){
  this.current(epoch);
  try{const me=await this.request('/me');this.current(epoch);need(me?.user_id===this.scope.actor_id&&me?.workspace_id===this.scope.workspace_id&&me?.role===this.role,'当前登录账号、工作区或权限已变化；原记录保留，未提交。');return me;}
  catch(error){if(this.active&&epoch===this.identityEpoch){this.identityStatus='concealed';this.changed();}throw error;}
 }
 async http(path,options={}){
  const epoch=this.operationEpoch??this.identityEpoch;await this.authorize(epoch);this.current(epoch);
  let result;try{result=await this.request(path,options);}catch(error){if(this.active&&epoch===this.identityEpoch&&[401,403].includes(error.status)){this.identityStatus='concealed';this.changed();}throw error;}
  this.current(epoch);await this.authorize(epoch);this.current(epoch);return result;
 }
 async wire(path,options={}){const epoch=this.operationEpoch??this.identityEpoch,value=await verifyGraphWire(await this.http(path,options));this.current(epoch);return value;}
 async operation(callback){
  need(!this.busy,'另一操作仍在核对，原稿保留，请稍后继续。');this.current();this.operationEpoch=this.identityEpoch;this.busy=true;this.error='';this.changed();
  try{return await callback();}catch(error){if(this.active){this.error=error.message||'操作未确认，原请求保留。';this.changed();}throw error;}
  finally{if(this.active){this.operationEpoch=null;this.busy=false;this.changed();}}
 }
 async open(){return this.recheckIdentity();}
 async recheckIdentity(){
  this.conceal();const epoch=this.identityEpoch;
  try{
   await this.authorize(epoch);this.current(epoch);
   if(!this.opened){
    try{const restored=this.journal.open(emptyNativeSourceFormV136());this.form=restored.form;this.pendingRecord=restored.pending;this.retirementRecord=restored.retirement;this.localSaved=this.journal.tabReady;this.error=this.journal.error;this.opened=true;}
    catch(error){this.corrupt=true;this.error=error.message;throw error;}
   }
   this.current(epoch);this.identityStatus='verified';this.changed();
   if(!this.options&&!this.busy)await this.refresh();return true;
  }catch(error){if(this.active&&epoch===this.identityEpoch){this.identityStatus='concealed';this.error=error.message;this.changed();}throw error;}
 }
 editable(){this.current();need(this.identityStatus==='verified'&&!this.corrupt&&!this.busy,'请先核对当前登录或等待原操作结果。');}
 saveLocal(){
  this.current();need(this.identityStatus==='verified'&&!this.corrupt);this.localSaved=false;
  try{this.journal.save(this.form);this.localSaved=true;this.error='';}catch(error){this.error=error.message;}this.changed();return this.localSaved;
 }
 edit(patch){this.editable();const next=nativeSourceFormV136({...this.form,...patch});this.form=next;this.preview=null;this.saveLocal();}
 choose(kind,id){
  this.editable();need(['source','installation'].includes(kind));const row=(kind==='source'?this.options?.sources:this.options?.installations)?.find(item=>item.id===id);
  need(row,'请选择已读取的实际来源或本地模型，原选择保留。');this.edit({[kind]:row});
 }
 setReason(id,reason){this.edit({reasons:{...this.form.reasons,[id]:reason}});}
 async _options(after=null,append=false){
  const value=await this.wire(this.base+'/options?limit=20'+(after?'&after='+after:''));await checkNativeSourceOptionsV136(value,this.scope);this.current();
  if(append){need(after&&after===this.options?.next_after&&value.next_after!==after);const old=this.options.sources;need(!value.sources.some(row=>old.some(prior=>prior.id===row.id)));this.options={...value,sources:[...old,...value.sources]};}
  else this.options=value;
 }
 async _registrations(after=null,append=false){
  const value=await this.wire(this.base+'?limit=20'+(after?'&after='+after:''));
  need(exact(value,'schema,instance_id,items,next_after,can_register')&&value.schema==='graph-event-native-audio-producer-page-v1'&&value.instance_id===this.scope.instance_id&&Array.isArray(value.items)&&value.items.length<=20&&(value.next_after===null||uuid.test(value.next_after))&&typeof value.can_register==='boolean');
  need(new Set(value.items.map(row=>row.id)).size===value.items.length);for(const row of value.items)await checkNativeSourceProducerV136(row,this.scope,{catalog:true});this.current();
  if(append){need(after===this.registrations.next_after&&value.next_after!==after&&!value.items.some(row=>this.registrations.items.some(prior=>prior.id===row.id)));}
  this.registrations={loaded:true,items:append?[...this.registrations.items,...value.items]:value.items,next_after:value.next_after};
 }
 async refresh(){return this.operation(async()=>{
  const outcomes=await Promise.allSettled([this._options(),this._registrations()]);this.current();
  const failed=outcomes.filter(item=>item.status==='rejected');if(failed.length)this.error=failed.map(item=>item.reason.message).join('；');
  if(this.form.selected_registration)try{await this._detail(this.form.selected_registration);}catch(error){this.error=error.message;}
  this.changed();
 });}
 async more(kind){return this.operation(async()=>{if(kind==='sources'){need(this.options?.next_after);await this._options(this.options.next_after,true);}else{need(kind==='registrations'&&this.registrations.next_after);await this._registrations(this.registrations.next_after,true);}this.changed();});}
 async _detail(id){need(uuid.test(id));const value=await this.wire(this.base+'/registrations/'+id);await checkNativeSourceProducerV136(value,this.scope,{catalog:true});need(value.id===id);this.current();this.detail=value;}
 async selectRegistration(id){this.edit({selected_registration:id});return this.operation(async()=>{await this._detail(id);this.changed();});}
 freshBody(){
  need(this.canRegister&&this.localSaved&&this.journal.canSend()&&!this.pending&&!this.retirement,'请先保存本页输入并核对原操作，暂不能新增来源。');
  const source=this.form.source,installation=this.form.installation;
  need(this.options.sources.some(row=>row.id===source?.id&&row.content_hash===source.content_hash)&&this.options.installations.some(row=>row.id===installation?.id&&row.installation_hash===installation.installation_hash),'原选来源或本地模型当前不可用；原选择保留，请明确重选。');
  return nativeSourceCommandV136({schema:'graph-event-native-audio-producer-command-v1',request_id:this.crypto.randomUUID(),label:nativeSourceTrimV136(this.form.label),adapter_id:'native-audio-transcription-v1',instance_id:this.scope.instance_id,source_id:source.id,source_hash:source.content_hash,installation_id:installation.id,installation_hash:installation.installation_hash},'register',this.scope);
 }
 async previewRegistration(){return this.operation(async()=>{
  const body=this.freshBody();const value=await this.journal.lock(async()=>{await this.authorize();return this.wire(this.base+'/preview',{method:'POST',body:graphWireCanonical(body)});});
  await checkNativeSourcePreviewV136(value,body,this.scope);this.current();this.preview={body,value};this.changed();return value;
 });}
 async outbox(kind,body,previewHash=null){
  const value={schema:'native-graph-source-outbox-v136',scope:this.scope,window_id:this.journal.windowID,kind,body,exact_body:graphWireCanonical(body),command_hash:await nativeSourceCommandHashV136(this.scope,kind,body),preview_contract_hash:previewHash};nativeSourceOutboxV136(value,this.scope);return value;
 }
 async _originalState(record){
  const pending=record.value;nativeSourceOutboxV136(pending,this.scope);need(await nativeSourceCommandHashV136(this.scope,pending.kind,pending.body)===pending.command_hash);
  const value=await this.wire(this.base+'/command-requests/'+pending.kind+'/'+pending.body.request_id);await checkNativeSourceStateV136(value,pending,this.scope);this.current();return value;
 }
 async _settleOriginal(record,state){
  need(state.outcome!=='not_found');const receipt=state.outcome==='saved'?state.saved:state.retirement,hint={outcome:state.outcome,server_id:receipt.id};
  if(this.journal.canSend()){
   if(this.retirementRecord&&this.retirementRecord.value.body.original.request_id===record.value.body.request_id)await this.journal.originalWon(record,this.retirementRecord,hint,()=>this.authorize());
   else await this.journal.settle(record,hint,()=>this.authorize());
   this.current();this.pendingRecord=this.journal.head('active');this.retirementRecord=this.journal.head('retiring');
  }
  this.pendingNotFound=false;this.retirementNotFound=false;this.notice=state.outcome==='saved'?'服务器已核实原操作保存。登记不代表资料已接纳或等待已匹配。':'服务器已核实原操作安全结束，原稿保留。';
  if(!this.journal.canSend())this.notice+=' 本窗口无法保存结清标记，原请求仍保留，下次仍可核对。';this.changed();return state;
 }
 async _sendOriginal(record){
  const state=await this.journal.withOriginal(record,()=>this.authorize(),async pending=>{
   const before=await this._originalState(record);if(before.outcome!=='not_found')return before;
   const path=pending.kind==='register'?this.base:this.base+'/revocations',reply=await this.wire(path,{method:'POST',body:pending.exact_body});
   need(exact(reply,pending.kind==='register'?'producer,created':'receipt,replayed')&&typeof reply[pending.kind==='register'?'created':'replayed']==='boolean');
   await checkNativeSourceReceiptV136(reply[pending.kind==='register'?'producer':'receipt'],pending,this.scope);
   return this._originalState(record);
  });need(state.outcome!=='not_found','服务器尚未确认原操作，完整请求保留，请只核对原结果。');return this._settleOriginal(record,state);
 }
 async register(){return this.operation(async()=>{
  need(this.preview&&this.localSaved&&!this.pending&&!this.retirement&&this.canRegister);const body=this.preview.body;
  await checkNativeSourcePreviewV136(this.preview.value,body,this.scope);const value=await this.outbox('register',body,this.preview.value.contract_hash);
  this.pendingRecord=await this.journal.install(value,()=>this.authorize());this.pendingNotFound=false;this.changed();return this._sendOriginal(this.pendingRecord);
 });}
 async revoke(){return this.operation(async()=>{
  need(this.detail?.status==='active'&&this.canRegister&&this.localSaved&&!this.pending&&!this.retirement);
  const row=this.detail,body={schema:'graph-event-native-audio-producer-revocation-v1',request_id:this.crypto.randomUUID(),instance_id:this.scope.instance_id,producer_id:row.id,producer_version:row.version,contract_hash:row.contract_hash,reason:nativeSourceTrimV136(this.form.reasons[row.id]||'')};
  nativeSourceCommandV136(body,'revoke',this.scope);this.pendingRecord=await this.journal.install(await this.outbox('revoke',body),()=>this.authorize());this.pendingNotFound=false;this.changed();return this._sendOriginal(this.pendingRecord);
 });}
 async recover(){return this.operation(async()=>{need(this.pendingRecord);const record=this.pendingRecord,state=await this._originalState(record);if(state.outcome==='not_found'){this.pendingNotFound=true;this.notice='尚未找到这一次原操作；这不证明请求以后不会到达。原内容保留，可明确重试或结束。';this.changed();return state;}return this._settleOriginal(record,state);});}
 async resend(){return this.operation(async()=>{need(this.pendingRecord&&this.pendingNotFound&&!this.retirement&&this.localSaved);return this._sendOriginal(this.pendingRecord);});}
 originalForRetirement(record){const body=record.value.body.original,original=this.journal.readRequest(this.journal.requestKey(body.request_id));need(graphWireCanonical(original.value.body)===graphWireCanonical(body));return original;}
 async _retirementState(record){
  const value=await this.wire(this.base+'/command-retirements/requests/'+record.value.body.request_id);need(exact(value,'found,retirement')&&typeof value.found==='boolean');
  if(value.found){await checkNativeSourceReceiptV136(value.retirement,record.value,this.scope);return {found:true,retirement:value.retirement};}
  need(value.retirement===null);const original=this.originalForRetirement(record),state=await this._originalState(original);return {found:false,original,state};
 }
 async _settleRetirement(record,result){
  if(result.found){
   if(this.journal.canSend()){await this.journal.settle(record,{outcome:'retired',server_id:result.retirement.id},()=>this.authorize());this.current();this.pendingRecord=this.journal.head('active');this.retirementRecord=this.journal.head('retiring');}
   this.retirementNotFound=false;this.pendingNotFound=false;this.notice='服务器已核实结束这一次未确认操作。已保存的资料与原稿保留。';this.changed();return result;
  }
  if(result.state.outcome!=='not_found')return this._settleOriginal(result.original,result.state);
  this.retirementNotFound=true;this.notice='尚未找到这次结束操作的回执；原操作与结束请求均保留，不会自动发送。';this.changed();return result;
 }
 async _sendRetirement(record){
  const result=await this.journal.withOriginal(record,()=>this.authorize(),async pending=>{
   const before=await this._retirementState(record);if(before.found||before.state.outcome!=='not_found')return before;
   const reply=await this.wire(this.base+'/command-retirements',{method:'POST',body:pending.exact_body});need(exact(reply,'retirement,replayed')&&typeof reply.replayed==='boolean');
   await checkNativeSourceReceiptV136(reply.retirement,pending,this.scope);return this._retirementState(record);
  });return this._settleRetirement(record,result);
 }
 async retireOriginal(){return this.operation(async()=>{
  need(this.pending&&this.pendingNotFound&&!this.retirement&&this.localSaved);
  const original=this.pending,body={schema:'graph-event-native-audio-source-retirement-v1',request_id:this.crypto.randomUUID(),instance_id:this.scope.instance_id,kind:original.kind,original:original.body,reason:nativeSourceTrimV136(this.form.reasons[original.body.request_id]||'')};
  this.retirementRecord=await this.journal.install(await this.outbox('retirement',body),()=>this.authorize());this.retirementNotFound=false;this.changed();return this._sendRetirement(this.retirementRecord);
 });}
 async recoverRetirement(){return this.operation(async()=>{need(this.retirementRecord);const record=this.retirementRecord;return this._settleRetirement(record,await this._retirementState(record));});}
 async resendRetirement(){return this.operation(async()=>{need(this.retirementRecord&&this.retirementNotFound&&this.localSaved);return this._sendRetirement(this.retirementRecord);});}
 discoverRecovery(more=false){this.editable();this.recovery=enumerateNativeSourceRecoveryV136(this.journal.storage,this.scope,{offset:more?this.recovery.next_offset||0:0});this.changed();}
 async restoreRecovery(selected){
  this.editable();const record=readNativeSourceRecoveryV136(this.journal.storage,this.scope,selected);
  if(record.kind==='draft'){need(this.localSaved,'请先保存本页新输入，未覆盖原稿。');this.form=nativeSourceFormV136(record.value.form);this.preview=null;this.saveLocal();return;}
  return this.operation(async()=>{
   const value=record.value;need(await nativeSourceCommandHashV136(this.scope,value.kind,value.body)===value.command_hash);
   // Read-only recovery works even when WebLocks/session pointers are absent.
   const original={key:record.key,raw:record.raw,value};
   if(this.journal.canSend())await this.journal.adopt(original,()=>this.authorize());
   if(value.kind==='retirement'){this.retirementRecord=original;return this._settleRetirement(original,await this._retirementState(original));}
   this.pendingRecord=original;const state=await this._originalState(original);if(state.outcome!=='not_found')return this._settleOriginal(original,state);
   this.pendingNotFound=true;this.notice='原请求已接回，尚未找到保存结果；没有自动重发。';this.changed();
  });
 }
}
