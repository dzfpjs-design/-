// Bounded local discovery. A local completion is only a hint, never authority.
import {nativeSourcePrefixV136,nativeSourceScopeV136,NATIVE_SOURCE_MAX_RECORD_V136} from './native-graph-sources-journal-v136.mjs';
import {nativeSourceFormV136,nativeSourceOutboxV136} from './native-graph-sources-v136.mjs';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const need=value=>{if(!value)throw Error('本机记录无法核对，原记录未被覆盖。');};
const same=(left,right)=>left?.actor_id===right.actor_id&&left?.workspace_id===right.workspace_id&&left?.instance_id===right.instance_id&&Object.keys(left||{}).length===3;
function candidate(key,raw,scope){
 const prefix=nativeSourcePrefixV136(scope);need(key.startsWith(prefix)&&typeof raw==='string'&&raw.length<=NATIVE_SOURCE_MAX_RECORD_V136);
 const value=JSON.parse(raw);need(value&&same(value.scope,scope));
 if(value.schema==='native-graph-source-draft-v136'){
  need(Object.keys(value).sort().join(',')==='form,generation,owner,schema,scope,window_id'&&UUID.test(value.owner)&&UUID.test(value.window_id)&&Number.isSafeInteger(value.generation)&&value.generation>=0);
  need(key===prefix+'form:entry:'+value.owner&&value.generation===0||key===prefix+'form:slot:'+value.owner+':'+(value.generation%2)&&value.generation>=1);
  nativeSourceFormV136(value.form);return {key,raw,kind:'draft',label:value.form.label.trim()||'未命名的语音来源稿',summary:(value.form.source?.name||'尚未选择来源')+' · '+(value.form.installation?.name||'尚未选择本地模型'),value};
 }
 nativeSourceOutboxV136(value,scope);need(key===prefix+(value.kind==='retirement'?'retirement:':'request:')+value.body.request_id);
 return {key,raw,kind:value.kind==='retirement'?'retirement':'request',label:value.kind==='register'?value.body.label:value.kind==='revoke'?'停止来源的原操作':'结束未确认操作的原请求',summary:'完整原内容保留；选择后只向服务器核对，不会自动发送。',value};
}
export function readNativeSourceRecoveryV136(storage,scope,selected){
 scope=nativeSourceScopeV136(scope);need(selected&&typeof selected.key==='string'&&selected.key.startsWith(nativeSourcePrefixV136(scope)));
 const raw=storage.getItem(selected.key);need(raw===selected.raw);return candidate(selected.key,raw,scope);
}
export function enumerateNativeSourceRecoveryV136(storage,scope,{offset=0,limit=12,scan=120}={}){
 scope=nativeSourceScopeV136(scope);need(Number.isSafeInteger(offset)&&offset>=0&&Number.isSafeInteger(limit)&&limit>=1&&limit<=30&&Number.isSafeInteger(scan)&&scan>=limit&&scan<=500);
 const prefix=nativeSourcePrefixV136(scope),items=[];let problems=0,index=offset,visited=0;const length=storage.length;
 need(Number.isSafeInteger(length)&&length>=0);
 while(index<length&&items.length<limit&&visited<scan){
  const key=storage.key(index++);visited++;
  // Foreign scope keys are never read or parsed, even to decide their schema.
  if(typeof key!=='string'||!key.startsWith(prefix))continue;
  if(!key.startsWith(prefix+'form:entry:')&&!key.startsWith(prefix+'form:slot:')&&!key.startsWith(prefix+'request:')&&!key.startsWith(prefix+'retirement:'))continue;
  try{items.push(candidate(key,storage.getItem(key),scope));}catch{problems++;}
 }
 return {loaded:true,items,problem_count:problems,next_offset:index<length?index:null};
}
