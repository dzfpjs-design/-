import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';

export const REVIEW_BASE='/setup/business-flow-reviews';
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.getPrototypeOf(v)===Object.prototype;
const need=(ok,message='原稿或保存回执无法核对，输入与原请求保留。')=>{if(!ok)throw Error(message);};
export function safeReviewJSON(v,depth=0){
 need(depth<=48,'稿件嵌套过深，原输入保留。');
 if(v===null||typeof v==='boolean')return v;
 if(typeof v==='string'){need(!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v),'稿件含无法保存的文字。');return v;}
 if(typeof v==='number'){need(Number.isFinite(v));return v;}
 if(Array.isArray(v)){v.forEach(x=>safeReviewJSON(x,depth+1));return v;}
 need(object(v));for(const[k,x]of Object.entries(v)){need(!['__proto__','prototype','constructor'].includes(k));safeReviewJSON(k,depth+1);safeReviewJSON(x,depth+1);}return v;
}
export const reviewCopy=v=>structuredClone(v);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
async function sha(raw,crypto){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))),n=>n.toString(16).padStart(2,'0')).join('');}
const formOf=detail=>({value:reviewCopy(detail.value),note:detail.note,version:detail.version,generation:0,dirty:false,backups:[]});
export function reviewIssueTarget(path){
 const m=/^\/business\/flows\/(\d+)(?:\/(nodes|edges)\/(\d+))?/.exec(path||'');
 return m?{flow:Number(m[1]),kind:m[2]||'flow',index:m[3]===undefined?null:Number(m[3])}:null;
}
function checkDetail(v,id){
 need(object(v)&&v.attempt_id===id&&Number.isSafeInteger(v.version)&&v.version>=0
  &&typeof v.note==='string'&&v.can_apply===false&&typeof v.source_available==='boolean'
  &&typeof v.saved==='boolean'&&object(v.diagnostics));safeReviewJSON(v);return v;
}

export class BusinessFlowReviewController{
 constructor({producerId,actorId,workspaceId,api,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{}}){
  need([producerId,actorId,workspaceId].every(v=>uuid.test(v||'')),'报告或当前账号范围尚未确定。');
  this.scope={producerId,actorId,workspaceId};this.storage=storage;this.session=session;this.locks=locks;this.crypto=crypto;
  this.sessionToken=session?.getItem('ga-setup-csrf')||'';
  this.api=scopedPreparationRequester({user_id:actorId,workspace_id:workspaceId},api,session);
  this.prefix=`business-flow-review:v1:${workspaceId}:${actorId}:${producerId}:`;
  this.pointer=this.prefix+'tab-pointer';this.owner=null;this.key=null;this.onChange=onChange;
  this.active=true;this.suspended=false;this.denied=false;this.identityStatus='checking';this.role=null;
  this.state={selected:null,forms:{},pending:null,archives:[]};this.items=[];this.details={};this.localSaved=true;
  this.busy=false;this.error='';this.notice='';this.notFound=false;this.openPromise=null;this.opened=false;
 }
 get pending(){return this.state.pending;}
 get form(){return this.state.forms[this.state.selected]||null;}
 get detail(){return this.details[this.state.selected]||null;}
 get canEdit(){return this.active&&!this.suspended&&this.identityStatus==='verified'&&['operator','admin'].includes(this.role);}
 get protectedWork(){return !this.localSaved||this.busy;}
 safeToLeave(){if(this.busy)return false;if(this.localSaved)return true;return this.persist();}
 notify(){if(this.active&&!this.suspended)this.onChange(this);}
 check(){need(this.active&&!this.suspended&&!this.denied,'页面身份或报告范围已变化，原稿与请求仍保留。');
  if(this.session.getItem('ga-setup-csrf-user')!==this.scope.actorId||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspaceId||!this.sessionToken||this.session.getItem('ga-setup-csrf')!==this.sessionToken){this.denied=true;this.identityStatus='concealed';this.notify();throw Error('页面会话已变化，原稿与请求保留在原账号范围。');}}
 dispose(){this.active=false;this.suspended=true;this.onChange=()=>{};}
 async recheckIdentity(){this.identityStatus='checking';this.notify();await this.authorize();this.identityStatus='verified';this.notify();}
 async authorize(){
  this.check();try{const me=await this.api('/me');this.check();need(me?.user_id===this.scope.actorId&&me?.workspace_id===this.scope.workspaceId&&typeof me.role==='string'&&(!this.role||me.role===this.role),'当前账号、工作区或权限已变化，原稿保留。');this.role=me.role;return me;}
  catch(error){if(this.active&&!this.suspended){this.denied=[401,403].includes(error.status)||/会话已变化|当前账号、工作区或权限已变化/.test(error.message);this.identityStatus='concealed';this.error=error.message;this.notify();}throw error;}
 }
 async http(path,options={}){
  await this.authorize();this.check();let result;
  try{result=await this.api(path,options);}catch(error){this.check();if([401,403].includes(error.status)){this.denied=true;this.identityStatus='concealed';this.notify();throw error;}await this.authorize();throw error;}
  this.check();await this.authorize();this.check();return result;
 }
 persist(){
  this.localSaved=false;
  try{this.check();need(this.key);safeReviewJSON(this.state);const raw=JSON.stringify(this.state);
   need(new TextEncoder().encode(raw).length<=12*1024*1024,'本机恢复稿超过容量，请先导出保留。');
   this.storage.setItem(this.key,raw);need(this.storage.getItem(this.key)===raw,'本机稿件读回不一致。');this.localSaved=true;return true;
  }catch(error){this.error='本机保存失败，当前输入仍在本页。请先导出或重试本机保存；暂不能发送或换稿。'+error.message;return false;}
  finally{this.notify();}
 }
 async open(){
  if(this.openPromise)return this.openPromise;
  this.openPromise=(async()=>{try{
   await this.authorize();this.check();
   if(!this.opened){
    const pointer=this.session.getItem(this.pointer);let restored=null;
    if(pointer){need(pointer.startsWith(this.prefix+'draft:'));const raw=this.storage.getItem(pointer);need(raw!==null,'本页原恢复记录缺失，未改用另一份稿。');restored=JSON.parse(raw);safeReviewJSON(restored);
     need(object(restored)&&object(restored.forms)&&Array.isArray(restored.archives)&&Object.hasOwn(restored,'pending'));
     if(restored.selected!==null)need(uuid.test(restored.selected));}
    // Fork every new lifetime, including copied tabs. Existing human work and
    // original pending bytes stay in the previous immutable recovery stash.
    this.owner=this.crypto.randomUUID();this.key=this.prefix+'draft:'+this.owner;
    if(restored)this.state=restored;
    this.identityStatus='verified';need(this.persist(),this.error);
    this.session.setItem(this.pointer,this.key);need(this.session.getItem(this.pointer)===this.key);this.opened=true;
   }
   this.identityStatus='verified';await this.refreshCandidates();
   if(this.state.selected)await this.loadDetail(this.state.selected);this.notify();return true;
  }catch(error){this.error=error.message;if(!this.denied)this.openPromise=null;this.notify();throw error;}})();
  return this.openPromise;
 }
 async refreshCandidates(){
  const value=await this.http(REVIEW_BASE+'/candidates?producer_id='+encodeURIComponent(this.scope.producerId));
  need(object(value)&&Array.isArray(value.items));const seen=new Set();
  for(const row of value.items){need(object(row)&&uuid.test(row.attempt_id)&&typeof row.name==='string'&&typeof row.status==='string'&&Number.isSafeInteger(row.saved_version)&&row.saved_version>=0&&!seen.has(row.attempt_id));seen.add(row.attempt_id);}
  this.items=reviewCopy(value.items);this.notify();return this.items;
 }
 async loadDetail(id=this.state.selected){
  need(uuid.test(id||''));const detail=checkDetail(await this.http(REVIEW_BASE+'/attempts/'+id),id);
  this.details[id]=reviewCopy(detail);
  if(!this.state.forms[id]){this.state.forms[id]=formOf(detail);need(this.persist(),this.error);}
  else if(!this.state.forms[id].dirty&&!this.pending&&detail.version===this.state.forms[id].version){this.state.forms[id]=formOf(detail);need(this.persist(),this.error);}
  this.notify();return detail;
 }
 async select(id){this.check();need(this.localSaved,'先保留本机输入，未切换稿件。');need(this.items.some(x=>x.attempt_id===id));this.state.selected=id;need(this.persist(),this.error);return this.loadDetail(id);}
 edit(value,note=this.form?.note,{backup=false}={}){
  this.check();need(this.canEdit&&this.form,'当前账号只能读取，或稿件尚未读取。');safeReviewJSON(value);need(typeof note==='string'&&Array.from(note).length<=12000);safeReviewJSON(note);
  const form=this.form;if(backup)form.backups.push({value:reviewCopy(form.value),note:form.note});
  form.value=reviewCopy(value);form.note=note;form.generation++;form.dirty=true;this.error='';this.persist();
 }
 undo(){need(this.canEdit&&this.form?.backups.length);const previous=this.form.backups.pop();this.edit(previous.value,previous.note);}
 alignVersion(){this.check();need(this.canEdit&&this.localSaved&&!this.pending&&this.detail&&this.detail.version>=this.form.version,'请先核对原保存结果与服务器现稿。');this.form.version=this.detail.version;this.form.dirty=true;need(this.persist(),this.error);}
 exportText(){this.check();need(this.identityStatus==='verified');return JSON.stringify({scope:this.scope,recovery:this.state},null,2);}
 async operation(callback){this.check();need(!this.busy,'正在核对另一项保存，请稍候。');this.busy=true;this.error='';this.notify();try{return await callback();}catch(error){if(this.active){this.error=error.message;this.notify();}throw error;}finally{if(this.active){this.busy=false;this.notify();}}}
 async locked(callback){need(this.locks?.request,'当前浏览器不能保障多窗口保存；本机稿保留，可导出并继续只读核对。');return this.locks.request(this.prefix+'save',{mode:'exclusive'},callback);}
 readOwn(){const raw=this.storage.getItem(this.key);need(raw!==null,'本机恢复记录缺失。');const value=JSON.parse(raw);safeReviewJSON(value);return value;}
 exact(p){this.check();need(this.pending?.raw===p.raw&&this.pending?.sha===p.sha&&this.readOwn().pending?.raw===p.raw,'原保存请求已变化，未提交替代内容。');}
 async probe(p){
  this.exact(p);let reply;
  try{reply=await this.http(REVIEW_BASE+'/requests/'+p.body.request_id+'?expected_sha256='+p.sha);}catch(error){if(error.status===404)return {found:false};throw error;}
  need(object(reply)&&reply.schema==='business-flow-review-original-result-v1'&&reply.request_id===p.body.request_id&&typeof reply.found==='boolean'&&reply.body_sha256===p.sha);
  if(!reply.found){need(reply.result===null);return {found:false};}return {found:true,result:reply.result};
 }
 checkResult(result,p){need(object(result)&&result.schema==='business-flow-review-save-result-v1'&&result.attempt_id===p.attemptId&&result.request_id===p.body.request_id&&result.version===p.body.expected_version+1&&result.status==='incomplete'&&result.can_apply===false&&result.can_publish===false&&result.can_execute===false&&result.semantic_quality_verified===false&&same(result.value,p.body.value)&&result.note===p.body.note);safeReviewJSON(result);return result;}
 accept(result,p){
  this.exact(p);this.checkResult(result,p);const before=reviewCopy(this.state),form=this.state.forms[p.attemptId];need(form);
  form.version=result.version;form.dirty=!(form.generation===p.generation&&same(form.value,p.body.value)&&form.note===p.body.note);
  this.state.pending=null;
  if(!this.persist()){this.state=before;throw Error('服务器已返回保存结果，但本机恢复记录未写入；原号仍保留，请导出或重新查询。');}
  if(this.details[p.attemptId])this.details[p.attemptId]={...this.details[p.attemptId],...reviewCopy(result),saved:true};
  this.notFound=false;this.notice=form.dirty?'原保存已核对；后来输入仍保留，尚未保存到服务器。':'草稿已保存并核对原回执；流程仍未完成，未采用或执行。';this.notify();return result;
 }
 async save(){return this.operation(()=>this.locked(async()=>{
  await this.authorize();need(this.canEdit&&this.form&&this.localSaved&&!this.pending,'先核对原请求或保留本机输入，未另起保存。');need(this.detail?.source_available===true,'原文来源暂不可核对；输入保留本机，请恢复来源后保存。');
  need(this.form.version===this.detail.version,'服务器已有新版本；先对照服务器现稿，再明确对齐版本。');need(this.persist(),this.error);
  const generation=this.form.generation,attemptId=this.state.selected;
  const body={request_id:this.crypto.randomUUID(),expected_version:this.form.version,value:reviewCopy(this.form.value),note:this.form.note};
  need(new TextEncoder().encode(JSON.stringify(body.value)).length<=1024*1024,'稿件超过1MiB；输入仍在本机，未截断或发送，请导出保留。');
  const raw=JSON.stringify(body);need(new TextEncoder().encode(raw).length<=2*1024*1024,'完整保存请求超过2MiB；原稿保留，未截断或发送。');
  const hash=await sha(raw,this.crypto);this.check();
  const p={attemptId,body,raw,sha:hash,generation,attempted:false,rejection:null};this.state.pending=p;need(this.persist(),this.error);
  return this.resolve(p,true);
 }));}
 async resolve(p,send){
  this.exact(p);need(JSON.stringify(p.body)===p.raw&&await sha(p.raw,this.crypto)===p.sha,'原请求原字节无法核对，未发送。');this.check();
  const prior=await this.probe(p);this.exact(p);if(prior.found)return this.accept(prior.result,p);
  this.notFound=true;if(!send){this.notice='尚未查到原保存回执；不表示原请求未到达，未创建新号或重发。';this.notify();return prior;}
  need(!p.rejection&&this.canEdit&&this.localSaved,'原请求已明确拒绝或本机输入未保留，未重发。');
  p={...p,attempted:true};this.state.pending=p;need(this.persist(),this.error);
  let result;try{result=await this.http(REVIEW_BASE+'/attempts/'+p.attemptId+'/save',{method:'POST',body:p.raw});}
  catch(error){this.exact(p);if(error.status===409&&error.detail?.code==='business_flow_review_version_conflict'&&error.detail.not_applied===true&&Number.isSafeInteger(error.detail.current_version)&&error.detail.current_version>=0){p={...p,rejection:{code:error.detail.code,current_version:error.detail.current_version}};this.state.pending=p;this.persist();}throw error;}
  this.exact(p);return this.accept(result,p);
 }
 async recover(send=false){return this.operation(()=>{const work=async()=>{need(this.pending,'没有待核对的原保存请求。');return this.resolve(reviewCopy(this.pending),send);};return send?this.locked(work):work();});}
 archiveRejected(){this.check();need(this.canEdit&&this.localSaved&&this.pending?.rejection?.code==='business_flow_review_version_conflict','未证明本次未应用，原号必须保留。');this.state.archives.push(reviewCopy(this.pending));this.state.pending=null;need(this.persist(),this.error);this.notFound=false;this.notice='已保留被明确拒绝的原请求；请读取服务器现稿，再对齐版本保存当前输入。';this.notify();}
}
