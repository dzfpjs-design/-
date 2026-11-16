import {restoreSuggestionJournal,suggestionStorageKey} from './native-suggestion-contract.mjs';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const need=(ok,message='本机建议恢复记录无法核对，原记录保留，未发送。')=>{if(!ok)throw Error(message);};
const keys=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===names.split(',').sort().join(',');
function safe(value,parents=[]) {
 if(value===null||typeof value==='boolean')return;
 if(typeof value==='number'){need(Number.isFinite(value));return;}
 if(typeof value==='string'){
  need(!value.includes('\0'));
  for(let i=0;i<value.length;i++){const code=value.charCodeAt(i);if(code>=0xD800&&code<=0xDBFF){const next=value.charCodeAt(++i);need(next>=0xDC00&&next<=0xDFFF);}else need(code<0xDC00||code>0xDFFF);}return;
 }
 need(value&&typeof value==='object'&&!parents.includes(value));
 for(const key of Object.keys(value)){safe(key);const descriptor=Object.getOwnPropertyDescriptor(value,key);need(descriptor&&Object.hasOwn(descriptor,'value'));safe(descriptor.value,[...parents,value]);}
}
const encode=value=>{safe(value);return JSON.stringify(value);};
const copy=value=>JSON.parse(encode(value));
function same(a,b){if(a===b)return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;const aa=Object.keys(a).sort(),bb=Object.keys(b).sort();return aa.length===bb.length&&aa.every((key,index)=>key===bb[index]&&same(a[key],b[key]));}
export const emptySuggestionJournal=()=>({selected:'',edits:{},reason:'',modelID:'',dailyLimit:1,validDays:7,pending:null});
function journal(value){safe(value);need(value&&restoreSuggestionJournal(value));return {...emptySuggestionJournal(),...copy(value)};}
function pending(value){need(value);journal({...emptySuggestionJournal(),pending:value});return copy(value);}
export function nativeSuggestionScope(account,taskID){const scope={actor_id:account?.user_id,workspace_id:account?.workspace_id,task_id:taskID};need(Object.values(scope).every(value=>typeof value==='string'&&value));safe(scope);return scope;}
export function suggestionScopedRequester(account,request,{storage,isCurrent=()=>true,onInvalid=()=>{}}={}){
 const scoped=scopedPreparationRequester(account,request,storage),token=storage?.getItem('ga-setup-csrf')||'';
 const invalid=()=>{onInvalid();throw Error('当前账号、工作区或权限已变化，原稿和原请求保留。');};
 const check=()=>{need(isCurrent(),'原账号或页面已变化，原稿和原请求保留。');try{if(!token||storage.getItem('ga-setup-csrf')!==token||storage.getItem('ga-setup-csrf-user')!==account.user_id||storage.getItem('ga-setup-csrf-workspace')!==account.workspace_id)invalid();}catch(error){onInvalid();throw error;}};
 const identity=async()=>{check();let actual;try{actual=await scoped('/me');}catch(error){onInvalid();throw error;}check();if(actual.user_id!==account.user_id||actual.workspace_id!==account.workspace_id||actual.role!==account.role)invalid();};
 return async(path,options={})=>{await identity();check();let result;try{result=await scoped(path,options);}catch(error){check();if(error.status===401)onInvalid();throw error;}check();await identity();check();return result;};
}

export class NativeSuggestionJournal {
 constructor({storage,tabStorage,locks,account,taskID,randomUUID=()=>globalThis.crypto.randomUUID()}){
  this.storage=storage;this.tabStorage=tabStorage;this.locks=locks;this.randomUUID=randomUUID;
  this.scope=nativeSuggestionScope(account,taskID);this.legacyKey=suggestionStorageKey(account,taskID);
  this.prefix='ga-native-suggestion-v130:'+Object.values(this.scope).map(encodeURIComponent).join(':')+':';
  this.outboxKey=this.prefix+'outbox';this.pointerKey=this.prefix+'current-working';this.key=null;this.raw=null;this.value=null;this.pointerError='';this.tabReady=false;
 }
 get canLock(){return !!this.locks&&typeof this.locks.request==='function';}
 get canWrite(){return this.canLock&&this.tabReady;}
 write(key,value,expected){need(this.storage.getItem(key)===expected,'另一页面已更新本机记录；本页稿和原请求保留，未覆盖。');const raw=encode(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机记录未完整读回，尚未发送；请保留本页输入。');return raw;}
 box(){const raw=this.storage.getItem(this.outboxKey);if(raw===null)return {raw,value:null};const value=JSON.parse(raw);safe(value);need(keys(value,'schema,scope,requests')&&value.schema==='native-suggestion-outbox-v130'&&same(value.scope,this.scope)&&Array.isArray(value.requests)&&value.requests.length>0&&value.requests.length<=200);for(const item of value.requests)pending(item);need(new Set(value.requests.map(item=>item.body.request_id)).size===value.requests.length);return {raw,value};}
 readPending(){return copy(this.box().value?.requests[0]||null);}
 work(key){need(typeof key==='string'&&key.startsWith(this.prefix+'working:')&&UUID.test(key.slice((this.prefix+'working:').length)));const raw=this.storage.getItem(key);need(raw!==null);const value=JSON.parse(raw);need(keys(value,'schema,scope,owner,value')&&value.schema==='native-suggestion-working-v130'&&same(value.scope,this.scope)&&key===this.prefix+'working:'+value.owner&&UUID.test(value.owner));value.value=journal(value.value);return {raw,value};}
 pointer(){try{const key=this.tabStorage?.getItem(this.pointerKey);if(key)return this.work(key).value.value;}catch(error){this.pointerError='会话恢复指针尚未核对；共享原请求仍保留，可在下方恢复本机保留稿。';}return null;}
 fork(value){const owner=this.randomUUID();need(typeof owner==='string'&&UUID.test(owner));const key=this.prefix+'working:'+owner;const next=journal(value),record={schema:'native-suggestion-working-v130',scope:this.scope,owner,value:next};const raw=this.write(key,record,null);this.key=key;this.raw=raw;this.value=next;this.tabReady=false;try{need(this.tabStorage);this.tabStorage.setItem(this.pointerKey,key);need(this.tabStorage.getItem(this.pointerKey)===key);this.tabReady=true;this.pointerError='';}catch(error){this.pointerError='会话恢复指针未保存；原请求与本机稿仍在，可从下方本机保留稿恢复，暂不能发送或重放。';}return copy(next);}
 save(value){need(this.key);const next=journal(value),record=this.work(this.key).value;need(this.storage.getItem(this.key)===this.raw,'本页保留稿被另一页面修改；原稿保留，请另存本页稿后核对。');record.value=next;this.raw=this.write(this.key,record,this.raw);this.value=next;return copy(next);}
 async lock(fn){need(this.canLock,'浏览器缺少多窗口保存保护；原记录可读，不能发送或重放请求。');return this.locks.request(this.prefix+'lock',{mode:'exclusive'},fn);}
 importPending(original){if(!original)return;const request=pending(original),current=this.box();const requests=current.value?.requests||[];const prior=requests.find(item=>item.body.request_id===request.body.request_id);if(prior){need(same(prior,request),'同一原编号的正文不同，两份原记录均保留，未发送。');return;}need(requests.length<200,'保留的原请求已达上限，尚未删除或发送。');this.write(this.outboxKey,{schema:'native-suggestion-outbox-v130',scope:this.scope,requests:[...requests,request]},current.raw);}
 migrate(){const raw=this.storage.getItem(this.legacyKey);if(raw===null)return null;const old=journal(JSON.parse(raw));if(old.pending){this.importPending(old.pending);const owner=this.randomUUID();need(UUID.test(owner));this.write(this.prefix+'legacy:'+owner,{schema:'native-suggestion-legacy-v130',scope:this.scope,raw},null);this.write(this.legacyKey,{...old,pending:null},raw);}return old;}
 async open(){const pointed=this.pointer();let old=null;const shared=this.box().value?.requests[0]||null;if(this.canLock){await this.lock(()=>{old=this.migrate();this.importPending(pointed?.pending);});}else{const raw=this.storage.getItem(this.legacyKey);if(raw!==null)old=journal(JSON.parse(raw));}
  const base=pointed||old||emptySuggestionJournal();return this.fork({...base,pending:this.readPending()||shared||base.pending||null});
 }
 async restore(key,current){const selected=this.work(key).value.value;return this.lock(()=>{if(current)this.fork(current);this.migrate();this.importPending(selected.pending);return this.fork({...selected,pending:this.readPending()});});}
 async preserve(current){return this.lock(()=>{this.migrate();this.importPending(current.pending);return this.fork({...current,pending:this.readPending()});});}
 workingPage({limit=20,after=null}={}){need(Number.isInteger(limit)&&limit>=1&&limit<=50);const prefix=this.prefix+'working:',found=[];need(this.storage.length<=10000,'本机记录较多，未清理原稿；请先保留当前稿。');for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(typeof key==='string'&&key.startsWith(prefix)&&key!==this.key&&(!after||key>after))found.push(key);}found.sort();const selected=found.slice(0,limit);const items=selected.map(key=>{const value=this.work(key).value.value;const title=Object.values(value.edits).find(item=>item?.dirty)?.form?.title||value.reason||'本机保留稿';return {key,label:String(title).slice(0,80),hasPending:!!value.pending};});return {items,next_cursor:found.length>limit?selected.at(-1):null};}
 async request(original,{fresh=false}={},callback){return this.lock(async()=>{need(this.canWrite,'会话恢复指针尚未持久核对，原稿与原请求保留，不能发送或重放。');try{need(this.tabStorage.getItem(this.pointerKey)===this.key);}catch(error){this.tabReady=false;throw Error('会话恢复指针已变化或无法读取，原稿与原请求保留，不能发送或重放。');}this.migrate();const request=pending(original);let current=this.box();if(fresh){need(current.value===null,'另一页面已有待核原请求，本页稿保留；请先核对该原请求。');this.importPending(request);current=this.box();}need(current.value&&same(current.value.requests[0],request),'共享原请求已变化，旧正文保留，未发送。');this.save({...this.value,pending:request});
   let settled=false;
   const settle=next=>{need(!settled);const latest=this.box();need(latest.raw===current.raw&&same(latest.value.requests[0],request),'原请求槽已变化，尚未结清。');const remaining=latest.value.requests.slice(1),value=this.save({...next,pending:remaining[0]||null});if(remaining.length)this.write(this.outboxKey,{...latest.value,requests:remaining},latest.raw);else{need(this.storage.getItem(this.outboxKey)===latest.raw);this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null,'原请求清理未完整读回，请继续按原编号核对。');}settled=true;return copy(value);};
   return callback(copy(request),settle);
  });}
}
