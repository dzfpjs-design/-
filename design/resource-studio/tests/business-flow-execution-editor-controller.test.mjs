import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {BusinessFlowReviewController} from '../src/business-flow-review-controller.mjs';
import * as editor from '../src/business-flow-execution-editor.mjs';
import {newBindings,ensureNode,addHumanOutput,numericEdit} from '../src/business-flow-execution-binding-fields.mjs';
import {graphWireClone,graphWireTypedCopy,graphWireRequestJSON,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
import {freshNumber} from '../src/typed-numbers-v132.mjs';

// Real review controller, pure editor and lossless raw transport seam. These
// synthetic receipts do not assert a real API/DB, source qualification or run.
const id=n=>'88000000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=x=>JSON.parse(JSON.stringify(x));
const scope={actorId:id(1),workspaceId:id(2),producerId:id(3)},attempt=id(4);
const qualification={id:id(5),qualification_hash:'a'.repeat(64),attempt_id:attempt,draft_version:1};
const falseFlags={semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
const raw=x=>graphWireRequestJSON(graphWireTypedCopy(x));
async function hash(x){return graphWireDigest(editor.executionDecode(raw(x)),{crypto:webcrypto});}
class Memory{constructor(){this.map=new Map();}getItem(k){return this.map.get(k)??null;}setItem(k,v){this.map.set(k,String(v));}removeItem(k){this.map.delete(k);}}
const source={business:{name:'公开合成范围',flows:['first','second'].map((flow_id,i)=>({flow_id,name:i?'后续独立流程':'人工登记流程',trigger:{kind:'manual',description:'操作员明确登记'},start_node:'receive',end_nodes:['receive'],nodes:[{node_id:'receive',name:'登记实际核对记录',actor:'human',role:'核对员',responsibility:'保存实际核对结论',inputs:[],outputs:['核对记录']}],edges:[],deliverables:[{name:'核对记录',description:'实际核对记录',produced_by:['receive']}]}))},evidence:[]};
async function fixture(scene=source){
 const storage=new Memory(),session=new Memory(),calls=[],db={drafts:Object.create(null),draftRequests:Object.create(null),adoptRequests:Object.create(null),adopted:[],configuration:{businesses:[],flows:{},businessFlowExecutions:{}},revision:1,loseSave:false,unknownSave:false,badSHA:false,conflict:false,losslessAccepted:false,callbacks:[],prepared:0};
 for(const[k,v]of Object.entries({'ga-setup-csrf-user':scope.actorId,'ga-setup-csrf-workspace':scope.workspaceId,'ga-setup-csrf':'synthetic-token'}))session.setItem(k,v);
 const api=async(path,options={})=>{
  assert.equal(options.headers['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers['X-CSRF-Token'],'synthetic-token');
  if(path==='/me')return {user_id:scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  if(path.includes('/candidates?'))return {items:[{attempt_id:attempt,name:'公开合成范围',status:'failed',saved_version:1}]};
  if(path==='/setup/business-flow-reviews/attempts/'+attempt)return {attempt_id:attempt,version:1,value:copy(scene),note:'已保存的合成修订',baseline_value:copy(scene),source_available:true,saved:true,diagnostics:{issues:[]},...falseFlags};
  throw Error('unexpected parent JSON path '+path);
 };
 const typedHTTP=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',raw:options.body});
  if(path.endsWith('/context'))return raw({schema:'business-flow-execution-context-v1',qualification_id:qualification.id,qualification_hash:qualification.qualification_hash,source_value:scene,configuration_revision:db.revision,configuration_hash:await hash(db.configuration),current_source_checked:false,can_apply:false,can_execute:false,adopted_flows:db.adopted});
  if(path==='/instances/members')return raw([{id:scope.actorId,name:'实际合成操作员',role:'operator'},{id:id(20),name:'另一实际操作员',role:'operator'}]);
  if(path==='/workspace/ability-options')return raw([]);
  if(path==='/setup/draft')return raw({id:id(7),revision:db.revision,configuration:db.configuration});
  if(path.includes('/flows/')&&path.endsWith('/draft')){const flow=path.split('/').at(-2);return raw(db.drafts[flow]||{schema:'business-flow-execution-draft-view-v1',id:null,actor_id:scope.actorId,workspace_id:scope.workspaceId,qualification_id:qualification.id,qualification_hash:qualification.qualification_hash,flow_id:flow,saved:false,version:0,value:null,value_hash:null,saved_at:null,history:[],...falseFlags});}
  if(path.includes('/draft-requests/')||path.includes('/requests/')){
   const [route,query]=path.split('?'),nonce=route.split('/').at(-1),sha=new URLSearchParams(query).get('expected_sha256'),draft=path.includes('/draft-requests/'),result=(draft?db.draftRequests:db.adoptRequests)[nonce];
   return raw({schema:draft?'business-flow-execution-draft-original-result-v1':'business-flow-execution-original-result-v1',request_id:nonce,found:!!result,body_sha256:db.badSHA?'f'.repeat(64):sha,result:result||null});
  }
  const s=editor.executionEditorState(c,qualification),body=editor.executionDecode(options.body||'{}');
  if(path.endsWith('/drafts/save')){
   assert.equal(options.method,'POST');const disk=editor.executionEditorState({state:JSON.parse(storage.getItem(c.key))},qualification).pending;assert.equal(disk.raw,options.body,'complete exact outbox read back before POST');
   assert.equal(await editor.executionRawHash(c,disk.raw),disk.sha);
   if(db.conflict){const error=Error('合成CAS冲突');error.status=409;error.detail={code:'business_flow_execution_draft_version_conflict',not_applied:true,request_id:body.request_id,body_sha256:disk.sha,current_version:2};throw error;}
   if(db.unknownSave)throw Error('合成：原保存未得到回执');
   assert.equal(body.expected_version,db.drafts[body.flow_id]?.version||0);
   const history=[...(db.drafts[body.flow_id]?.history||[]),{version:body.expected_version+1,request_id:body.request_id,saved_at:'2026-10-24T00:00:00Z',value_hash:await hash(body.value)}];
   const draft={schema:'business-flow-execution-draft-view-v1',id:id(8),actor_id:scope.actorId,workspace_id:scope.workspaceId,qualification_id:qualification.id,qualification_hash:qualification.qualification_hash,flow_id:body.flow_id,saved:true,version:body.expected_version+1,value:body.value,value_hash:await hash(body.value),saved_at:'2026-10-24T00:00:00Z',history,...falseFlags};
   const result={schema:'business-flow-execution-draft-save-result-v1',request_id:body.request_id,body_sha256:disk.sha,draft,...falseFlags};db.drafts[body.flow_id]=draft;db.draftRequests[body.request_id]=result;
   if(db.loseSave)throw Error('合成：已保存但响应丢失');return raw(result);
  }
  if(path.endsWith('/preview')){
   const mapping=[];for(const [index,n] of scene.business.flows.find(f=>f.flow_id===body.flow_id).nodes.entries())mapping.push({source_path:'/business/flows/0/nodes/'+index,source_node_id:n.node_id,source_hash:await hash(n),graph_node_id:await editor.executionRuntimeNodeId(c,body.flow_id,n.node_id),input_ports:[],output_ports:[]});
   const translation={schema:'business-flow-execution-graph-v1',source_mapping:{nodes:mapping}};if(db.oldMappingAlias){translation.mapping=translation.source_mapping;delete translation.source_mapping;}
   const preview={schema:'business-flow-execution-preview-v1',actor_id:scope.actorId,workspace_id:scope.workspaceId,command:body,binding:{binding_hash:'c'.repeat(64),translation},scope:'只加入选定流程，不执行设备。',unselected_flow_ids:scene.business.flows.filter(f=>f.flow_id!==body.flow_id).map(f=>f.flow_id),semantic_quality_verified:false,can_execute:false};return raw({...preview,preview_hash:await hash(preview)});
  }
  if(path.endsWith('/adopt')){
   assert.ok(s.pending);assert.equal(s.pending.raw,options.body);assert.equal(db.adoptRequests[body.request_id],undefined,'no repeated paid or write activity under a new nonce');
   const origin={schema:'business-flow-execution-origin-v1',adoption_id:id(10),business_name:body.business_name,binding_hash:s.pending.binding_hash,command_hash:await hash(body)};
   db.configuration={...db.configuration,businesses:[...db.configuration.businesses,body.business_name],flows:{...db.configuration.flows,[body.business_name]:{source_flow:scene.business.flows.find(f=>f.flow_id===body.flow_id)}},businessFlowExecutions:{...db.configuration.businessFlowExecutions,[body.business_name]:origin}};db.revision++;
   const result={schema:'business-flow-execution-adoption-result-v1',id:id(10),request_id:body.request_id,qualification_id:qualification.id,origin,accepted_revision:db.revision,configuration_hash:await hash(db.configuration),binding_hash:s.pending.binding_hash,can_execute:false};db.adoptRequests[body.request_id]=result;
   db.adopted.push({flow_id:body.flow_id,business_name:body.business_name,adoption_id:id(10),accepted_revision:db.revision,qualification_id:qualification.id,qualification_hash:qualification.qualification_hash,binding_hash:s.pending.binding_hash});return raw(result);
  }
  throw Error('unexpected lossless path '+path);
 };
 const make=()=>new BusinessFlowReviewController({...scope,api,storage,session,crypto:webcrypto,locks:{request:async(_name,_opts,fn)=>fn()}});let c=make();await c.open();await c.select(attempt);
 const options={typedHTTP,prepareConfiguration:async()=>{db.prepared++;},onConfigurationAdopted:async(saved,actualScope,receipt,handoff)=>{db.callbacks.push({saved,scope:actualScope,receipt,handoff});return db.losslessAccepted?{accepted:true,preserved_numeric_tokens:true}:{accepted:false,reason:'原采用已保存，无损配置同步尚未接通。'};}};
 await editor.openExecutionEditor(c,qualification,options);await editor.selectExecutionFlow(c,qualification,scene.business.flows[0].flow_id);
 const reopen=async()=>{c.dispose();c=make();await c.open();await editor.openExecutionEditor(c,qualification,options);return c;};
 return {get c(){return c;},storage,session,calls,db,options,reopen,source:scene};
}
function fill(f){const flow=f.source.business.flows[0];let b=addHumanOutput(ensureNode(newBindings(flow),flow.nodes[0]),flow.nodes[0],'核对记录','string','record');b.nodes.receive.actor_id=scope.actorId;b.goals=[{id:'record_goal',required_nodes:['receive'],deliverable_indices:[0]}];const value=editor.executionFormValue(f.c,qualification);value.execution_bindings=b;value.role_bindings={核对员:scope.actorId};editor.editExecutionValue(f.c,qualification,value);return b;}
const writes=(f,suffix)=>f.calls.filter(call=>call.method==='POST'&&(!suffix||call.path.endsWith(suffix)));

test('incomplete buffers and float/integer/negative-zero schema tokens persist through actual parent storage and raw server receipt recovery',async()=>{
 const f=await fixture();fill(f);let value=editor.executionFormValue(f.c,qualification);
 value.execution_bindings.input_schema.properties.threshold={type:'number',minimum:freshNumber('1.0'),maximum:freshNumber('1'),default:freshNumber('-0.0')};
 // The public form contract is plain values plus token metadata, not atom
 // instances hidden inside structuredClone/JSON.stringify.
 value.execution_bindings=graphWireClone(editor.executionDecode(raw(value.execution_bindings)));
 const n=numericEdit(value.execution_bindings,{},['input_schema','properties','threshold','maximum'],'-','integer');value.execution_bindings=n.value;value.buffers={...n.buffers,unknown_buffer:{text:'完整保留'}};editor.editExecutionValue(f.c,qualification,value);
 f.db.loseSave=true;await assert.rejects(()=>editor.saveExecutionDraft(f.c,qualification),/响应丢失/);assert.equal(writes(f,'/drafts/save').length,1);
 const original=editor.executionEditorState(f.c,qualification).pending.raw;assert.match(original,/"minimum":1\.0/);assert.match(original,/"default":-0\.0/);assert.match(original,/"maximum":null/);
 await f.reopen();assert.equal(editor.executionFormValue(f.c,qualification).buffers.number_texts['/input_schema/properties/threshold/maximum'].text,'-');
 await editor.recoverExecutionRequest(f.c,qualification);assert.equal(writes(f,'/drafts/save').length,1);assert.equal(editor.executionEditorState(f.c,qualification).pending,null);
 const restored=editor.executionFormValue(f.c,qualification);assert.equal(restored.buffers.unknown_buffer.text,'完整保留');assert.match(raw(restored),/"minimum":1\.0/);assert.match(raw(restored),/"default":-0\.0/);
 assert.ok(editor.executionReadiness(restored).some(message=>message.includes('数值尚未填写完整')));
 await assert.rejects(()=>editor.previewExecution(f.c,qualification),/数值尚未填写完整/);assert.equal(writes(f,'/preview').length,0);
 const records=editor.executionEditorRecords(f.c,qualification);assert.equal(Object.hasOwn(records[0].request,'direct_response_raw'),false,'lost POST response must not be fabricated');assert.match(records[0].request.original_query_raw,/"minimum":1\.0/);assert.match(records[0].request.original_query_raw,/"default":-0\.0/);assert.match(records[0].response_raw,/"minimum":1\.0/);assert.equal(f.c.safeToLeave(),true);
});

test('raw server numeric types survive saved reload and preview; any edit clears preview and both acknowledgements',async()=>{
 const f=await fixture();fill(f);const value=editor.executionFormValue(f.c,qualification);value.execution_bindings.input_schema.properties.n={type:'number',minimum:freshNumber('1.0'),maximum:freshNumber('1'),default:freshNumber('-0.0')};value.execution_bindings=graphWireClone(editor.executionDecode(raw(value.execution_bindings)));editor.editExecutionValue(f.c,qualification,value);
 await editor.saveExecutionDraft(f.c,qualification);const savedRecord=editor.executionEditorRecords(f.c,qualification)[0];assert.match(savedRecord.request.direct_response_raw,/"minimum":1\.0/);assert.match(savedRecord.request.direct_response_raw,/"maximum":1[,}]/);assert.match(savedRecord.request.direct_response_raw,/"default":-0\.0/);assert.match(savedRecord.request.original_query_raw,/"minimum":1\.0/);await f.reopen();assert.match(raw(editor.executionFormValue(f.c,qualification)),/"minimum":1\.0/);assert.match(raw(editor.executionFormValue(f.c,qualification)),/"maximum":1[,}]/);assert.match(raw(editor.executionFormValue(f.c,qualification)),/"default":-0\.0/);
 await editor.previewExecution(f.c,qualification);assert.equal(f.db.prepared,1);const p=writes(f,'/preview')[0];assert.match(p.raw,/"minimum":1\.0/);assert.match(p.raw,/"default":-0\.0/);
 editor.setExecutionConsent(f.c,qualification,'consent',true);editor.setExecutionConsent(f.c,qualification,'share_source',true);
 const next=editor.executionFormValue(f.c,qualification);next.buffers.unfinished_label='后续输入';editor.editExecutionValue(f.c,qualification,next);const s=editor.executionEditorState(f.c,qualification);assert.equal(s.preview,null);assert.equal(s.consent,false);assert.equal(s.share_source,false);
 await assert.rejects(()=>editor.adoptExecution(f.c,qualification),/明确采用及共享/);assert.equal(writes(f,'/adopt').length,0);
});

test('saved adoption with lossy configuration sync retains the original receipt, never re-POSTs, and leaves other flows unadopted',async()=>{
 const f=await fixture();f.db.configuration.rate=freshNumber('1.0');fill(f);await editor.saveExecutionDraft(f.c,qualification);await editor.previewExecution(f.c,qualification);editor.setExecutionConsent(f.c,qualification,'consent',true);editor.setExecutionConsent(f.c,qualification,'share_source',true);
 const result=await editor.adoptExecution(f.c,qualification);assert.equal(result.adopted,true);assert.equal(result.synchronised,false);assert.equal(writes(f,'/adopt').length,1);
 const pending=editor.executionEditorState(f.c,qualification).pending;assert.ok(pending.receipt_raw);assert.ok(pending.original_query_raw);assert.match(pending.configuration_raw,/"rate":1\.0/);assert.match(pending.raw,/"consent":true/);assert.equal(f.db.callbacks[0].handoff.requires_lossless_sync,true);assert.equal(f.db.callbacks[0].saved.configuration.rate,1);assert.doesNotThrow(()=>JSON.stringify(f.db.callbacks[0].saved));
 await f.reopen();const samePending=editor.executionEditorState(f.c,qualification).pending;assert.equal(samePending.request_id,pending.request_id);await editor.recoverExecutionRequest(f.c,qualification,true);assert.equal(writes(f,'/adopt').length,1);
 f.db.losslessAccepted=true;const settled=await editor.recoverExecutionRequest(f.c,qualification);assert.equal(settled.synchronised,true);assert.equal(editor.executionEditorState(f.c,qualification).pending,null);assert.equal(writes(f,'/adopt').length,1);
 assert.equal(editor.executionEditorView(f.c,qualification).context.adopted_flows.length,1);assert.equal(editor.executionEditorView(f.c,qualification).context.adopted_flows[0].flow_id,'first');assert.ok(editor.executionEditorView(f.c,qualification).context.source_value.business.flows.some(flow=>flow.flow_id==='second'));
 await editor.selectExecutionFlow(f.c,qualification,'second');assert.equal(editor.executionEditorState(f.c,qualification).selected,'second');assert.deepEqual(editor.executionFormValue(f.c,qualification).execution_bindings.nodes,{receive:null});
});

test('unknown nonce, bad response SHA, local write failure and changed identity do not create replacement requests or erase buffers',async()=>{
 const f=await fixture();fill(f);f.db.unknownSave=true;await assert.rejects(()=>editor.saveExecutionDraft(f.c,qualification));const p=copy(editor.executionEditorState(f.c,qualification).pending);
 await editor.recoverExecutionRequest(f.c,qualification);assert.equal(writes(f,'/drafts/save').length,1);assert.equal(editor.executionEditorState(f.c,qualification).pending.request_id,p.request_id);assert.equal(editor.executionEditorState(f.c,qualification).pending.raw,p.raw);
 await assert.rejects(()=>editor.saveExecutionDraft(f.c,qualification),/原执行保存/);f.db.badSHA=true;await assert.rejects(()=>editor.recoverExecutionRequest(f.c,qualification));assert.equal(editor.executionEditorState(f.c,qualification).pending.raw,p.raw);
 f.session.setItem('ga-setup-csrf-user',id(99));const count=f.calls.length;await assert.rejects(()=>editor.recoverExecutionRequest(f.c,qualification),/会话已变化/);assert.equal(f.calls.length,count);
 const other=await fixture();fill(other);const set=other.storage.setItem.bind(other.storage);other.storage.setItem=()=>{throw Error('synthetic disk full');};const v=editor.executionFormValue(other.c,qualification);v.buffers.keep='新输入不丢';editor.editExecutionValue(other.c,qualification,v);assert.equal(other.c.safeToLeave(),false);await assert.rejects(()=>editor.saveExecutionDraft(other.c,qualification));assert.equal(writes(other).length,0);assert.equal(editor.executionFormValue(other.c,qualification).buffers.keep,'新输入不丢');other.storage.setItem=set;assert.equal(other.c.safeToLeave(),true);
});

test('only exact proven CAS rejection can be archived; runtime-node consent is cleared by original-node capability review markers',async()=>{
 const f=await fixture();fill(f);const v=editor.executionFormValue(f.c,qualification),runtime=await editor.executionRuntimeNodeId(f.c,'first','receive');v.external_node_ids=[runtime];v.buffers.clear_external_node_ids=['receive'];v.buffers.node_reviews={receive:{required:true,reason:'更换后的合同需要核对'}};editor.editExecutionValue(f.c,qualification,v);
 assert.deepEqual(editor.executionFormValue(f.c,qualification).external_node_ids,[]);assert.deepEqual(editor.executionFormValue(f.c,qualification).buffers.clear_external_node_ids,[]);editor.acknowledgeExecutionNode(f.c,qualification,'receive');assert.equal(editor.executionFormValue(f.c,qualification).buffers.node_reviews.receive.required,false);
 f.db.conflict=true;await assert.rejects(()=>editor.saveExecutionDraft(f.c,qualification),/CAS冲突/);const original=copy(editor.executionEditorState(f.c,qualification).pending);editor.archiveExecutionRejected(f.c,qualification);const s=editor.executionEditorState(f.c,qualification);assert.equal(s.pending,null);assert.equal(editor.executionDecode(s.archives.at(-1)).raw,original.raw);assert.equal(editor.executionFormValue(f.c,qualification).buffers.node_reviews.receive.acknowledged,true);
 const unknown=await fixture();unknown.db.unknownSave=true;await assert.rejects(()=>editor.saveExecutionDraft(unknown.c,qualification));assert.throws(()=>editor.archiveExecutionRejected(unknown.c,qualification),/未证明/);assert.ok(editor.executionEditorState(unknown.c,qualification).pending);
});

test('preview consumes the actual converter source_mapping.nodes and refuses an invented mapping alias',async()=>{
 const f=await fixture();fill(f);await editor.saveExecutionDraft(f.c,qualification);f.db.oldMappingAlias=true;
 await assert.rejects(()=>editor.previewExecution(f.c,qualification),/原步骤的转换对应/);assert.equal(editor.executionEditorState(f.c,qualification).preview,null);assert.equal(writes(f,'/adopt').length,0);
 f.db.oldMappingAlias=false;const actual=await editor.previewExecution(f.c,qualification);assert.equal(actual.binding.translation.schema,'business-flow-execution-graph-v1');assert.equal(actual.binding.translation.source_mapping.nodes[0].source_node_id,'receive');assert.equal(actual.binding.translation.source_mapping.nodes[0].graph_node_id,await editor.executionRuntimeNodeId(f.c,'first','receive'));
});

test('explicit role choice assigns every same-role source node while conflicting per-node choices stay intact until confirmed',async()=>{
 const scene=copy(source);scene.business.flows[0].nodes.push({...copy(scene.business.flows[0].nodes[0]),node_id:'finish',name:'第二个同岗位步骤'});
 const f=await fixture(scene);assert.deepEqual(editor.executionFormValue(f.c,qualification).execution_bindings.nodes,{receive:null,finish:null});
 editor.chooseExecutionRole(f.c,qualification,'核对员',scope.actorId);let value=editor.executionFormValue(f.c,qualification);assert.equal(value.role_bindings.核对员,scope.actorId);assert.equal(value.execution_bindings.nodes.receive.actor_id,scope.actorId);assert.equal(value.execution_bindings.nodes.finish.actor_id,scope.actorId);assert.deepEqual(value.execution_bindings.nodes.finish.output_ports,{'核对记录':''});
 value.execution_bindings.nodes.finish.actor_id=id(20);value.execution_bindings.nodes.finish.retained_input={note:'不能丢失的步骤填写'};editor.editExecutionValue(f.c,qualification,value);
 assert.deepEqual(editor.chooseExecutionRole(f.c,qualification,'核对员',scope.actorId),{conflict:true});value=editor.executionFormValue(f.c,qualification);assert.equal(value.execution_bindings.nodes.finish.actor_id,id(20));assert.equal(value.buffers.role_change_requests.核对员.actor_id,scope.actorId);assert.ok(editor.executionReadiness(value).some(message=>message.includes('岗位负责人变更')));
 value.buffers.later_input='看到冲突后继续填写的说明';editor.editExecutionValue(f.c,qualification,value);editor.confirmExecutionRole(f.c,qualification,'核对员');value=editor.executionFormValue(f.c,qualification);assert.equal(value.execution_bindings.nodes.finish.actor_id,scope.actorId);assert.equal(value.execution_bindings.nodes.finish.retained_input.note,'不能丢失的步骤填写');assert.equal(value.buffers.later_input,'看到冲突后继续填写的说明');assert.equal(value.buffers.role_change_requests.核对员,undefined);
 editor.chooseExecutionRole(f.c,qualification,'核对员',id(20));value=editor.executionFormValue(f.c,qualification);value.execution_bindings.nodes.finish.actor_id='';editor.editExecutionValue(f.c,qualification,value);assert.throws(()=>editor.confirmExecutionRole(f.c,qualification,'核对员'),/对照期间又有修改/);assert.equal(editor.executionFormValue(f.c,qualification).execution_bindings.nodes.finish.actor_id,'');editor.cancelExecutionRole(f.c,qualification,'核对员');assert.equal(editor.executionFormValue(f.c,qualification).buffers.role_change_requests.核对员,undefined);assert.equal(writes(f).length,0);
});

test('constructor/prototype identifiers and literal special role keys remain own wire values, with encoded local slots and no prototype mutation',async()=>{
 const scene=copy(source),flow=scene.business.flows[0];flow.flow_id='constructor';scene.business.flows[1].flow_id='prototype';
 flow.nodes=[{...copy(flow.nodes[0]),node_id:'constructor',role:'__proto__'},{...copy(flow.nodes[0]),node_id:'prototype',role:'constructor'},{...copy(flow.nodes[0]),node_id:'finish',role:'prototype'}];flow.start_node='constructor';flow.end_nodes=['finish'];flow.edges=[{kind:'sequence',from_node:'constructor',to_node:'prototype'},{kind:'sequence',from_node:'prototype',to_node:'finish'}];flow.deliverables[0].produced_by=['finish'];
 const rootConstructor=Object.prototype.constructor,f=await fixture(scene);
 assert.equal(editor.executionEditorState(f.c,qualification).selected,'constructor');assert.equal(Object.hasOwn(editor.executionEditorState(f.c,qualification).forms,'flow:constructor'),true);assert.equal(Object.hasOwn(editor.executionEditorState(f.c,qualification).forms,'constructor'),false);
 for(const role of ['__proto__','constructor','prototype'])editor.chooseExecutionRole(f.c,qualification,role,scope.actorId);
 let value=editor.executionFormValue(f.c,qualification);for(const key of ['constructor','prototype']){assert.equal(Object.hasOwn(value.execution_bindings.nodes,key),true);assert.equal(value.execution_bindings.nodes[key].actor_id,scope.actorId);}for(const role of ['__proto__','constructor','prototype']){assert.equal(Object.hasOwn(value.role_bindings,role),true);assert.equal(value.role_bindings[role],scope.actorId);}
 Object.defineProperty(value.buffers,'constructor',{value:{prototype:{['__proto__']:'原始未知缓冲完整保留'}},enumerable:true,writable:true,configurable:true});editor.editExecutionValue(f.c,qualification,value);assert.equal(f.c.safeToLeave(),true);
 const local=editor.executionFlowDraft(f.c,qualification,'constructor');assert.equal(typeof local.value_raw,'string');assert.equal(Object.hasOwn(local,'value'),false);assert.match(local.value_raw,/"__proto__"/);assert.equal(Object.prototype.constructor,rootConstructor);assert.equal(Object.hasOwn(Object.prototype,'actor_id'),false);
 await editor.saveExecutionDraft(f.c,qualification);const original=writes(f,'/drafts/save')[0].raw;assert.match(original,/"flow_id":"constructor"/);assert.match(original,/"constructor":\{/);await f.reopen();value=editor.executionFormValue(f.c,qualification);assert.equal(value.buffers.constructor.prototype.__proto__,'原始未知缓冲完整保留');assert.equal(Object.getPrototypeOf(value.role_bindings),Object.prototype);assert.equal(Object.prototype.constructor,rootConstructor);assert.equal(Object.hasOwn(Object.prototype,'actor_id'),false);
 await editor.selectExecutionFlow(f.c,qualification,'prototype');assert.equal(Object.hasOwn(editor.executionEditorState(f.c,qualification).forms,'flow:prototype'),true);assert.equal(editor.executionFlowDraft(f.c,qualification,'constructor').version,1);
});

test('old qualification pending inventory remains actionable and original GET recovery does not require fresh source loading or replacement POST',async()=>{
 const f=await fixture();fill(f);f.db.unknownSave=true;await assert.rejects(()=>editor.saveExecutionDraft(f.c,qualification));const original=copy(editor.executionEditorState(f.c,qualification).pending),newer={...qualification,id:id(30),qualification_hash:'d'.repeat(64),draft_version:2};
 const transport=async(path,options)=>{const answer=await f.options.typedHTTP(path,options);if(path.endsWith('/qualifications/'+newer.id+'/context')){const ctx=copy(editor.executionDecode(answer));ctx.qualification_id=newer.id;ctx.qualification_hash=newer.qualification_hash;return raw(ctx);}return answer;};
 await editor.openExecutionEditor(f.c,newer,{...f.options,typedHTTP:transport});const entries=editor.executionEditorEntries(f.c);assert.equal(entries.length,2);const old=editor.executionPendingSelections(f.c).find(entry=>entry.qualification.id===qualification.id);assert.ok(old);assert.equal(old.has_draft,true);assert.equal(old.pending.request_id,original.request_id);
 const before=f.calls.length;await editor.recoverExecutionRequest(f.c,old.qualification,false,f.options);const recoveryCalls=f.calls.slice(before);assert.ok(recoveryCalls.some(call=>call.path.includes('/draft-requests/'+original.request_id)));assert.equal(recoveryCalls.some(call=>call.path.endsWith('/context')),false,'old nonce lookup is not blocked by a fresh-source gate');assert.equal(recoveryCalls.some(call=>call.method==='POST'),false);assert.equal(editor.executionEditorState(f.c,qualification).pending.raw,original.raw);assert.equal(editor.executionEditorState(f.c,newer).pending,null);assert.equal(f.c.safeToLeave(),true);
});
