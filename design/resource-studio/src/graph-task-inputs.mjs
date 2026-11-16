import {inspectHumanSchema,humanValues,humanOutput} from './graph-task-controller.mjs';
import {typedRequestJSON,parseTypedJSON,plainTyped,cloneTyped,isTypedNumber,typedEqual,embedRequestJSON} from './typed-numbers-v132.mjs';
import {monitorPolicyPinInputs,monitorPolicyDraftValue,checkedMonitorPolicySelection} from './monitor-policy-pin-v287.mjs';
const copy=value=>JSON.parse(JSON.stringify(value)),object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const need=(ok,message='任务输入与原版本无法核对，已保留原草稿。')=>{if(!ok)throw Error(message);};
const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const text=v=>typeof v==='string'&&v.length>0;
export const GRAPH_INPUT_DRAFT='workflow-graph-task-input-draft-v1';
export function checkedTaskInputMetadata(value,scope){
 need(object(value)&&value.schema==='workflow-graph-task-inputs-v1'&&value.instance_id===scope.instance_id&&value.release_id===scope.release_id&&value.business_id===scope.business_id&&value.configuration_hash===scope.configuration_hash&&sha(value.hash)&&value.execution_granted===false);
 need(['graph','legacy'].includes(value.mode)&&Array.isArray(value.entity_fields)&&Array.isArray(value.system_fields));
 if(value.mode==='graph'){
  need(sha(value.graph_hash)&&sha(value.binding_hash)&&object(value.input_schema)&&object(value.user_input_schema)&&object(value.user_input_schema.properties));
  const seen=new Set();for(const field of value.entity_fields){need(object(field)&&text(field.field)&&text(field.label)&&typeof field.required==='boolean'&&Array.isArray(field.waits)&&field.waits.every(text)&&!seen.has(field.field)&&Object.hasOwn(value.user_input_schema.properties,field.field)&&!value.system_fields.includes(field.field));seen.add(field.field);}
 }else need(value.entity_fields.length===0&&value.graph_hash===null&&value.binding_hash===null&&value.user_input_schema===null);
 return value;
}
function draftScope(metadata,scope){checkedTaskInputMetadata(metadata,scope);need(text(scope.actor_id)&&text(scope.workspace_id));return {actor_id:scope.actor_id,workspace_id:scope.workspace_id,instance_id:scope.instance_id,release_id:scope.release_id,business_id:scope.business_id,configuration_hash:scope.configuration_hash,graph_hash:metadata.graph_hash,binding_hash:metadata.binding_hash,metadata_hash:metadata.hash};}
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const stableJSON=v=>Array.isArray(v)?v.map(stableJSON):object(v)?Object.fromEntries(Object.keys(v).sort().map(key=>[key,stableJSON(v[key])])):v;
const sameJSON=(a,b)=>same(stableJSON(a),stableJSON(b));
// Only this exact published reference port is selected after a Task draft
// exists. Arbitrary object inputs remain unsupported by the human form.
export function runtimeDocumentSourceInputs(schema){
 const marker='x-runtime-document-source-set-v207',reference={type:'object',properties:{source_set_id:{type:'string',pattern:'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},source_set_hash:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['source_set_id','source_set_hash'],additionalProperties:false,[marker]:true};
 return Object.entries(schema?.properties||{}).filter(([,spec])=>object(spec)&&Object.hasOwn(spec,marker)).map(([key,spec])=>{
  need(spec[marker]===true&&sameJSON(Object.fromEntries(Object.entries(spec).filter(([name])=>!['title','description'].includes(name))),reference),'运行期资料输入的声明无法核对，原任务草稿保留。');
  return {key,label:typeof spec.title==='string'&&spec.title.trim()?spec.title:'运行期资料依据',required:(schema.required||[]).includes(key)};
 });
}
const originalRequests=new WeakMap();
const creationEncoding='workflow-task-creation-form-encoding-v2';
const mirror=value=>value===undefined?undefined:JSON.parse(JSON.stringify(plainTyped(value)));
const sameMirror=(a,b)=>sameJSON(mirror(a),mirror(b));
const hasAtom=value=>isTypedNumber(value)||value!==null&&typeof value==='object'&&Object.values(value).some(hasAtom);
const encodingFor=payload=>({schema:creationEncoding,request_id:payload.request_id});
function checkedEncoding(value,payload){need(object(value)&&Object.keys(value).sort().join('|')==='request_id|schema'&&value.schema===creationEncoding&&value.request_id===payload?.request_id,'原数值请求的编码范围无法核对，原记录保留。');return value;}
function bindOriginal(payload,exact){need(typedEqual(payload,parseTypedJSON(exact)));originalRequests.set(payload,exact);return payload;}
export function taskCreationJSON(payload){
 const exact=originalRequests.get(payload);if(exact!==undefined){need(typedEqual(payload,parseTypedJSON(exact)),'原发起正文已变化，完整原请求仍保留。');return exact;}
 // A legacy payload keeps its original JSON encoding. New typed payloads are
 // registered only by persistTaskCreation's explicit v2 request boundary.
 return hasAtom(cloneTyped(payload))?typedRequestJSON(payload):JSON.stringify(payload);
}
export function taskInputForm(metadata,scope,{typedNumbers=false}={}){
 checkedTaskInputMetadata(metadata,scope);if(metadata.mode==='legacy')return {supported:true,fields:[],entities:[],schema:null};
 const entities=metadata.entity_fields,sourceInputs=runtimeDocumentSourceInputs(metadata.user_input_schema),policyInputs=monitorPolicyPinInputs(metadata.user_input_schema),keys=new Set(entities.map(row=>row.field)),schema=typedNumbers?{...metadata.user_input_schema}:copy(metadata.user_input_schema);
 for(const field of [...sourceInputs,...policyInputs]){need(!keys.has(field.key)&&!metadata.system_fields.includes(field.key));keys.add(field.key);}
 // Entity IDs have a dedicated picker; the original schema stays on the server.
 // Preserve unsupported root constraints instead of pretending to support them.
 schema.properties=Object.fromEntries(Object.entries(schema.properties).filter(([key])=>!keys.has(key)));
 schema.required=(schema.required||[]).filter(key=>!keys.has(key));
 const model=inspectHumanSchema(schema,{typedNumbers});return {...model,entities,sourceInputs,policyInputs,schema};
}
export function createTaskInputDraft(metadata,scope,options={}){
 const model=taskInputForm(metadata,scope,options);need(model.supported,model.reason);
 const values=model.schema?humanValues(model.schema,null,options):{};
 for(const field of model.entities)values[field.field]={mode:'unset',raw:''};
 for(const field of model.policyInputs||[])values[field.key]={mode:'unset',raw:''};
 return {schema:GRAPH_INPUT_DRAFT,scope:draftScope(metadata,scope),values};
}
export function checkedTaskInputDraft(draft,metadata,scope,options={}){
 need(object(draft)&&draft.schema===GRAPH_INPUT_DRAFT&&same(draft.scope,draftScope(metadata,scope))&&object(draft.values));
 const expected=createTaskInputDraft(metadata,scope,options);need(Object.keys(draft.values).length===Object.keys(expected.values).length&&Object.keys(expected.values).every(key=>Object.hasOwn(draft.values,key)));
 for(const value of Object.values(draft.values))need(object(value)&&Object.keys(value).length===2&&['unset','value','null'].includes(value.mode)&&typeof value.raw==='string');
 for(const field of taskInputForm(metadata,scope,options).policyInputs||[])monitorPolicyDraftValue(draft.values[field.key],{...field,required:false});
 return draft;
}
export function editTaskInput(draft,metadata,scope,key,value,options={}){
 checkedTaskInputDraft(draft,metadata,scope,options);need(Object.hasOwn(draft.values,key));const result=copy(draft);result.values[key]=copy(value);checkedTaskInputDraft(result,metadata,scope,options);return result;
}
export function buildTaskGraphInputs(metadata,scope,draft,selectedObjects,options={}){
 checkedTaskInputMetadata(metadata,scope);if(metadata.mode==='legacy')return {};
 const model=taskInputForm(metadata,scope,options);need(model.supported,model.reason);draft=checkedTaskInputDraft(draft||createTaskInputDraft(metadata,scope,options),metadata,scope,options);
 need(Array.isArray(selectedObjects)&&new Set(selectedObjects).size===selectedObjects.length);
 const entityKeys=new Set([...model.entities.map(row=>row.field),...(model.policyInputs||[]).map(row=>row.key)]),values=Object.fromEntries(Object.entries(draft.values).filter(([key])=>!entityKeys.has(key))),result=humanOutput(model.schema,values,options);
 for(const field of model.policyInputs||[]){
  const value=checkedMonitorPolicySelection(draft.values[field.key],field,scope,selectedObjects,
   options.monitorPolicyRole,options.monitorPolicyOptions?.[field.key],options);
  if(value!==undefined)result[field.key]=value;
 }
 for(const field of model.entities){
  const value=draft.values[field.field],spec=metadata.user_input_schema.properties[field.field];
  if(value.mode==='unset'){need(!field.required,'请选择'+field.label+'；不会自动使用第一个对象。');continue;}
  need(value.mode==='value'&&selectedObjects.includes(value.raw),'关联对象必须从本次已选对象中明确选择。');
  need(!spec.enum||spec.enum.includes(value.raw),'关联对象不在当前流程声明的范围内。');
  result[field.field]=value.raw;
 }
 need(!Object.keys(result).some(key=>metadata.system_fields.includes(key)),'服务器固定的对象资料不能手工替换。');return result;
}
export function taskInputHistoryLabel(metadata,key,value){
 if(monitorPolicyPinInputs(metadata?.user_input_schema).some(field=>field.key===key))return value.mode==='null'?'不自动补充观测':value.mode==='unset'?'尚未明确选择':'已保留原观测策略选择（使用前须重新核对）';
 return null;
}
export function persistTaskCreation(storage,key,form,payload,{typedNumbers=false}={}){
 need(object(payload)&&text(payload.request_id),'请保留原发起请求，不能用空请求覆盖。');
 const historyKey=key+':task-create-outbox:'+payload.request_id,previous=storage.getItem(historyKey),old=previous===null?null:checkCreationRecord(previous);
 // Replaying an old v1 nonce never upgrades its body or storage family.
 if(old?.schema==='workflow-task-creation-outbox-v1'){need(same(old.payload,payload),'原发起编号已绑定不同内容。');typedNumbers=false;}
 const exact=typedNumbers?(originalRequests.get(payload)||typedRequestJSON(payload)):JSON.stringify(payload);
 if(typedNumbers)bindOriginal(payload,exact);
 const pending=readTaskCreation(storage,key);need(!pending||(pending.schema==='workflow-task-creation-outbox-v2'?taskCreationJSON(pending.payload)===exact:same(pending.payload,payload)),'另一窗口已有待核对请求，未覆盖原编号。');
 const record=typedNumbers?{schema:'workflow-task-creation-outbox-v2',payload:mirror(payload),form:mirror({...form,creationAction:payload,creationEncoding:encodingFor(payload)}),exact_body:exact}:{schema:'workflow-task-creation-outbox-v1',payload:copy(payload),form:copy({...form,creationAction:payload})};
 const encoded=JSON.stringify(record);
 if(previous!==null)need(old.schema===record.schema&&(typedNumbers?taskCreationJSON(old.payload)===exact:same(old.payload,payload)),'原发起编号已绑定不同内容。');
 if(previous===null)writeBack(storage,historyKey,encoded);
 // This independent outbox is the authority. A stale form writer cannot erase
 // the original nonce, full body or the human inputs needed after a reload.
 writeBack(storage,key+':task-create-pending',previous||encoded);
 saveTaskForm(storage,key,{...form,creationAction:payload});return exact;
}
function writeBack(storage,key,value){storage.setItem(key,value);need(storage.getItem(key)===value,'原输入尚未可靠保存，请勿离开；尚未提交。');}
function checkCreationRecord(raw){
 const record=JSON.parse(raw);need(object(record)&&object(record.payload)&&text(record.payload.request_id)&&object(record.form),'原发起恢复记录无法核对，请保留原记录。');
 if(record.schema==='workflow-task-creation-outbox-v2'){
  need(Object.keys(record).sort().join('|')==='exact_body|form|payload|schema'&&text(record.exact_body));
  const payload=parseTypedJSON(record.exact_body);need(object(payload)&&text(payload.request_id)&&sameMirror(payload,record.payload)&&sameMirror(record.form.creationAction,record.payload),'原数值正文与本机镜像不一致，请保留原记录。');
  if(record.form.creationEncoding!=null)checkedEncoding(record.form.creationEncoding,payload);
  record.form.creationEncoding=encodingFor(payload);
  record.payload=bindOriginal(payload,record.exact_body);record.form.creationAction=record.payload;
 }else need(record.schema==='workflow-task-creation-outbox-v1'&&record.form.creationEncoding==null&&same(record.form.creationAction,record.payload),'原发起恢复记录无法核对，请保留原记录。');
 return record;
}
export function readTaskCreation(storage,key){
 const raw=storage.getItem(key+':task-create-pending');if(raw===null)return null;
 const record=checkCreationRecord(raw);
 const immutable=storage.getItem(key+':task-create-outbox:'+record.payload.request_id);need(immutable===raw,'原发起独立历史与恢复记录不一致。');return record;
}
export function readTaskForm(storage,key){
 const raw=storage.getItem(key),form=raw===null?{}:JSON.parse(raw);need(object(form));const pending=readTaskCreation(storage,key);
 if(pending?.schema==='workflow-task-creation-outbox-v1')need(form.creationEncoding==null,'原表单与独立请求的编码家族不一致，原记录保留。');
 if(!pending){
  need(form.creationEncoding==null,'原数值请求的独立正文暂未读到，请保留本机记录，不能把镜像按旧请求重发。');
  if(form.creationAction){const history=storage.getItem(key+':task-create-outbox:'+form.creationAction.request_id);if(history!==null)need(checkCreationRecord(history).schema!=='workflow-task-creation-outbox-v2','原数值请求的恢复指针缺失，完整历史保留，不能降为旧请求。');}
 }
 const independentObjectFields=Object.fromEntries(['type','attributes','reference','showObject','objectAction'].filter(name=>Object.hasOwn(form,name)).map(name=>[name,form[name]]));
 return pending?{...pending.form,...(sameMirror(form.creationAction,pending.payload)?{...independentObjectFields,creationStatus:form.creationStatus}:{}),creationAction:pending.payload}:form;
}
export function saveTaskForm(storage,key,form){
 const pending=readTaskCreation(storage,key),raw=storage.getItem(key),previous=raw===null?{}:JSON.parse(raw);need(object(previous));
 const original=pending?.payload||previous.creationAction;
 need(!original||sameMirror(original,form.creationAction),'另一窗口已有待核对请求，当前输入仍在本页，未覆盖原编号。');
 need(pending||!form.creationAction||storage.getItem(key+':task-create-completed:'+form.creationAction.request_id)===null,'另一窗口已结清原请求，请读取本机已保存的任务草稿；当前输入保留。');
 const next={...form},encoding=pending?.schema==='workflow-task-creation-outbox-v2'?encodingFor(pending.payload):previous.creationEncoding??form.creationEncoding;
 if(encoding!=null){checkedEncoding(encoding,form.creationAction);need(!pending||pending.schema==='workflow-task-creation-outbox-v2','原请求的编码家族不一致，原记录保留。');next.creationEncoding=encoding;}
 const encoded=JSON.stringify(plainTyped(next));if(raw!==encoded)writeBack(storage,key,encoded);return true;
}
/** Caller holds the actual Task-create WebLock and has freshly checked /me. */
export function migrateLegacyTaskCreation(storage,key,payload){
 need(object(payload)&&text(payload.request_id)&&!originalRequests.has(payload)&&!hasAtom(cloneTyped(payload)),'原数值请求不能按旧表单重新编码，原输入保留。');
 need(readTaskCreation(storage,key)===null,'另一窗口已有原请求，未替换。');
 const raw=storage.getItem(key);need(raw!==null,'旧表单原文尚未读到，原请求保留。');const form=JSON.parse(raw);
 need(object(form)&&object(form.creationAction)&&form.creationEncoding==null&&JSON.stringify(form.creationAction)===JSON.stringify(payload),'旧表单原正文已变化或带新编码标记，未迁移或发送。');
 need(storage.getItem(key+':task-create-completed:'+payload.request_id)===null,'原请求已结清，不能按旧稿重新建立待发请求。');
 const immutable=storage.getItem(key+':task-create-outbox:'+payload.request_id);
 if(immutable!==null){const old=checkCreationRecord(immutable);need(old.schema==='workflow-task-creation-outbox-v1'&&JSON.stringify(old.payload)===JSON.stringify(payload),'原独立正文不属于此旧请求，不能降级或替换。');}
 const backup=key+':task-create-legacy-form:'+payload.request_id+':'+crypto.randomUUID();need(storage.getItem(backup)===null);writeBack(storage,backup,raw);
 need(storage.getItem(key)===raw,'备份期间旧表单已有新输入，原稿保留，未迁移。');
 const exact=persistTaskCreation(storage,key,form,payload,{typedNumbers:false}),pending=readTaskCreation(storage,key);
 need(pending?.schema==='workflow-task-creation-outbox-v1'&&taskCreationJSON(pending.payload)===exact&&exact===JSON.stringify(form.creationAction),'旧请求尚未完整读回，未发送。');return pending;
}
export function completeTaskCreation(storage,key,form,payload){
 const pending=readTaskCreation(storage,key);need(!pending||(pending.schema==='workflow-task-creation-outbox-v2'?taskCreationJSON(pending.payload)===taskCreationJSON(payload):same(pending.payload,payload)),'另一窗口的待核对请求已变化，原记录保留。');
 const raw=storage.getItem(key),previous=raw===null?{}:JSON.parse(raw);need(!previous.creationAction||sameMirror(previous.creationAction,payload),'原请求已变化，尚未清除。');
 need(pending||previous.creationAction&&sameMirror(previous.creationAction,payload),'另一窗口已结清原请求并开始新稿，请读取本机已保存的任务草稿；未覆盖当前草稿。');
 // Keep the immutable outbox forever; clearing only follows the caller's
 // verified server receipt and cold Task detail read.
 writeBack(storage,key+':task-create-completed:'+payload.request_id,JSON.stringify(pending?.schema==='workflow-task-creation-outbox-v2'?{payload:mirror(payload),exact_body:taskCreationJSON(payload)}:{payload}));
 writeBack(storage,key,JSON.stringify(plainTyped({...form,creationAction:null,...(pending?.schema==='workflow-task-creation-outbox-v2'||previous.creationEncoding!=null?{creationEncoding:null}:{})})));
 if(pending){storage.removeItem(key+':task-create-pending');need(storage.getItem(key+':task-create-pending')===null,'原任务已保存，恢复指针尚未清除，请继续核对。');}
}
export function preserveTaskInputDraft(storage,key,form,owner=null){
 if(!form.graphInputDraft)return;
 const record={schema:'workflow-task-input-history-v1',at:new Date().toISOString(),business:form.business,title:form.title,selectedObjects:copy(form.selectedObjects),draft:copy(form.graphInputDraft)};
 const journal=owner?key+':task-input-history:journal:'+encodeURIComponent(owner)+':'+encodeURIComponent(JSON.stringify(form.graphInputDraft.scope)):null;
 const encoded=JSON.stringify(record),last=storage.getItem(journal||key+':task-input-last');
 if(last!==null){const value=JSON.parse(last);if(same({...value,at:null},{...record,at:null}))return;}
 if(journal)writeBack(storage,journal,encoded);
 else{writeBack(storage,key+':task-input-history:'+crypto.randomUUID(),encoded);writeBack(storage,key+':task-input-last',encoded);}
}
export function readTaskInputHistory(storage,key,scope){
 const rows=[];for(let i=0;i<storage.length;i++){const name=storage.key(i);if(!name?.startsWith(key+':task-input-history:'))continue;
  const row=JSON.parse(storage.getItem(name));need(object(row)&&row.schema==='workflow-task-input-history-v1'&&object(row.draft?.scope)&&Array.isArray(row.selectedObjects));
  if(['actor_id','workspace_id','instance_id'].every(field=>row.draft.scope[field]===scope[field]))rows.push({...row,key:name});
 }
 return rows.sort((a,b)=>b.at.localeCompare(a.at));
}
export function preserveTaskFormJournal(storage,key,form,scope,owner){
 need(text(owner)&&text(scope.actor_id)&&text(scope.workspace_id)&&text(scope.instance_id));
 const journal=key+':task-form-journal:'+encodeURIComponent(owner),record={schema:'workflow-task-form-journal-v1',at:new Date().toISOString(),scope:copy(scope),form:mirror(form)};
 const last=storage.getItem(journal);if(last!==null){const previous=JSON.parse(last);if(same({...previous,at:null},{...record,at:null}))return;}
 writeBack(storage,journal,JSON.stringify(record));
}
export function readTaskFormJournals(storage,key,scope){
 const rows=[];for(let i=0;i<storage.length;i++){const name=storage.key(i);if(!name?.startsWith(key+':task-form-journal:'))continue;
  const row=JSON.parse(storage.getItem(name));need(object(row)&&row.schema==='workflow-task-form-journal-v1'&&object(row.scope)&&object(row.form));
  if(['actor_id','workspace_id','instance_id'].every(field=>row.scope[field]===scope[field]))rows.push({...row,key:name});
 }
 return rows.sort((a,b)=>b.at.localeCompare(a.at));
}
export async function lookupTaskCreation(api,scope,payload){
 const value=await api('/workspace/instances/'+encodeURIComponent(scope.instance_id)+'/task-requests/lookup',{method:'POST',body:taskCreationJSON(payload)});
 need(object(value)&&value.schema==='workflow-task-request-recovery-v1'&&typeof value.found==='boolean');
 if(!value.found){need(value.request===null);if(value.retirement)checkedTaskRetirement(value.retirement,scope,payload);return value;}
 const row=value.request;need(object(row)&&row.request_id===payload.request_id&&row.actor_id===scope.actor_id&&row.instance_id===scope.instance_id&&row.release_id===payload.release_id&&row.business_id===payload.business_id&&text(row.task_id)&&sha(row.creation_hash),'原发起回执与账号、对象或版本无法核对，请保留原请求。');return value;
}
function checkedTaskRetirement(value,scope,payload){
 need(object(value)&&value.schema==='workflow-task-request-retirement-v1'&&value.actor_id===scope.actor_id&&value.instance_id===scope.instance_id&&value.request_id===payload.request_id&&sameMirror(value.original,payload)&&sha(value.creation_hash)&&sha(value.command_hash)&&text(value.reason),'原请求停止回执与账号或原内容不一致，请保留原输入。');return value;
}
export async function retireTaskCreation(api,scope,payload,reason){
 const exact=originalRequests.get(payload),body=exact===undefined?JSON.stringify({original:payload,reason}):embedRequestJSON({reason},'original',taskCreationJSON(payload));
 const value=await api('/workspace/instances/'+encodeURIComponent(scope.instance_id)+'/task-requests/retire',{method:'POST',body});
 checkedTaskRetirement(value,scope,payload);need(value.reason===reason,'原停止原因不一致，原输入保留。');
 const saved=await lookupTaskCreation(api,scope,payload);need(!saved.found&&sameJSON(saved.retirement,value),'原请求停止记录尚未独立读回，请只核对原请求。');return value;
}
