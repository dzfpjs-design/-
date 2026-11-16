// Execution authoring shares the review controller's identity, durable scope,
// operation lock and leave guard. Server authority never comes from this state.
import {bindingWireValue,cloneBindings,newBindings,ensureNode,copy,array,object,EVENT_BINDINGS_SCHEMA,ANOMALY_TRIGGER_MODE} from './business-flow-execution-binding-fields.mjs';
import {parseTypedJSON,freshNumber} from './typed-numbers-v132.mjs';
import {graphWireParseCanonical,graphWireRequestJSON,graphWireTypedCopy,graphWireNumericTokens,graphWireRestoreNumericTokens,graphWireDigest} from './full-semantic-graph-wire.mjs';

export const EXECUTION_BASE='/setup/business-flow-executions';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,HASH=/^[a-f0-9]{64}$/,FLOW=/^[a-z][a-z0-9_-]{0,47}$/;
const VALUE_KEYS=['business_name','execution_bindings','buffers','role_bindings','node_actions','external_node_ids'];
const FLAGS=['semantic_quality_verified','can_apply','can_publish','can_execute'];
const need=(ok,message='执行草稿、保存回执或当前来源无法核对；原输入和原请求保留。')=>{if(!ok)throw Error(message);};
const integer=v=>Number.isSafeInteger(v)&&v>=0;
const plain=v=>JSON.parse(JSON.stringify(v));
const own=(value,key)=>object(value)&&Object.hasOwn(value,key);
const get=(value,key)=>own(value,key)?value[key]:undefined;
const put=(value,key,item)=>{Object.defineProperty(value,key,{value:item,enumerable:true,writable:true,configurable:true});return item;};
const qslot=id=>'qualification:'+id,slot=id=>'flow:'+id;
const formFor=(s,id)=>get(s.forms,slot(id));
const draftFor=(v,id)=>get(v.drafts,slot(id));
const idsFor=(v,id)=>get(v.nodeIds,slot(id));
const nodeFor=(value,id)=>get(value?.execution_bindings?.nodes,id);
const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
const flags=v=>FLAGS.every(key=>v?.[key]===false);
const wire=v=>graphWireRequestJSON(graphWireTypedCopy(v));
const same=(a,b)=>wire(a)===wire(b);
export async function executionRawHash(c,raw){need(typeof raw==='string');return [...new Uint8Array(await c.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(n=>n.toString(16).padStart(2,'0')).join('');}
export function executionDecode(raw){
 need(typeof raw==='string'&&new TextEncoder().encode(raw).length<=24*1024*1024,'服务器响应超过可无损核对的容量，原请求保留。');
 // Standard JSON is parsed into tokens first; never round-trip through .json().
 return graphWireParseCanonical(graphWireRequestJSON(parseTypedJSON(raw)));
}
function pin(q){need(object(q)&&UUID.test(q.id||'')&&HASH.test(q.qualification_hash||'')&&UUID.test(q.attempt_id||'')&&integer(q.draft_version)&&q.draft_version>=1,'请选择服务器已记录的具体作者核对版本。');return {id:q.id,qualification_hash:q.qualification_hash,attempt_id:q.attempt_id,draft_version:q.draft_version};}
function active(c,q){c.check();const p=pin(q);need(c.state.selected===p.attempt_id,'已切换业务稿，原执行草稿与请求保留在原版本。');return p;}
export function executionEditorState(c,q){return get(c.state.executionEditors,qslot(q.id))||get(c.state.executionEditors,q.id)||null;}
export function executionEditorView(c,q){return get(c.executionViews,qslot(q.id))||null;}
export function executionFlowDraft(c,q,id){const s=executionEditorState(c,q);return s?(formFor(s,id)||get(s.forms,id)||null):null;}
function state(c,q){
 const p=active(c,q);c.state.executionEditors??={};let s=get(c.state.executionEditors,qslot(p.id));
 if(!s){const old=get(c.state.executionEditors,p.id);s={schema:'business-flow-execution-editor-local-v2',qualification:p,selected:old?.selected||null,forms:{},pending:old?.pending?plain(old.pending):null,archives:[],records:[],preview:old?.preview?plain(old.preview):null,consent:old?.consent===true,share_source:old?.share_source===true};
  if(old){need(same(old.qualification,p));for(const [id,f]of Object.entries(old.forms||{})){const value=copy(f.value);graphWireRestoreNumericTokens(value,f.numeric_tokens||{});const restored={...plain(f),...pack(value)};delete restored.value;delete restored.numeric_tokens;put(s.forms,slot(id),restored);}s.archives=(old.archives||[]).map(wire);s.records=(old.records||[]).map(wire);if(s.pending?.handoff){s.pending.handoff_raw=wire(s.pending.handoff);delete s.pending.handoff;}delete c.state.executionEditors[p.id];}
  put(c.state.executionEditors,qslot(p.id),s);
 }
 need(same(s.qualification,p));return s;
}
export function executionEditorEntries(c){return Object.entries(c.state.executionEditors||{}).map(([key,s])=>{need(object(s),'本机旧执行稿目录无法读取，原记录保留。');const p=pin(s.qualification);need(key===qslot(p.id)||key===p.id,'本机旧资格目录与原版本不一致，未忽略旧稿。');return {qualification:p,selected_flow_id:s.selected,has_draft:Object.keys(s.forms||{}).length>0,pending:s.pending?{kind:s.pending.kind,flow_id:s.pending.flow_id,request_id:s.pending.request_id,receipt_saved:typeof s.pending.receipt_raw==='string'}:null};});}
export function executionPendingSelections(c){return executionEditorEntries(c).filter(entry=>entry.pending);}
export function executionEditorRecords(c,q){return (executionEditorState(c,q)?.records||[]).map(record=>typeof record==='string'?executionDecode(record):record);}
export function executionServerDraft(c,q,id){const v=executionEditorView(c,q);return v?draftFor(v,id):null;}
export function executionRuntimeId(c,q,flowId,nodeId){const v=executionEditorView(c,q);return v?get(idsFor(v,flowId),nodeId):undefined;}
function persist(c){need(c.persist(),c.error);}
function editable(c,q){active(c,q);executionEditorEntries(c);need(c.canEdit&&c.localSaved&&!c.pending,'请先核对当前账号并保护本机输入或原修订请求。');const s=state(c,q);need(!s.pending,'原执行保存或采用结果尚未核对，请先查询原号。');return s;}
function selected(c,q){const s=state(c,q),v=executionEditorView(c,q),id=s.selected;need(v?.context&&FLOW.test(id||'')&&formFor(s,id),'请选择这一核对版本中的实际流程。');const f=v.context.source_value.business.flows.find(item=>item.flow_id===id);need(f,'当前流程已不在所核对的来源中，原稿仍保留。');return {s,v,id,flow:f,form:formFor(s,id)};}
function current(c,q,id){active(c,q);need(state(c,q).selected===id,'已切换流程；原流程的请求和输入保留。');}
function validateValue(value){need(exact(value,VALUE_KEYS)&&typeof value.business_name==='string'&&object(value.execution_bindings)&&object(value.buffers)&&object(value.role_bindings)&&object(value.node_actions)&&Array.isArray(value.external_node_ids),'执行草稿的完整六项内容无法读取，原稿保留。');wire(value);}
function unpack(form){let value;if(typeof form.value_raw==='string')value=copy(executionDecode(form.value_raw));else{value=copy(form.value);graphWireRestoreNumericTokens(value,form.numeric_tokens||{});}validateValue(value);return value;}
function pack(value){validateValue(value);return {value_raw:wire(value)};}
function valueBody(form){const value=unpack(form);return {...graphWireTypedCopy(value),execution_bindings:bindingWireValue(value.execution_bindings,value.buffers)};}
export function executionFormValue(c,q){const s=executionEditorState(c,q),form=s?.selected&&(formFor(s,s.selected)||get(s.forms,s.selected));return form?unpack(form):null;}
export async function executionRuntimeNodeId(c,flowId,nodeId){need(FLOW.test(flowId)&&typeof nodeId==='string');return 'n_'+(await executionRawHash(c,JSON.stringify([flowId,nodeId]))).slice(0,24);}
function clearPreview(s){s.preview=null;s.consent=false;s.share_source=false;}
function transport(c,q,options){c.executionViews??={};let view=get(c.executionViews,qslot(q.id));if(!view)view=put(c.executionViews,qslot(q.id),{drafts:{},nodeIds:{},issues:[]});if(options){view.typedHTTP=options.typedHTTP;view.prepareConfiguration=options.prepareConfiguration;view.onConfigurationAdopted=options.onConfigurationAdopted;}return view;}
async function http(c,q,path,options={}){
 active(c,q);const v=transport(c,q);need(typeof v.typedHTTP==='function','无损读取接口尚未连接；本机输入保留，未退回普通JSON请求。');await c.authorize();active(c,q);
 let raw;try{raw=await v.typedHTTP(path,options);}catch(error){c.check();if([401,403].includes(error.status)){c.denied=true;c.identityStatus='concealed';c.notify();}else await c.authorize();throw error;}
 active(c,q);await c.authorize();active(c,q);return {raw,value:executionDecode(raw)};
}
function checkContext(c,q,value){
 need(value?.schema==='business-flow-execution-context-v1'&&value.qualification_id===q.id&&value.qualification_hash===q.qualification_hash&&integer(value.configuration_revision)&&value.configuration_revision>=1&&HASH.test(value.configuration_hash)&&value.can_apply===false&&value.can_execute===false&&object(value.source_value)&&Array.isArray(value.source_value.business?.flows)&&Array.isArray(value.adopted_flows),'服务器来源或已采用流程清单尚未就绪。');
 const seen=new Set();for(const f of value.source_value.business.flows){need(FLOW.test(f.flow_id||'')&&!seen.has(f.flow_id)&&typeof f.name==='string'&&Array.isArray(f.nodes));seen.add(f.flow_id);}
 for(const a of value.adopted_flows)need(seen.has(a.flow_id)&&typeof a.business_name==='string'&&UUID.test(a.adoption_id||'')&&integer(a.accepted_revision)&&a.accepted_revision>=1&&a.qualification_id===q.id&&a.qualification_hash===q.qualification_hash&&HASH.test(a.binding_hash));
 return value;
}
async function loadContext(c,q){const response=await http(c,q,EXECUTION_BASE+'/qualifications/'+q.id+'/context');const ctx=checkContext(c,q,response.value),s=state(c,q),v=transport(c,q);
 if(v.context&&(ctx.configuration_revision!==v.context.configuration_revision||ctx.configuration_hash!==v.context.configuration_hash))clearPreview(s);
 v.context=ctx;v.contextRaw=response.raw;
 for(const f of ctx.source_value.business.flows){const ids=Object.create(null);put(v.nodeIds,slot(f.flow_id),ids);for(const n of f.nodes)put(ids,n.node_id,await executionRuntimeNodeId(c,f.flow_id,n.node_id));}
 active(c,q);return ctx;
}
async function checkDraft(c,q,id,draft){
 need(draft?.schema==='business-flow-execution-draft-view-v1'&&draft.actor_id===c.scope.actorId&&draft.workspace_id===c.scope.workspaceId&&draft.qualification_id===q.id&&draft.qualification_hash===q.qualification_hash&&draft.flow_id===id&&typeof draft.saved==='boolean'&&integer(draft.version)&&Array.isArray(draft.history)&&flags(draft));
 if(!draft.saved)need(draft.version===0&&draft.id===null&&draft.value===null&&draft.value_hash===null&&draft.saved_at===null&&draft.history.length===0);
 else{need(UUID.test(draft.id||'')&&draft.version>=1&&HASH.test(draft.value_hash)&&typeof draft.saved_at==='string');validateValue(draft.value);need(await graphWireDigest(draft.value,{crypto:c.crypto})===draft.value_hash,'服务器草稿数值或内容摘要不一致，未替换本机输入。');need(draft.history.length===draft.version);for(let i=0;i<draft.history.length;i++){const h=draft.history[i];need(h.version===i+1&&UUID.test(h.request_id||'')&&typeof h.saved_at==='string'&&HASH.test(h.value_hash));}need(draft.history.at(-1).value_hash===draft.value_hash);}
 return draft;
}
async function loadDraft(c,q,id){
 const response=await http(c,q,EXECUTION_BASE+'/qualifications/'+q.id+'/flows/'+encodeURIComponent(id)+'/draft'),dto=await checkDraft(c,q,id,response.value),s=state(c,q),v=transport(c,q);
 put(v.drafts,slot(id),dto);const flow=v.context.source_value.business.flows.find(f=>f.flow_id===id);need(flow);
 if(!formFor(s,id))put(s.forms,slot(id),{...pack(dto.saved?dto.value:{business_name:flow.name,execution_bindings:newBindings(flow),buffers:{},role_bindings:{},node_actions:{},external_node_ids:[]}),version:dto.version,server_hash:dto.value_hash,generation:0,dirty:!dto.saved,remoteConflict:false,server_raw:response.raw});
 else{const form=formFor(s,id);if(dto.version===form.version){need(form.server_hash===dto.value_hash,'同一保存版本的服务器草稿内容已变化，原稿保留。');form.remoteConflict=false;}else form.remoteConflict=true;form.server_raw=response.raw;}
 persist(c);return dto;
}
export function openExecutionEditor(c,q,options){
 active(c,q);const view=transport(c,q,options);if(view.openPromise)return view.openPromise;
 view.openPromise=c.operation(()=>c.locked(async()=>{
  const s=state(c,q);await loadContext(c,q);
  const memberReply=await http(c,q,'/instances/members'),abilityReply=await http(c,q,'/workspace/ability-options');
  need(Array.isArray(memberReply.value)&&memberReply.value.every(m=>UUID.test(m.id||'')&&typeof m.name==='string'&&['admin','operator'].includes(m.role)),'实际成员清单无法核对。');
  need(Array.isArray(abilityReply.value)&&abilityReply.value.every(a=>UUID.test(a.id||'')&&HASH.test(a.content_hash||'')&&typeof a.name==='string'&&typeof a.release_version==='string'&&Array.isArray(a.agents)&&object(a.input_schema)&&object(a.output_schema)),'已发布能力清单无法核对。');
  view.members=memberReply.value;view.abilities=abilityReply.value;view.membersRaw=memberReply.raw;view.abilitiesRaw=abilityReply.raw;
  if(s.selected){need(view.context.source_value.business.flows.some(f=>f.flow_id===s.selected));await loadDraft(c,q,s.selected);}
  persist(c);c.notify();return view.context;
 })).finally(()=>{view.openPromise=null;});return view.openPromise;
}
export async function selectExecutionFlow(c,q,id){return c.operation(()=>c.locked(async()=>{const s=editable(c,q),v=transport(c,q);need(v.context?.source_value.business.flows.some(f=>f.flow_id===id),'请选择当前核对版本中的流程。');s.selected=id;clearPreview(s);persist(c);await loadDraft(c,q,id);c.notify();}));}
export function editExecutionValue(c,q,next){
 need(!c.busy,'当前请求尚未核对，输入保持原状。');const s=editable(c,q),{v,id,form}=selected(c,q);validateValue(next);const value=copy(next),clear=array(value.buffers.clear_external_node_ids);
 if(clear.length){const ids=clear.map(node=>get(idsFor(v,id),node)).filter(Boolean);value.external_node_ids=value.external_node_ids.filter(node=>!ids.includes(node));value.buffers.external_consent_cleared_history=[...array(value.buffers.external_consent_cleared_history),...clear.map(node=>({source_node_id:node}))];value.buffers.clear_external_node_ids=[];}
 Object.assign(form,pack(value));form.generation++;form.dirty=true;clearPreview(s);v.issues=[];c.error='';c.persist();c.notify();
}
export function editExecutionBindings(c,q,bindings,metadata){const value=executionFormValue(c,q);need(value&&object(metadata?.buffers));value.execution_bindings=cloneBindings(bindings,metadata.buffers);value.buffers=copy(metadata.buffers);editExecutionValue(c,q,value);}
export function acknowledgeExecutionNode(c,q,nodeId){const value=executionFormValue(c,q),review=get(value?.buffers.node_reviews,nodeId);need(review?.required===true);put(value.buffers.node_reviews,nodeId,{...review,required:false,acknowledged:true});editExecutionValue(c,q,value);}
function roleNodes(c,q,role,actorId){const {flow,v}=selected(c,q),nodes=flow.nodes.filter(n=>n.role===role);need(nodes.length&&(!actorId||v.members?.some(m=>m.id===actorId)),'岗位或实际成员已不在当前清单中。');return nodes;}
function applyRole(value,nodes,role,actorId){
 let bindings=value.execution_bindings;for(const node of nodes){bindings=ensureNode(bindings,node);get(bindings.nodes,node.node_id).actor_id=actorId;}
 value.execution_bindings=bindings;if(actorId)put(value.role_bindings,role,actorId);else delete value.role_bindings[role];
 if(value.buffers.role_change_requests)delete value.buffers.role_change_requests[role];return value;
}
export function chooseExecutionRole(c,q,role,actorId){
 const value=executionFormValue(c,q),nodes=roleNodes(c,q,role,actorId),conflicts=nodes.filter(n=>nodeFor(value,n.node_id)?.actor_id&&nodeFor(value,n.node_id).actor_id!==actorId);
 if(actorId&&conflicts.length){value.buffers.role_change_requests??={};put(value.buffers.role_change_requests,role,{actor_id:actorId,previous_actors:Object.fromEntries(nodes.map(n=>[n.node_id,nodeFor(value,n.node_id)?.actor_id||''])),conflicting_node_ids:conflicts.map(n=>n.node_id)});editExecutionValue(c,q,value);return {conflict:true};}
 editExecutionValue(c,q,applyRole(value,nodes,role,actorId));return {conflict:false};
}
export function confirmExecutionRole(c,q,role){
 const value=executionFormValue(c,q),pending=get(value?.buffers.role_change_requests,role);need(pending,'没有待确认的岗位负责人变更。');const nodes=roleNodes(c,q,role,pending.actor_id);
 need(nodes.every(n=>(nodeFor(value,n.node_id)?.actor_id||'')===get(pending.previous_actors,n.node_id)),'岗位中的步骤账号在对照期间又有修改，原选择保留；请重新选择岗位负责人。');
 value.buffers.previous_role_assignments=[...array(value.buffers.previous_role_assignments),{role,role_actor_id:get(value.role_bindings,role)||'',previous_actors:copy(pending.previous_actors)}];editExecutionValue(c,q,applyRole(value,nodes,role,pending.actor_id));
}
export function cancelExecutionRole(c,q,role){const value=executionFormValue(c,q);need(get(value?.buffers.role_change_requests,role));delete value.buffers.role_change_requests[role];editExecutionValue(c,q,value);}
export function executionReadiness(value){
 const issues=[];if(!value?.execution_bindings)issues.push('尚未逐项填写执行绑定。');
 else if(Object.values(value.execution_bindings.nodes||{}).some(node=>!object(node)))issues.push('还有原步骤尚未明确实际执行绑定；可以先保存草稿。');
 if(value?.execution_bindings?.schema===EVENT_BINDINGS_SCHEMA&&value.execution_bindings.trigger?.mode!==ANOMALY_TRIGGER_MODE)issues.push('请对照原流程明确选择异常信号启动方式；保存草稿不会建立实例授权。');
 if(Object.keys(value?.buffers?.role_change_requests||{}).length)issues.push('有岗位负责人变更尚未明确确认；原步骤选择仍保留。');
 for(const [id,r] of Object.entries(value?.buffers?.node_reviews||{}))if(r?.required===true)issues.push('步骤 '+id+' 更换能力后尚未明确重新核对。');
 for(const n of Object.values(value?.buffers?.number_texts||{}))try{need(object(n)&&typeof n.text==='string');freshNumber(n.text,n.kind||'number');need(!n.error);}catch{issues.push('有数值尚未填写完整；可以保存草稿，暂不能预览采用。');break;}
 return issues;
}
export function executionDraftValue(c,q){return selected(c,q).form;}
function pendingExact(c,q,p){
 const s=state(c,q),stored=get(c.readOwn().executionEditors,qslot(q.id)),original=stored?.pending;
 need(s.pending?.raw===p.raw&&s.pending.sha===p.sha&&original?.raw===p.raw&&original.sha===p.sha&&original.path===p.path&&p.qualification_id===q.id&&p.qualification_hash===q.qualification_hash&&['save','adopt'].includes(p.kind)&&FLOW.test(p.flow_id)&&UUID.test(p.request_id)&&HASH.test(p.sha),'原执行请求与本机读回不一致，禁止替换或重发。');
 need(p.path===EXECUTION_BASE+(p.kind==='save'?'/drafts/save':'/adopt'));
 return s;
}
async function checkPending(c,q,p){pendingExact(c,q,p);need(await executionRawHash(c,p.raw)===p.sha,'原执行请求摘要不一致。');const body=executionDecode(p.raw);need(body.request_id===p.request_id&&body.qualification_id===q.id&&body.qualification_hash===q.qualification_hash&&body.flow_id===p.flow_id);return body;}
async function makePending(c,q,kind,body,generation){
 const raw=graphWireRequestJSON(body);need(new TextEncoder().encode(raw).length<=2*1024*1024,'完整请求超过2MiB；原输入未截断，请先导出保留。');const p={kind,qualification_id:q.id,qualification_hash:q.qualification_hash,flow_id:body.flow_id,request_id:body.request_id,path:EXECUTION_BASE+(kind==='save'?'/drafts/save':'/adopt'),raw,sha:await executionRawHash(c,raw),generation,attempted:false,rejection:null,receipt_raw:null};
 const s=state(c,q);need(!s.pending);s.pending=p;persist(c);pendingExact(c,q,p);return p;
}
function rejection(error,p){const d=error.detail;return [409,422].includes(error.status)&&d?.not_applied===true&&d.request_id===p.request_id&&d.body_sha256===p.sha&&((p.kind==='save'&&d.code==='business_flow_execution_draft_version_conflict'&&integer(d.current_version))||(p.kind==='adopt'&&d.code==='business_flow_execution_adoption_not_applied'));}
async function checkSaveResult(c,q,p,result){const body=await checkPending(c,q,p);need(result?.schema==='business-flow-execution-draft-save-result-v1'&&result.request_id===p.request_id&&result.body_sha256===p.sha&&flags(result));await checkDraft(c,q,p.flow_id,result.draft);need(result.draft.saved&&result.draft.version===body.expected_version+1&&same(result.draft.value,body.value),'保存结果与原完整数值、版本或请求不一致。');return result.draft;}
async function acceptSaved(c,q,p,response){
 const dto=await checkSaveResult(c,q,p,response.value),s=pendingExact(c,q,p),form=formFor(s,p.flow_id);need(form);const before=plain(s);
 if(form.generation===p.generation){Object.assign(form,pack(dto.value));form.dirty=false;}else form.dirty=true;
 form.version=dto.version;form.server_hash=dto.value_hash;form.remoteConflict=false;form.server_raw=response.raw;
 // A recovery caller may hold a pre-query copy. Preserve the actual current
 // outbox, including the just-read original GET bytes, not that older copy.
 s.records.push(wire({kind:'save',request:plain(s.pending),response_raw:response.raw}));s.pending=null;
 if(!c.persist()){put(c.state.executionEditors,qslot(q.id),before);throw Error('服务器已保存，但本机结清失败；原号仍保留，请重新查询。');}
 put(transport(c,q).drafts,slot(p.flow_id),dto);c.notice='执行草稿已保存并读回原回执；尚未采用、启用或执行。';c.notify();return dto;
}
async function checkAdoption(c,q,p,result){const body=await checkPending(c,q,p);need(result?.schema==='business-flow-execution-adoption-result-v1'&&result.request_id===p.request_id&&result.qualification_id===q.id&&UUID.test(result.id||'')&&result.accepted_revision===body.configuration_revision+1&&HASH.test(result.configuration_hash)&&HASH.test(result.binding_hash)&&result.can_execute===false&&result.origin?.schema==='business-flow-execution-origin-v1'&&result.origin.adoption_id===result.id&&result.origin.business_name===body.business_name&&result.origin.binding_hash===result.binding_hash&&HASH.test(result.origin.command_hash));need(result.binding_hash===p.binding_hash,'采用回执不是已预览的绑定。');return result;}
async function acceptAdoption(c,q,p,response){
 const result=await checkAdoption(c,q,p,response.value),s=pendingExact(c,q,p);s.pending.receipt_raw=response.raw;persist(c);
 // No second POST after a verified original receipt, even if downstream view
 // synchronisation, local disk or a parent callback fails.
 const readback=await http(c,q,'/setup/draft'),saved=readback.value;
 need(integer(saved.revision)&&saved.revision>=result.accepted_revision&&object(saved.configuration),'当前配置尚未读回原采用版本。');
 need(same(saved.configuration.businessFlowExecutions?.[result.origin.business_name],result.origin),'当前配置已不含同一采用来源，原回执保留。');
 if(saved.revision===result.accepted_revision)need(await graphWireDigest(saved.configuration,{crypto:c.crypto})===result.configuration_hash,'当前配置完整数值摘要与采用回执不一致。');
 const numeric_tokens=graphWireNumericTokens(saved),savedPlain=plain(saved),lossy=Object.entries(numeric_tokens).some(([path,token])=>{let value=savedPlain;for(const k of path.slice(1).split('/').map(p=>p.replaceAll('~1','/').replaceAll('~0','~')))value=value?.[k];return JSON.stringify(value)!==token;});
 const handoff={raw:readback.raw,canonical:wire(saved),numeric_tokens:plain(numeric_tokens),requires_lossless_sync:lossy};
 s.pending.configuration_raw=readback.raw;s.pending.handoff_raw=wire(handoff);persist(c);
 const callback=transport(c,q).onConfigurationAdopted;
 if(typeof callback!=='function'){c.notice='此流程的采用已存入服务器；当前页面尚未连接配置同步，原号和其他流程保留。';c.notify();return {adopted:true,synchronised:false,result};}
 const accepted=await callback(savedPlain,{actor_id:c.scope.actorId,workspace_id:c.scope.workspaceId},plain(result),handoff);active(c,q);pendingExact(c,q,p);
 if(accepted?.accepted!==true||lossy&&accepted?.preserved_numeric_tokens!==true){c.notice=accepted?.reason||'此流程采用已保存；无损配置同步仍待接通，未重新发送或启用。';c.notify();return {adopted:true,synchronised:false,result};}
 const before=plain(s);s.records.push(wire({kind:'adopt',request:plain(s.pending),response_raw:response.raw,configuration_raw:readback.raw}));s.pending=null;clearPreview(s);
 if(!c.persist()){put(c.state.executionEditors,qslot(q.id),before);throw Error('采用已保存，但本机同步记录尚未结清；原号仍保留。');}
 // The candidate list remains open. Refreshing context cannot relabel the
 // other flows as adopted merely because this one was accepted.
 await loadContext(c,q);c.notice='此流程已加入配置，尚未生成、启用或执行；其他流程仍可继续配置。';c.notify();return {adopted:true,synchronised:true,result};
}
async function resolve(c,q,p,send){
 const body=await checkPending(c,q,p),path=EXECUTION_BASE+(p.kind==='save'?'/draft-requests/':'/requests/')+p.request_id+'?expected_sha256='+p.sha;
 const probe=await http(c,q,path),r=probe.value;pendingExact(c,q,p);
 need(r?.schema===(p.kind==='save'?'business-flow-execution-draft-original-result-v1':'business-flow-execution-original-result-v1')&&r.request_id===p.request_id&&r.body_sha256===p.sha&&typeof r.found==='boolean');
 if(r.found){need(object(r.result));state(c,q).pending.original_query_raw=probe.raw;persist(c);const recovered={raw:wire(r.result),value:r.result};return p.kind==='save'?acceptSaved(c,q,p,recovered):acceptAdoption(c,q,p,recovered);}
 need(r.result===null);if(p.receipt_raw){c.notice='此前已核对保存的原回执本次未查到，原证据保留；不能重新发送。';c.notify();return {found:false};}
 if(!send){c.notice='尚未查到原请求，不表示请求未到达；原编号与完整原文保留。';c.notify();return {found:false};}
 need(!p.rejection&&c.canEdit&&c.localSaved,'原请求已明确拒绝或本机未保护，未重发。');need(body.schema===(p.kind==='save'?'business-flow-execution-draft-command-v1':'business-flow-execution-adopt-command-v1'));
 p={...p,attempted:true};state(c,q).pending=p;persist(c);pendingExact(c,q,p);
 let response;try{response=await http(c,q,p.path,{method:'POST',body:p.raw});}
 catch(error){pendingExact(c,q,p);if(rejection(error,p)){state(c,q).pending={...p,rejection:plain(error.detail)};persist(c);}throw error;}
 pendingExact(c,q,p);
 if(p.kind==='save'){await checkSaveResult(c,q,p,response.value);state(c,q).pending.receipt_raw=response.raw;state(c,q).pending.direct_response_raw=response.raw;persist(c);}
 else{await checkAdoption(c,q,p,response.value);state(c,q).pending.receipt_raw=response.raw;state(c,q).pending.direct_response_raw=response.raw;persist(c);}
 // A direct mutation response is retained before querying the independent
 // original-number endpoint. A failed GET leaves the same outbox intact.
 return resolve(c,q,state(c,q).pending,false);
}
export async function saveExecutionDraft(c,q){return c.operation(()=>c.locked(async()=>{
 const s=editable(c,q),{id,form}=selected(c,q);need(!form.remoteConflict,'服务器已有另一版执行草稿，请先对照再明确对齐。');
 const body={schema:'business-flow-execution-draft-command-v1',request_id:c.crypto.randomUUID(),qualification_id:q.id,qualification_hash:q.qualification_hash,flow_id:id,expected_version:form.version,value:valueBody(form)};
 const p=await makePending(c,q,'save',body,form.generation);current(c,q,id);return resolve(c,q,p,true);
}));}
export async function recoverExecutionRequest(c,q,send=false,options){return c.operation(()=>{if(options)transport(c,q,options);const work=async()=>{const p=state(c,q).pending;need(p,'没有待核对的执行原请求。');return resolve(c,q,plain(p),send);};return send?c.locked(work):work();});}
export function archiveExecutionRejected(c,q){need(!c.busy);const s=state(c,q),p=s.pending;need(c.canEdit&&c.localSaved&&p?.rejection&&rejection({status:p.kind==='save'?409:422,detail:p.rejection},p),'未证明原请求未应用，不能另起编号。');s.archives.push(wire(p));s.pending=null;clearPreview(s);persist(c);c.notice='被明确拒绝的原请求已保留；请读取服务器现稿并核对后继续。';c.notify();}
export async function refreshExecutionDraft(c,q){return c.operation(()=>c.locked(async()=>{const {id}=selected(c,q);await loadContext(c,q);await loadDraft(c,q,id);c.notify();}));}
export function alignExecutionDraft(c,q,useServer=false){need(!c.busy);const s=editable(c,q),{id,form,v}=selected(c,q),dto=draftFor(v,id);need(dto&&dto.version>=form.version,'请先读取服务器现稿。');s.archives.push(wire({kind:'local_draft_before_alignment',flow_id:id,form:plain(form)}));
 if(useServer){need(dto.saved,'服务器尚无已保存执行草稿。');Object.assign(form,pack(dto.value));form.dirty=false;}else form.dirty=true;
 form.version=dto.version;form.server_hash=dto.value_hash;form.remoteConflict=false;form.generation++;clearPreview(s);persist(c);c.notify();
}
export async function previewExecution(c,q){return c.operation(()=>c.locked(async()=>{
 editable(c,q);let {s,v,id,form}=selected(c,q);need(!form.dirty&&form.version>=1&&!form.remoteConflict,'请先保存当前执行草稿并读取这一版本，再检查采用。');
 const readiness=executionReadiness(unpack(form));need(!readiness.length,readiness.join(' '));need(typeof v.prepareConfiguration==='function','当前配置的保存接缝尚未连接；可保存草稿，暂不能预览采用。');
 clearPreview(s);persist(c);await v.prepareConfiguration();current(c,q,id);await loadContext(c,q);await loadDraft(c,q,id);current(c,q,id);({s,v,form}=selected(c,q));need(!form.remoteConflict&&!form.dirty,'服务器草稿已有变化，请对照后重新检查。');
 const actual=await http(c,q,'/setup/draft');need(actual.value.revision===v.context.configuration_revision&&await graphWireDigest(actual.value.configuration,{crypto:c.crypto})===v.context.configuration_hash,'配置在读取期间已变化，请重新检查。');
 const value=valueBody(form),command={schema:'business-flow-execution-preview-command-v1',qualification_id:q.id,qualification_hash:q.qualification_hash,flow_id:id,business_name:value.business_name,configuration_revision:v.context.configuration_revision,configuration_hash:v.context.configuration_hash,execution_bindings:value.execution_bindings,role_bindings:value.role_bindings,node_actions:value.node_actions,external_node_ids:value.external_node_ids};
 const raw=graphWireRequestJSON(command);need(new TextEncoder().encode(raw).length<=2*1024*1024,'预览请求超过2MiB，原稿保留。');
 let response;try{response=await http(c,q,EXECUTION_BASE+'/preview',{method:'POST',body:raw});}catch(error){v.issues=Array.isArray(error.detail?.issues)?plain(error.detail.issues):[];c.notify();throw error;}
 current(c,q,id);const p=response.value;need(p?.schema==='business-flow-execution-preview-v1'&&p.actor_id===c.scope.actorId&&p.workspace_id===c.scope.workspaceId&&HASH.test(p.preview_hash)&&p.can_execute===false&&p.semantic_quality_verified===false&&Array.isArray(p.unselected_flow_ids)&&same(p.command,executionDecode(raw))&&object(p.binding)&&HASH.test(p.binding.binding_hash));
 need(await graphWireDigest(p,{omit:'preview_hash',crypto:c.crypto})===p.preview_hash,'执行预览完整内容摘要不一致。');
 const mapping=p.binding.translation?.source_mapping?.nodes,sourceNodes=v.context.source_value.business.flows.find(f=>f.flow_id===id).nodes;
 need(Array.isArray(mapping)&&mapping.length===sourceNodes.length&&new Set(mapping.map(n=>n.source_node_id)).size===sourceNodes.length,'预览没有保留每个原步骤的转换对应。');
 for(const n of mapping)need(get(idsFor(v,id),n.source_node_id)===n.graph_node_id,'转换后的步骤对应与原流程不一致。');
 const others=v.context.source_value.business.flows.filter(f=>f.flow_id!==id).map(f=>f.flow_id);need(p.unselected_flow_ids.length===others.length&&new Set(p.unselected_flow_ids).size===others.length&&p.unselected_flow_ids.every(key=>others.includes(key)),'预览中的未选流程清单不完整。');
 s.preview={raw:response.raw,command_raw:raw,flow_id:id,form_generation:form.generation,draft_version:form.version};s.consent=false;s.share_source=false;v.issues=[];persist(c);c.notice='已检查实际执行安排，请对照原流程、负责人、能力及共享范围后明确选择。';c.notify();return p;
}));}
export function setExecutionConsent(c,q,key,value){need(!c.busy);const s=editable(c,q);need(s.preview&&['consent','share_source'].includes(key)&&typeof value==='boolean');s[key]=value;persist(c);c.notify();}
export async function adoptExecution(c,q){return c.operation(()=>c.locked(async()=>{
 const s=editable(c,q),{id,form}=selected(c,q);need(s.preview&&s.preview.flow_id===id&&s.preview.form_generation===form.generation&&s.preview.draft_version===form.version&&!form.dirty&&!form.remoteConflict&&s.consent===true&&s.share_source===true,'请先检查已保存版本，并明确采用及共享范围。');need(!executionReadiness(unpack(form)).length);
 const preview=executionDecode(s.preview.raw),command=executionDecode(s.preview.command_raw),body={...graphWireTypedCopy(command),schema:'business-flow-execution-adopt-command-v1',request_id:c.crypto.randomUUID(),preview_hash:preview.preview_hash,consent:true,share_source:true};
 const p=await makePending(c,q,'adopt',body,form.generation);p.binding_hash=preview.binding.binding_hash;state(c,q).pending=p;persist(c);return resolve(c,q,p,true);
}));}
export function executionPendingAdoption(c,q){const s=executionEditorState(c,q);if(s?.pending?.kind!=='adopt'||!s.pending.receipt_raw)return null;const r=executionDecode(s.pending.receipt_raw);return r.result||r;}
export function exportExecutionDraft(c,q){active(c,q);need(c.identityStatus==='verified');return JSON.stringify({scope:c.scope,qualification:pin(q),editor:executionEditorState(c,q)},null,2);}
