import {typedRequestJSON,parseTypedJSON,plainTyped,embedRequestJSON} from './typed-numbers-v132.mjs';

export const GOAL_REQUEST_SCHEMA='native-goal-request-v132';
const operations=new Set(['define','check','accept','stop']);
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const need=(ok,message='原目标请求或本机稿无法核对，原记录保留，未发送。')=>{if(!ok)throw Error(message);};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const closed=(value,names)=>object(value)&&Object.keys(value).sort().join(',')===names.split(',').sort().join(',');
const clone=value=>JSON.parse(JSON.stringify(value));
function equal(left,right){if(typeof left==='number'||typeof right==='number')return typeof left==='number'&&typeof right==='number'&&Number.isFinite(left)&&Number.isFinite(right)&&left===right;if(left===null||right===null||typeof left!=='object'||typeof right!=='object')return left===right;if(Array.isArray(left)!==Array.isArray(right))return false;const a=Object.keys(left).sort(),b=Object.keys(right).sort();return a.length===b.length&&a.every((key,index)=>key===b[index]&&equal(left[key],right[key]));}
export function nativeGoalScope(account,taskID){const scope={actor_id:account?.user_id,workspace_id:account?.workspace_id,task_id:taskID};need(Object.values(scope).every(value=>typeof value==='string'&&value&&!value.includes('\0')));return scope;}
function command(body){need(object(body)&&typeof body.request_id==='string'&&/^[A-Za-z0-9_-]{12,100}$/.test(body.request_id)&&Number.isSafeInteger(body.expected_version)&&body.expected_version>=1&&typeof body.reason==='string');}
export function freezeNativeGoalRequest(action,body,scope){need(operations.has(action));const exact_body=typedRequestJSON(body),mirror=JSON.parse(exact_body);command(mirror);const original={schema:GOAL_REQUEST_SCHEMA,scope:clone(scope),action,body:mirror,exact_body};checkNativeGoalRequest(original,scope);return original;}
export function checkNativeGoalRequest(original,scope){
 need(object(original)&&operations.has(original.action));
 if(Object.hasOwn(original,'schema')||Object.hasOwn(original,'exact_body')){
  need(closed(original,'schema,scope,action,body,exact_body')&&original.schema===GOAL_REQUEST_SCHEMA&&closed(original.scope,'actor_id,workspace_id,task_id')&&equal(original.scope,scope)&&typeof original.exact_body==='string');
  const typed=parseTypedJSON(original.exact_body),mirror=JSON.parse(original.exact_body);plainTyped(original.body);need(equal(mirror,original.body));command(mirror);need(equal(plainTyped(typed),mirror));
 }else{need(closed(original,'action,body'));command(original.body);JSON.stringify(original.body);}
 return original;
}
export function nativeGoalRequestJSON(original,scope){checkNativeGoalRequest(original,scope);return original.schema===GOAL_REQUEST_SCHEMA?original.exact_body:JSON.stringify(original.body);}
export function nativeGoalRecoveryJSON(original,scope,{expected_version,action='probe',reason}){
 const exact=nativeGoalRequestJSON(original,scope),prefix={operation:original.action,expected_version,action,reason};
 return original.schema===GOAL_REQUEST_SCHEMA?embedRequestJSON(prefix,'original_request',exact):JSON.stringify({...prefix,original_request:original.body});
}
// Validate the download envelope without rebuilding its numeric JSON tokens.
// The caller still owns the fresh account/workspace checks before displaying it.
export async function nativeGoalExportText(response,taskID){
 need(response?.ok===true&&typeof response.text==='function'&&response.headers?.get('Content-Type')?.split(';')[0].trim().toLowerCase()==='application/json','导出响应格式无法核对，未生成下载。');
 const text=await response.text();let value;try{value=JSON.parse(text);}catch{throw Error('导出正文无法核对，未生成下载。');}
 need(value?.schema==='native-business-goal-export-v1'&&value.goal?.task_id===taskID&&value.physical_state_verified===false&&value.learning_auto_published===false&&value.production_ready===false,'导出记录不属于原目标或声明不完整，未生成下载。');
 return text;
}
function form(value){need(object(value)&&Array.isArray(value.fields));JSON.stringify(value);return clone(value);}

// Each page owns a working slot.  The shared outbox contains only an immutable
// original request, so draft edits cannot replace another page's pending nonce.
export class NativeGoalJournal{
 constructor({storage,tabStorage,locks,scope,legacyKey,randomUUID=()=>crypto.randomUUID()}){need(closed(scope,'actor_id,workspace_id,task_id'));scope=nativeGoalScope({user_id:scope.actor_id,workspace_id:scope.workspace_id},scope.task_id);Object.assign(this,{storage,tabStorage,locks,scope,legacyKey,randomUUID});this.prefix='ga-native-goal-v132:'+Object.values(scope).map(encodeURIComponent).join(':')+':';this.outboxKey=this.prefix+'outbox';this.pointerKey=this.prefix+'working-pointer';this.key=null;this.raw=null;this.form=null;this.tabReady=false;this.notice='';}
 get canLock(){return !!this.locks&&typeof this.locks.request==='function';}
 get canSend(){return this.canLock&&this.tabReady;}
 write(key,value,expected){need(this.storage.getItem(key)===expected,'另一页面已更新该保留稿，本页输入保留，尚未覆盖。');const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机稿尚未完整读回，不能提交；请留在本页。');return raw;}
 legacy(){const raw=this.storage.getItem(this.legacyKey);if(raw===null)return {raw,value:null};const value=JSON.parse(raw);need(object(value)&&object(value.form)&&Array.isArray(value.form.fields));if(value.pending){need(!Object.hasOwn(value.pending,'schema')&&!Object.hasOwn(value.pending,'exact_body'));checkNativeGoalRequest(value.pending,this.scope);}return {raw,value};}
 shared(){const raw=this.storage.getItem(this.outboxKey);if(raw===null)return {raw,value:null};const value=JSON.parse(raw);need(value.schema===GOAL_REQUEST_SCHEMA);checkNativeGoalRequest(value,this.scope);return {raw,value};}
 pending(){return clone(this.legacy().value?.pending||this.shared().value||null);}
 work(key){need(typeof key==='string'&&key.startsWith(this.prefix+'working:')&&uuid.test(key.slice((this.prefix+'working:').length)));const raw=this.storage.getItem(key);need(raw!==null);const value=JSON.parse(raw);need(closed(value,'schema,scope,owner,form')&&value.schema==='native-goal-working-v132'&&equal(value.scope,this.scope)&&key===this.prefix+'working:'+value.owner);value.form=form(value.form);return {raw,value};}
 fork(value){const owner=this.randomUUID();need(uuid.test(owner));const key=this.prefix+'working:'+owner,next=form(value),record={schema:'native-goal-working-v132',scope:this.scope,owner,form:next};this.raw=this.write(key,record,null);this.key=key;this.form=next;this.tabReady=false;try{need(this.tabStorage);this.tabStorage.setItem(this.pointerKey,key);need(this.tabStorage.getItem(this.pointerKey)===key);this.tabReady=true;this.notice='';}catch{this.notice='会话保存不可用；原稿与原请求仍在，本页暂不能提交，可读取下方保留稿。';}return clone(next);}
 open(defaultForm){const legacy=this.legacy();let previous;try{const key=this.tabStorage?.getItem(this.pointerKey);if(key)previous=this.work(key).value.form;}catch{this.notice='会话恢复指针未核对，仍可读取本机保留稿。';}const current=this.fork(previous||legacy.value?.form||defaultForm);if(!this.canLock)this.notice='浏览器缺少多窗口保存保护；原记录可读，不能提交或重放。';return {form:current,pending:this.pending()};}
 save(value){need(this.key);const record=this.work(this.key).value;record.form=form(value);this.raw=this.write(this.key,record,this.raw);this.form=record.form;return clone(this.form);}
 lock(callback){need(this.canLock,'浏览器缺少多窗口保存保护，原请求保留，不能提交。');return this.locks.request(this.prefix+'request-lock',{mode:'exclusive'},callback);}
 assertPointer(){need(this.canSend,'会话保存尚未核对，原稿和原请求保留，不能提交。');try{need(this.tabStorage.getItem(this.pointerKey)===this.key);}catch{this.tabReady=false;throw Error('会话保存已变化，原稿和原请求保留，不能提交。');}}
 preserve(value){return this.lock(()=>({form:this.fork(value),pending:this.pending()}));}
 restore(key,current){return this.lock(()=>{const selected=this.work(key).value.form;this.fork(current);return {form:this.fork(selected),pending:this.pending()};});}
 workingPage({limit=20,after=null}={}){need(Number.isInteger(limit)&&limit>=1&&limit<=50&&this.storage.length<=10000);const keys=[];for(let index=0;index<this.storage.length;index++){const key=this.storage.key(index);if(key?.startsWith(this.prefix+'working:')&&key!==this.key&&(!after||key>after))keys.push(key);}keys.sort();const chosen=keys.slice(0,limit);return {items:chosen.map(key=>({key,label:this.work(key).value.form.purpose?.slice(0,100)||'本机保留的目标稿'})),next_cursor:keys.length>limit?chosen.at(-1):null};}
 request(original,{fresh=false}={},callback){return this.lock(async()=>{
  this.assertPointer();checkNativeGoalRequest(original,this.scope);this.save(this.form);const typed=original.schema===GOAL_REQUEST_SCHEMA;
  let slot=typed?this.shared():this.legacy();
  if(typed){if(fresh)need(!slot.value&&!this.legacy().value?.pending,'已有原目标请求待核对，当前输入保留，不能另发请求。');if(!slot.value){this.write(this.outboxKey,original,null);slot=this.shared();}need(equal(slot.value,original),'原请求槽已变化，尚未提交。');}
  else need(slot.value?.pending&&equal(slot.value.pending,original),'原旧请求已变化，不能按新正文恢复。');
  let settled=false;const settle=()=>{need(!settled);if(typed){need(this.storage.getItem(this.outboxKey)===slot.raw,'原目标请求槽已变化，尚未结清。');this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null,'原请求清理尚未完整读回，请继续核对原编号。');}else{need(this.storage.getItem(this.legacyKey)===slot.raw,'原旧请求槽已变化，尚未结清。');const owner=this.randomUUID();need(uuid.test(owner));this.write(this.prefix+'legacy:'+owner,{schema:'native-goal-legacy-v132',scope:this.scope,raw:slot.raw},null);this.write(this.legacyKey,{...slot.value,pending:null},slot.raw);}settled=true;return this.pending();};
  return callback(clone(original),settle);
 });}
}
