import {json as runtimeJSON} from './runtime-api.js';
import {approvalScope,approvalPrefix,approvalNeed as need,approvalCopy as copy,approvalCanonical as canonical,approvalSHA,approvalUUID,checkedApprovalOriginal,checkedApprovalReceived,checkedApprovalBody,approvalFingerprintBody,makeApprovalOriginal,approvalPlanState,parseApprovalLocal,approvalParkRecord} from './supplement-approval-v245-protocol.mjs';
export class SupplementApprovalControllerV245{
 constructor({account,instanceId,api=runtimeJSON,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,scopeAvailable=()=>true,onChange=()=>{},onRow=()=>{},onSettled=()=>{}}){this.scope=approvalScope(account,instanceId);this.prefix=approvalPrefix(this.scope);Object.assign(this,{api,storage,session,locks,crypto,scopeAvailable,onChange,onRow,onSettled,role:account.role});this.token=session.getItem('ga-setup-csrf');this.active=true;this.suspended=false;this.epoch=0;this.started=false;this.loaded=false;this.busy=false;this.localSaved=false;this.authenticated=false;this.denied=false;this.pending=null;this.legacy=null;this.state='unread';this.error='';this.notice='';}
 view(){return {scope_key:this.prefix,pending:this.pending,legacy:this.legacy,parked:!!this.parked,state:this.state,error:this.error,notice:this.notice,busy:this.busy,loaded:this.loaded,localSaved:this.localSaved,authenticated:this.authenticated,denied:this.denied};}
 emit(){if(this.active&&!this.suspended)this.onChange(this.view());}
 check(epoch=this.epoch){need(this.active&&!this.suspended&&!this.denied&&epoch===this.epoch&&this.scopeAvailable(),'账号、工作区或实例已变化；原批准输入与申请保留。');if(!this.token||this.session.getItem('ga-setup-csrf')!==this.token||this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id){this.deny();throw Error('当前登录范围已变化，补采批准记录已隐藏，原申请保留。');}}
 deny(){this.denied=true;this.authenticated=false;this.emit();}
 headers(){return {'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.token};}
 async identity(epoch=this.epoch){this.check(epoch);try{const user=await this.api('/me',{cache:'no-store',headers:this.headers()});this.check(epoch);if(user.user_id!==this.scope.actor_id||user.workspace_id!==this.scope.workspace_id||user.role!==this.role){this.deny();throw Error('当前实际账号、工作区或权限已变化；已隐藏原批准范围。');}this.authenticated=true;return user;}catch(e){if([401,403].includes(e.status))this.deny();throw e;}}
 async request(path,options={}){const epoch=this.epoch;await this.identity(epoch);const value=await this.api(path,{...options,cache:'no-store',headers:{...options.headers,...this.headers()}});this.check(epoch);await this.identity(epoch);return value;}
 write(key,value){this.check();const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机原批准未完整读回，未发送或清除原申请。');return raw;}
 remove(key){this.storage.removeItem(key);need(this.storage.getItem(key)===null,'服务器原批准已保存，但本机等待记录未结清，请继续核对原号。');}
 original(record=this.pending){need(record);const raw=this.storage.getItem(this.prefix+'pending'),saved=checkedApprovalOriginal(parseApprovalLocal(raw),this.scope),immutable=this.storage.getItem(this.prefix+'original:'+saved.body.request_id);need(immutable!==null&&immutable===raw&&canonical(saved)===canonical(record),'原批准正文与独立原件不一致，未重发、清除或换号。');return saved;}
 async checkedOriginal(record=this.pending){const p=this.original(record);need(await approvalSHA(p.raw,this.crypto)===p.sha256&&await approvalSHA(canonical(approvalFingerprintBody(p.body)),this.crypto)===p.approval_fingerprint,'原批准正文、摘要或指纹不同，原件保留，未重发。');this.check();return p;}
 async start(legacy=this.legacySource??null){
  this.legacySource=legacy;
  try{
   await this.identity();this.error='';if(this.stageFailedBeforePost)this.pending=null;
   const raw=this.storage.getItem(this.prefix+'pending');if(raw!==null)this.pending=checkedApprovalOriginal(parseApprovalLocal(raw),this.scope);
   const originals=[];
   for(let i=0;i<this.storage.length;i++){
    const key=this.storage.key(i);if(!key?.startsWith(this.prefix+'original:'))continue;
    const p=checkedApprovalOriginal(parseApprovalLocal(this.storage.getItem(key)),this.scope),received=this.storage.getItem(this.prefix+'received:'+p.body.request_id);
    if(received===null)originals.push(p);else checkedApprovalReceived(parseApprovalLocal(received),p);
   }
   need(originals.length<=1&&(!this.pending||originals.every(p=>p.body.request_id===this.pending.body.request_id)),'还有不同原批准尚待核对，未另起批准。');
   if(!this.pending&&originals.length){this.pending=originals[0];this.write(this.prefix+'pending',this.pending);}
   if(this.pending){
    const p=await this.checkedOriginal();this.state='unknown';
    const parked=this.storage.getItem(this.prefix+'park:'+p.body.request_id);
    this.parked=parked!==null&&canonical(parseApprovalLocal(parked))===canonical(approvalParkRecord(p));
   }else{
    const old=typeof legacy==='function'?legacy():legacy;
    if(old){
     need(old.instanceId===this.scope.instance_id&&approvalUUID(old.id));
     const body=checkedApprovalBody(old.body),saved=this.storage.getItem(this.prefix+'legacy-received:'+body.request_id);
     if(saved!==null){const receipt=parseApprovalLocal(saved);need(receipt.schema==='supplement-legacy-approval-received-v245'&&canonical(receipt.scope)===canonical(this.scope)&&canonical(receipt.original)===canonical(old)&&receipt.approved_by===this.scope.actor_id&&receipt.approval_fingerprint===await approvalSHA(canonical(approvalFingerprintBody(body)),this.crypto)&&/^[a-f0-9]{64}$/.test(receipt.approved_hash));this.legacy=null;}
     else{this.legacy=copy(old);this.state='legacy_unverified';}
    }
   }
   this.loaded=true;this.localSaved=true;this.stageFailedBeforePost=false;
  }catch(e){this.localSaved=false;this.error=e.message;}finally{this.emit();}
 }
 async action(fn){this.check();need(!this.busy,'原批准正在核对，请等待。');this.busy=true;this.error='';this.notice='';this.emit();try{return await fn();}catch(e){if(this.active&&!this.suspended&&!this.denied)this.error=e.message;throw e;}finally{this.busy=false;this.emit();}}
 async lock(fn){need(this.locks?.request,'当前浏览器不能保护多窗口原批准；原输入保留，暂不提交。');return this.locks.request(this.prefix+'command',{mode:'exclusive'},async()=>{this.check();return fn();});}
 async stage(plan,form){need(this.loaded&&this.localSaved&&!this.pending&&!this.legacy&&this.role==='admin');await this.start();need(this.loaded&&this.localSaved&&!this.pending&&!this.legacy,'另一窗口已有原批准或独立原件，请恢复原号。');need(this.storage.getItem(this.prefix+'pending')===null,'另一窗口已有原批准，请恢复原号。');const current=await this.request('/supplement-plans/'+plan.id);need(current.id===plan.id&&current.instance_id===this.scope.instance_id&&current.version===plan.version&&current.proposal_hash===plan.proposal_hash,'原方案或版本已变化，选项保留，请重新核对。');const p=await makeApprovalOriginal(this.scope,current,form,this.crypto.randomUUID(),this.crypto);this.check();need(this.storage.getItem(this.prefix+'pending')===null);try{this.write(this.prefix+'original:'+p.body.request_id,p);this.write(this.prefix+'pending',p);}catch(e){this.pending=p;this.localSaved=false;this.stageFailedBeforePost=true;throw e;}this.pending=p;this.state='unknown';this.emit();return p;}
 async resolve(p,row){
  await this.checkedOriginal(p);need(row?.id===p.plan_id&&row.instance_id===this.scope.instance_id,'服务器返回了其他方案范围，原输入保留，未采用该记录。');
  const state=approvalPlanState(p,row,this.scope);this.state=state;this.onRow(copy(row));
  if(state!=='saved'){
   this.notice=state==='unknown'?'当前尚未看到原批准保存；旧请求仍可能稍后提交，原号与原文继续保留，不另起批准。':state==='other_request'?'当前方案记录了另一原批准；本次原申请仍保留，只读核对，不采用他人回执。':state==='terminal_unconfirmed'?'当前方案已停止或受阻，但未确认本次原批准结果；原申请保留，只读核对，不重发。':'原批准账号、指纹或范围不一致；原输入与原件保留，暂不采用回执或重发。';this.emit();return null;
  }
  need(await approvalSHA(canonical(row.approved),this.crypto)===row.approved_hash,'原批准内容与服务器保存摘要不符，原件保留。');
  await this.checkedOriginal(p);
  const received={schema:'supplement-approval-received-v245',scope:copy(this.scope),request_id:p.body.request_id,sha256:p.sha256,approval_fingerprint:p.approval_fingerprint,plan_id:p.plan_id,approved_by:row.approved_by,approved_hash:row.approved_hash};
  this.write(this.prefix+'received:'+p.body.request_id,received);
  const settled=this.storage.getItem(this.prefix+'settled:'+p.body.request_id);
  if(settled!==null)checkedApprovalReceived(parseApprovalLocal(settled),p);
  else{await this.onSettled(copy(row),copy(p));await this.checkedOriginal(p);this.write(this.prefix+'settled:'+p.body.request_id,received);}
  await this.checkedOriginal(p);this.remove(this.prefix+'pending');this.pending=null;this.legacy=null;this.parked=false;
  this.notice=['cancelled','blocked','failed'].includes(row.status)?'本次原批准已保存；当前方案已停止、受阻或失败，原回执保留，没有新增读取。':'已按原账号、原号、固定范围与指纹核对原批准；没有另建批准或重复读取。';this.emit();return row;
 }
 approve(plan,form){return this.action(()=>this.lock(async()=>{const p=await this.stage(plan,form);return this.resolve(p,await this.request('/supplement-plans/'+p.plan_id+'/approve',{method:'POST',body:p.raw}));}));}
 recover(retry=false){return this.action(()=>this.lock(async()=>{this.parked=false;if(this.legacy&&!this.pending)return this.recoverLegacy();const p=await this.checkedOriginal();const row=await this.request('/supplement-plans/'+p.plan_id);const found=await this.resolve(p,row);if(found||!retry)return found;need(this.state==='unknown'&&this.role==='admin','此原批准只能只读核对，不能重发或另建批准。');await this.checkedOriginal(p);return this.resolve(p,await this.request('/supplement-plans/'+p.plan_id+'/approve',{method:'POST',body:p.raw}));}));}
 park(){return this.action(()=>this.lock(async()=>{await this.identity();need(!this.legacy&&this.pending&&this.loaded&&this.localSaved);const p=await this.checkedOriginal();this.write(this.prefix+'park:'+p.body.request_id,approvalParkRecord(p));await this.checkedOriginal(p);this.parked=true;this.notice='原申请、原文与账号范围已在本机独立保留，允许稍后核对；批准结果仍未知，未取消、换号或新增读取。';this.emit();}));}
 async recoverLegacy(){const old=this.legacy;need(old&&old.instanceId===this.scope.instance_id&&typeof old.id==='string');const body=checkedApprovalBody(old.body),fingerprint=await approvalSHA(canonical(approvalFingerprintBody(body)),this.crypto),row=await this.request('/supplement-plans/'+old.id);need(row?.id===old.id&&row.instance_id===this.scope.instance_id);const checked={...copy(old),scope:copy(this.scope),approval_fingerprint:fingerprint};this.onRow(copy(row));const state=approvalPlanState(checked,row,this.scope);this.state=state;if(state!=='saved'){this.notice='旧记录缺少原账号及原文摘要证明；原件保留，只能读取原号结果，不补签为新批准或重发。';this.emit();return null;}need(await approvalSHA(canonical(row.approved),this.crypto)===row.approved_hash);this.write(this.prefix+'legacy-received:'+body.request_id,{schema:'supplement-legacy-approval-received-v245',scope:copy(this.scope),original:copy(old),approval_fingerprint:fingerprint,approved_by:row.approved_by,approved_hash:row.approved_hash});await this.onSettled(copy(row),copy(old));this.legacy=null;this.notice='旧原批准已由服务器同账号、原号与指纹证明保存；未补签授权或重新读取。';this.emit();return row;}
 guard(){try{this.check();if(!this.loaded||!this.localSaved||this.busy||this.legacy)return false;if(!this.pending)return this.storage.getItem(this.prefix+'pending')===null;const p=this.original();return !!this.parked&&canonical(parseApprovalLocal(this.storage.getItem(this.prefix+'park:'+p.body.request_id)))===canonical(approvalParkRecord(p));}catch{return false;}}
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.onChange=()=>{};}
}
