import {freshNumber,typedRequestJSON,parseTypedJSON,plainTyped,embedRequestJSON} from './typed-numbers-v132.mjs';
const SCHEMA='object-create-request-v132';
const need=(ok,message='原对象登记或保存记录无法核对，输入和原请求保留。')=>{if(!ok)throw Error(message);};
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const same=(a,b)=>{if(typeof a!==typeof b)return false;if(a===null||b===null||typeof a!=='object')return a===b;if(Array.isArray(a)!==Array.isArray(b))return false;const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));};
const key=(base)=>base+':object-create-v132';
function scope(s){need(object(s)&&Object.keys(s).sort().join('|')==='actor_id|instance_id|workspace_id'&&Object.values(s).every(x=>typeof x==='string'&&x));return s;}
function request(body){need(object(body)&&Object.keys(body).sort().join('|')==='attributes|object_type|reference|release_id|request_id');need(object(body.attributes)&&['object_type','reference','release_id','request_id'].every(k=>typeof body[k]==='string')&&/^[a-zA-Z0-9_-]{12,100}$/.test(body.request_id));return body;}
export function buildObjectAttributes(type,values){
 need(object(type)&&Array.isArray(type.fields)&&object(values),'请先核对当前对象类型和字段。');const attributes={};
 for(const field of type.fields){
  if(!Object.hasOwn(values,field.key)||values[field.key]===''){need(!field.required,'请填写'+field.label+'。');continue;}
  attributes[field.key]=['数值','整数'].includes(field.kind)?freshNumber(values[field.key],field.kind==='整数'?'integer':'number',field.label):values[field.key];
 }
 return attributes;
}
export function prepareObjectRequest(payload,owner){
 scope(owner);const exact_body=typedRequestJSON(payload),body=request(plainTyped(parseTypedJSON(exact_body)));
 return {schema:SCHEMA,scope:{...owner},body,exact_body};
}
export function objectRequestJSON(action,owner){
 scope(owner);
 if(action?.schema===SCHEMA){
  need(Object.keys(action).sort().join('|')==='body|exact_body|schema|scope'&&same(scope(action.scope),owner));
  const actual=request(plainTyped(parseTypedJSON(action.exact_body)));need(same(actual,request(action.body)));return action.exact_body;
 }
 // The original legacy DTO is sent with precisely its old JSON.stringify
 // contract. Missing old type information is never reconstructed or upgraded.
 request(action);const exact=JSON.stringify(action);need(typeof exact==='string');return exact;
}
export const objectRequestBody=action=>action?.schema===SCHEMA?action.body:action;
export function readObjectRequest(storage,base,owner){
 const raw=storage.getItem(key(base));if(raw===null)return null;let record;try{record=JSON.parse(raw);}catch{need(false);}
 need(record?.schema===SCHEMA);objectRequestJSON(record,owner);return record;
}
export function persistObjectRequest(storage,base,owner,action){
 const exact=objectRequestJSON(action,owner);need(action?.schema===SCHEMA);const value=JSON.stringify(action),prior=storage.getItem(key(base));
 need(prior===null||prior===value,'另一窗口已有原对象登记，请先读取并核对；本页输入保留。');
 storage.setItem(key(base),value);need(storage.getItem(key(base))===value,'原对象请求尚未可靠保存，未提交。');return exact;
}
export function checkedObjectReceipt(value,owner,action){
 const body=objectRequestBody(action);objectRequestJSON(action,owner);
 need(object(value)&&value.schema==='object-request-recovery-v132'&&value.actor_id===owner.actor_id&&value.workspace_id===owner.workspace_id&&value.instance_id===owner.instance_id&&value.request_id===body.request_id&&/^[a-f0-9]{64}$/.test(value.request_hash)&&typeof value.found==='boolean');
 need(value.found?object(value.object)&&typeof value.object.id==='string'&&value.object.entity_type===body.object_type&&value.retirement===null:value.object===null&&(!value.retirement||value.retirement.request_id===body.request_id&&value.retirement.request_hash===value.request_hash));
 return value;
}
export async function lookupObjectRequest(api,owner,action){return checkedObjectReceipt(await api('/instances/'+owner.instance_id+'/objects/requests/lookup-v132',{method:'POST',body:objectRequestJSON(action,owner)}),owner,action);}
export async function retireObjectRequest(api,owner,action,reason){
 need(typeof reason==='string'&&reason.trim());
 const body=embedRequestJSON({reason},'original',objectRequestJSON(action,owner));
 return checkedObjectReceipt(await api('/instances/'+owner.instance_id+'/objects/requests/retire-v132',{method:'POST',body}),owner,action);
}
export function settleObjectRequest(storage,base,owner,action,receipt){
 checkedObjectReceipt(receipt,owner,action);need(receipt.found||receipt.retirement);
 const history=base+':object-request-receipt:'+objectRequestBody(action).request_id,value=JSON.stringify({action,receipt}),old=storage.getItem(history);
 if(old!==null){
  let saved;try{saved=JSON.parse(old);}catch{need(false);}
  need(objectRequestJSON(saved.action,owner)===objectRequestJSON(action,owner));checkedObjectReceipt(saved.receipt,owner,action);
  need(saved.receipt.request_hash===receipt.request_hash&&saved.receipt.found===receipt.found&&(receipt.found?saved.receipt.object.id===receipt.object.id:same(saved.receipt.retirement,receipt.retirement)));
 }else{storage.setItem(history,value);need(storage.getItem(history)===value,'登记结果尚未可靠保留，请重新核对原请求。');}
 if(action?.schema===SCHEMA){const cached=readObjectRequest(storage,base,owner);if(cached){need(objectRequestJSON(cached,owner)===objectRequestJSON(action,owner),'另一份原登记仍保留，请先读取并核对。');storage.removeItem(key(base));need(storage.getItem(key(base))===null);}}
 return receipt;
}

const DRAFT='object-input-draft-v132',MAX_DRAFT=4*1024*1024;
const utf8=value=>new TextEncoder().encode(value).length;
const draftKey=(name,base)=>typeof name==='string'&&[':object-draft-v132:',':task-form-journal:',':object-form-history:',':object-form-completed:',':object-original-form:'].some(part=>name.startsWith(base+part));
function inputForm(value,owner){
 need(object(value)&&typeof value.type==='string'&&object(value.attributes)&&typeof value.reference==='string'&&typeof value.showObject==='boolean');
 need(Object.values(value.attributes).every(v=>v===null||['string','boolean'].includes(typeof v)||typeof v==='number'&&Number.isFinite(v)));
 const action=value.objectAction??null;if(action)objectRequestJSON(action,owner);
 return {type:value.type,attributes:{...value.attributes},reference:value.reference,showObject:value.showObject,objectAction:action};
}
function draftProjection(raw,key,base,owner){
 need(typeof raw==='string'&&utf8(raw)<=MAX_DRAFT);const value=JSON.parse(raw);let form,release_id='',at='',window_id='',kind;
 if(key.startsWith(base+':object-draft-v132:')){
  need(object(value)&&Object.keys(value).sort().join('|')==='at|form|owner|release_id|schema|scope'&&value.schema===DRAFT&&same(scope(value.scope),owner)&&typeof value.owner==='string'&&value.owner&&typeof value.release_id==='string'&&typeof value.at==='string');
  ({form,release_id,at}=value);window_id=value.owner;kind='object';
 }else if(key.startsWith(base+':task-form-journal:')){
  need(object(value)&&value.schema==='workflow-task-form-journal-v1'&&object(value.scope)&&['actor_id','workspace_id','instance_id'].every(name=>value.scope[name]===owner[name])&&typeof value.at==='string');
  form=value.form;release_id=value.scope.release_id||'';at=value.at;kind='window';window_id=key.slice((base+':task-form-journal:').length);
 }else{
  // Old backups are input only, scoped by the caller's existing account key.
  // They never establish a receipt or authorize another request.
  need([':object-form-history:',':object-form-completed:',':object-original-form:'].some(part=>key.startsWith(base+part)));
  form=value;kind='legacy';
 }
 const projected=inputForm(form,owner);if(projected.objectAction)release_id=objectRequestBody(projected.objectAction).release_id;need(typeof release_id==='string');
 return {form:projected,release_id,at,window_id,kind};
}
export function objectInputFormJSON(form,owner){scope(owner);return typedRequestJSON(inputForm(form,owner));}
export function preserveObjectInputDraft(storage,base,owner,windowId,releaseId,form){
 scope(owner);need(typeof windowId==='string'&&windowId&&typeof releaseId==='string');
 const record={schema:DRAFT,scope:{...owner},owner:windowId,release_id:releaseId,at:new Date().toISOString(),form:inputForm(form,owner)},raw=typedRequestJSON(record);
 need(utf8(raw)<=MAX_DRAFT,'对象原稿超过本机单份保存范围，请保留页面，不会丢弃原字段。');
 const name=base+':object-draft-v132:'+crypto.randomUUID();need(storage.getItem(name)===null);storage.setItem(name,raw);need(storage.getItem(name)===raw,'当前对象原稿尚未可靠保留，未切换。');return {key:name,raw};
}
export function enumerateObjectInputDrafts(storage,base,owner,{offset=0,limit=20,maxScan=200}={}){
 scope(owner);need(typeof base==='string'&&base&&Number.isSafeInteger(offset)&&offset>=0&&Number.isSafeInteger(limit)&&limit>=1&&limit<=50&&Number.isSafeInteger(maxScan)&&maxScan>=1&&maxScan<=500);
 const items=[];let index=offset,scanned=0,problem_count=0;const length=storage.length;
 for(;index<length&&scanned<maxScan&&items.length<limit;index++,scanned++){
  const key=storage.key(index);if(!draftKey(key,base))continue;
  const raw=storage.getItem(key);try{const projection=draftProjection(raw,key,base,owner),form=projection.form;
   if(!Object.keys(form.attributes).length&&!form.reference&&!form.objectAction&&!form.showObject)continue;
   items.push({key,raw,scope:{...owner},...projection});
  }catch{problem_count++;}
 }
 return {items,next_offset:index<length?index:null,problem_count};
}
export function readObjectInputDraftCandidate(storage,base,owner,candidate){
 scope(owner);need(object(candidate)&&same(candidate.scope,owner)&&draftKey(candidate.key,base)&&typeof candidate.raw==='string');
 const raw=storage.getItem(candidate.key);need(raw===candidate.raw,'这份对象草稿已在另一页面变化，原稿保留，请重新读取列表。');
 const projection=draftProjection(raw,candidate.key,base,owner);return projection;
}
