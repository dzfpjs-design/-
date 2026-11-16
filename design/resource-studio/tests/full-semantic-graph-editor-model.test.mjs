/** Authoring contracts only. These synthetic rows never prove server authority. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
 createGraphEditorDraft,createNewDesignRevision,buildGraphPreviewCommand,collectGraphEditorIssues,
 clone,setNode,setRoleMember,selectAbility,setAbilityInput,setNodeAction,addOutputField,addControl,
 connectNodes,addGoal,addPredicate,addReference,setLoop,removeControl,removeReference,
 restoreAbilityDraft,updateGraphDraft,schemaKind,referenceSchemaKind,compatible,parseNumberText,applyNumericEdit,humanSchemaSupport,numericDraftDisplay,restoreGraphEditorDraftNumbers,
 pendingGraphEditorItems,completeGraphEditorBuffer,archiveGraphEditorBuffer,restoreGraphEditorBuffer,graphEditorBufferHistory,focusGraphEditorBuffer,
} from '../src/full-semantic-graph-editor-model.mjs';
import {graphWireParseCanonical,graphWireRequestJSON,graphWireNumericTokens,graphWireTypedCopy} from '../src/full-semantic-graph-wire.mjs';
import {isTypedNumber,numericToken} from '../src/typed-numbers-v132.mjs';

const uid=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0'),H='a'.repeat(64),ACTOR=uid(1);
function context(agent='人工确认'){
 const step={name:'逐项核对原资料',agent,role:agent==='人工确认'?'核对负责人':'',purpose:'保存实际处理结果',output:'可追溯的候选结果',next_index:null,else_index:null,branch_condition:''};
 const business={name:'通用资料处理',trigger:'人工发起',output:'完整候选结果',steps:[step],participants:[],interactions:[],conditions:[],coordination:[]};
 const owners=[
  {owner_path:'/domain',kind:'domain',source_fields:['value']},
  {owner_path:'/businesses/0',kind:'business',source_fields:['trigger','output']},
  {owner_path:'/businesses/0/steps/0',kind:'step',source_fields:['name','agent','role','purpose','output','next_index','branch_condition','else_index']},
  {owner_path:'/global_claims/0',kind:'global_claim',source_fields:['kind','statement']},
  {owner_path:'/questions/0',kind:'question',source_fields:['value']},
 ].map(o=>({...o,value_hash:H,ref_hashes:[]}));
 return {schema:'report-full-semantic-graph-editor-context-v1',handoff_id:uid(10),active:true,
  design:{handoff_id:uid(10),design_hash:H,result:{domain:'合成协议测试',businesses:[business],objects:[],relationships:[],global_claims:[{kind:'要求',statement:'必须保留真实结果'}],questions:['实际领域质量尚待验证']},business_bindings:[{candidate_index:0,step_ids:[uid(20)]}]},
  manifest:{owners:owners.map(o=>({path:o.owner_path,value_hash:o.value_hash,ref_hashes:[]})),refs:[]},
  owners,configuration:{revision:7,hash:H},proofs:[{id:uid(30),proof_hash:H,current_source_verified:true,reason:''}],
  schema_origins:{objects:[],relationships:[]},adoptions:[],event_wait:{available:false,reason:'当前没有可登记的真实事件接收来源'}};
}
function mapOwners(value,ctx){return updateGraphDraft(value,d=>{for(const row of d.owner_bindings){const spec=ctx.owners.find(s=>s.owner_path===row.owner_path);row.reason='显式对应合成工作区安排，仍不认证为原文事实';if(['domain','question'].includes(spec.kind)){row.resolution='informational';continue;}row.resolution='typed';for(const clause of row.clauses){if(spec.kind==='business')clause.targets=[{kind:clause.source_field==='trigger'?'node':'goal',id:clause.source_field==='trigger'?'step_1':'goal_1'}];else if(spec.kind==='step')clause.targets=clause.source_field==='next_index'?[{kind:'edge',id:'edge_1'}]:['branch_condition','else_index'].includes(clause.source_field)?[]:[{kind:'node',id:'step_1'}];else clause.targets=[{kind:'goal',id:'goal_1'},...Object.keys(d.graph.references).map(id=>({kind:'value_ref',id}))];}}});}
function ready(ctx=context(),abilities=[]){let value=createGraphEditorDraft(ctx,ctx.design.result.businesses[0].name);value.proof_id=uid(30);if(value.graph.nodes[0].kind==='human'){value=setRoleMember(value,'核对负责人',ACTOR);value=addOutputField(value,'step_1','核对是否完成','boolean');}else{value=setRoleMember(value,'能力实际执行负责人',ACTOR);value=setNode(value,'step_1',{actor_id:ACTOR});value=selectAbility(value,'step_1',abilities[0]);for(const [field,source] of Object.entries(abilities[0].fixed_bindings||{}))value=setAbilityInput(value,'step_1',field,{mode:'server',source});value=setNodeAction(value,'step_1','observe');}value=addControl(value,'end');value=connectNodes(value,'step_1','end_1');value.graph.entry='step_1';value=addGoal(value,'必须取得本次实际结果');value.graph.goals[0].required_nodes=['step_1'];return mapOwners(value,ctx);}
function audioOption(extra={}){return {id:uid(40),name:'真实本地原音频能力（合成元数据）',agents:['多模感知智能体'],content_hash:H,release_version:'1.0.1',max_attempts:2,budget:{max_attempts:2},adapter:'local_audio',external:false,
 input_schema:{type:'object',properties:{audio_ref:{title:'原音频与固定设置',type:'object',properties:{input_audio_id:{type:'string'},snapshot_hash:{type:'string'}},required:['input_audio_id','snapshot_hash'],additionalProperties:false},task_id:{title:'本任务',type:'string'}},required:['audio_ref','task_id'],additionalProperties:false},
 output_schema:{type:'object',properties:{attributes:{type:'object'},audio_transcription:{type:'object'},audio_run_id:{type:'string'},anomalies:{type:'array',items:{type:'object'}}},required:['attributes','audio_transcription','audio_run_id','anomalies'],additionalProperties:false},
 fixed_bindings:{audio_ref:'task_audio_ref',task_id:'task_identifier'},...extra};}
function aggregateOption(operation={enum:['mean','sum','count']}){return audioOption({name:'汇总业务数量（实际builtin合同形状）',agents:['数据管理智能体'],adapter:'builtin',fixed_bindings:{records:'object_records'},input_schema:{type:'object',required:['records','field','operation'],additionalProperties:false,properties:{records:{type:'array',items:{type:'object'}},field:{type:'string'},operation:clone(operation)}},output_schema:{type:'object',required:['value','sample_count','provenance'],properties:{value:{type:['number','null']},sample_count:{type:'integer'},provenance:{type:'array'}}}});}
function aggregateDraft(ctx,cap,operation){let value=ready(ctx,[cap]);value=setNodeAction(value,'step_1','data');value=setAbilityInput(value,'step_1','field',{mode:'literal',value:'数量'});value=setAbilityInput(value,'step_1','operation',{mode:'literal',value:operation});return value;}
function attachAdoption(ctx,value,extra={}){const binding={graph:clone(value.graph),owner_bindings:clone(value.owner_bindings),role_bindings:Object.fromEntries(Object.entries(value.role_bindings).map(([role,user_id])=>[role,{user_id,username:'合成操作员',membership_role:'operator'}])),proof_id:value.proof_id,binding_hash:H,source_design:clone(ctx.design),source_business:clone(ctx.design.result.businesses[0])};ctx.adoptions=[{business_name:value.business_name,origin:{adoption_id:uid(50)},receipt:{receipt_hash:H},binding,root_id:uid(50),parent_id:null,is_current:true,execution_scope:{node_actions:clone(value.node_actions),external_node_ids:clone(value.external_node_ids)},...extra}];return ctx;}

test('new drafts retain every original source step but never infer entry, edges or successful guards',()=>{
 const ctx=context();ctx.design.result.businesses[0].steps.push({...ctx.design.result.businesses[0].steps[0],name:'第二项原职责'});ctx.design.business_bindings[0].step_ids.push(uid(21));
 const before=clone(ctx),draft=createGraphEditorDraft(ctx,'通用资料处理');
 assert.deepEqual(draft.graph.nodes.map(n=>n.source_step_id),[uid(20),uid(21)]);
 assert.equal(draft.graph.entry,'');assert.deepEqual(draft.graph.edges,[]);assert.deepEqual(draft.graph.predicates,{});
 assert.ok(draft.owner_bindings.every(row=>row.resolution==='unresolved'));
 assert.deepEqual(ctx,before);assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[]),e=>e.issues.some(i=>i.path==='graph.entry'));
});
test('complete explicit human design produces only the actual closed root-preview command',()=>{
 const ctx=context(),draft=ready(ctx),before=clone(draft);draft.ui.secretDraftNote='本机输入不得发送到预览命令';
 assert.deepEqual(collectGraphEditorIssues(draft,ctx,[]),[]);
 const body=buildGraphPreviewCommand(draft,ctx,[]);
 assert.deepEqual(Object.keys(body).sort(),['schema','configuration_revision','configuration_hash','proof_id','business_name','graph','owner_bindings','role_bindings','external_node_ids','node_actions'].sort());
 assert.equal(body.schema,'report-full-semantic-graph-preview-command-v3');assert.equal(body.configuration_revision,7);
 assert.equal(JSON.stringify(body).includes('本机输入不得发送'),false);assert.equal('parent' in body,false);
 assert.deepEqual({...draft,ui:before.ui},before);body.graph.nodes[0].output_schema.properties.answer_1.title='外部副本';assert.equal(draft.graph.nodes[0].output_schema.properties.answer_1.title,'核对是否完成');
});
test('accepted graphs clone all frozen structures and execution scope, with exact current parent',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption({external:true,adapter:'http'}),original=ready(ctx,[cap]);original.external_node_ids=['step_1'];attachAdoption(ctx,original);
 const saved=clone(ctx.adoptions[0]),draft=createGraphEditorDraft(ctx,'通用资料处理');
 assert.deepEqual(draft.graph,saved.binding.graph);assert.deepEqual(draft.owner_bindings,saved.binding.owner_bindings);
 assert.deepEqual(draft.external_node_ids,['step_1']);assert.deepEqual(draft.node_actions,{step_1:'observe'});
 const body=buildGraphPreviewCommand(draft,ctx,[cap]);assert.equal(body.schema,'report-full-semantic-graph-revision-preview-command-v1');assert.deepEqual(body.parent,{id:uid(50),receipt_hash:H});assert.equal(Object.keys(body.parent).length,2);
 assert.deepEqual(ctx.adoptions[0],saved);
});
test('changed head cannot be silently rebased or overwrite the saved draft',()=>{
 const ctx=context();attachAdoption(ctx,ready(ctx));const draft=createGraphEditorDraft(ctx,'通用资料处理'),before=clone(draft);ctx.adoptions[0].origin.adoption_id=uid(51);
 assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[]),e=>e.issues.some(i=>i.path==='parent'&&i.message.includes('不能自动覆盖')));
 assert.throws(()=>createNewDesignRevision(draft,ctx),/原采用版本已变化/);assert.deepEqual(draft,before);
});
test('replacement source preserves old IDs for comparison and needs an explicit new design revision',()=>{
 const ctx=context();attachAdoption(ctx,ready(ctx));ctx.design.handoff_id=uid(11);ctx.handoff_id=uid(11);ctx.design.business_bindings[0].step_ids=[uid(22)];
 const retained=createGraphEditorDraft(ctx,'通用资料处理');assert.equal(retained.graph.nodes[0].source_step_id,uid(20));
 assert.ok(collectGraphEditorIssues(retained,ctx,[]).some(i=>i.message.includes('恰好保留')));
 const next=createNewDesignRevision(retained,ctx);assert.equal(next.graph.nodes[0].source_step_id,uid(22));assert.equal(next.graph.entry,'');assert.deepEqual(next.graph.edges,[]);assert.deepEqual(next.parent,retained.parent);
 assert.deepEqual(next.ui.previous_working_draft.graph,retained.graph);assert.equal(retained.graph.nodes[0].source_step_id,uid(20));
});
test('real structured audio inputs are frozen objects, not paths or stringified JSON',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption(),draft=ready(ctx,[cap]);
 assert.deepEqual(collectGraphEditorIssues(draft,ctx,[cap]),[]);
 const pin=draft.graph.nodes[0].execution,audio=draft.graph.references[pin.input_bindings.audio_ref];assert.equal(audio.kind,'object');assert.equal(audio.field,'task_audio_ref');assert.equal(audio.source,'input');
 assert.deepEqual(pin.arguments,{});assert.equal(draft.graph.input_schema.properties.task_audio_ref.type,'object');assert.equal(schemaKind(cap.input_schema.properties.audio_ref).kind,'object');assert.equal(compatible(audio,cap.input_schema.properties.audio_ref,cap.input_schema),true);
 const forged=setAbilityInput(draft,'step_1','audio_ref',{mode:'literal',value:'{"input_audio_id":"invented"}'});
 assert.ok(collectGraphEditorIssues(forged,ctx,[cap]).some(i=>i.message.includes('服务器固定输入')));
 assert.ok(collectGraphEditorIssues(forged,ctx,[cap]).some(i=>i.message.includes('逐项填写')));
 assert.throws(()=>setAbilityInput(draft,'step_1','audio_ref',{mode:'server',source:'arbitrary.secret.path'}),/尚未接入/);
});
test('ability version changes, unknown members and stale formal proof are specific gaps',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption(),draft=ready(ctx,[cap]),changed={...cap,content_hash:'b'.repeat(64)};
 assert.ok(collectGraphEditorIssues(draft,ctx,[changed]).some(i=>i.message.includes('版本或合同已变化')));
 assert.ok(collectGraphEditorIssues(draft,ctx,[cap],[]).some(i=>i.message.includes('真实成员')));
 ctx.proofs[0].current_source_verified=false;ctx.proofs[0].reason='正式副本需重新核对';assert.ok(collectGraphEditorIssues(draft,ctx,[cap]).some(i=>i.message==='正式副本需重新核对'));
});
test('editing capability, actor, action or input never auto-grants an old external consent',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption({external:true,adapter:'http'}),draft=ready(ctx,[cap]);draft.external_node_ids=['step_1'];
 assert.deepEqual(collectGraphEditorIssues(draft,ctx,[cap]),[]);
 for(const changed of [selectAbility(draft,'step_1',cap),setNode(draft,'step_1',{actor_id:uid(2)}),setNodeAction(draft,'step_1','data'),setAbilityInput(draft,'step_1','task_id',{mode:'server',source:'task_identifier'})]){
  assert.deepEqual(changed.external_node_ids,[]);assert.ok(collectGraphEditorIssues(changed,ctx,[cap]).some(i=>i.message.includes('外发')));
 }
 assert.deepEqual(draft.external_node_ids,['step_1']);
 const reselected=selectAbility(draft,'step_1',cap);assert.deepEqual(reselected.ui.previous_executions.step_1[0],draft.graph.nodes[0].execution);
 const restored=restoreAbilityDraft(reselected,'step_1',0);assert.deepEqual(restored.graph.nodes[0].execution,draft.graph.nodes[0].execution);assert.deepEqual(restored.external_node_ids,[]);
});
test('unknown and structured predicate refs cannot become false or a default successful path',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption(),draft=addPredicate(ready(ctx,[cap]),'是否允许继续');
 draft.graph.nodes[0].guards.precondition='condition_1';draft.owner_bindings.find(o=>o.owner_path==='/global_claims/0').clauses[0].targets.push({kind:'predicate',id:'condition_1'});
 assert.deepEqual(draft.graph.predicates.condition_1,{op:'ref',key:''});
 const valid=clone(draft);valid.graph.predicates.condition_1={op:'value',kind:'boolean',value:false};assert.deepEqual(collectGraphEditorIssues(valid,ctx,[cap]),[]);assert.equal(buildGraphPreviewCommand(valid,ctx,[cap]).graph.predicates.condition_1.value,false);
 const before=clone(draft);assert.ok(collectGraphEditorIssues(draft,ctx,[cap]).some(i=>i.path==='graph.predicates.condition_1'));assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[cap]),e=>e.issues.some(i=>i.path==='graph.predicates.condition_1'));assert.deepEqual(draft,before);
 const objectKey=draft.graph.nodes[0].execution.input_bindings.audio_ref;draft.graph.predicates.condition_1={op:'eq',left:{op:'ref',key:objectKey},right:{op:'value',kind:'string',value:'{}'}};
 assert.equal(draft.graph.references[objectKey].kind,'object');assert.ok(collectGraphEditorIssues(draft,ctx,[cap]).some(i=>i.path==='graph.predicates.condition_1.left'));assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[cap]),e=>e.issues.some(i=>i.path==='graph.predicates.condition_1.left'));assert.equal(draft.graph.predicates.condition_1.left.key,objectKey);
});
test('typed grouped predicates retain false and reject invalid dates, booleans as numbers and empty groups',()=>{
 const ctx=context();for(const ast of [
  {op:'all',args:[]},
  {op:'eq',left:{op:'value',kind:'integer',value:true},right:{op:'value',kind:'integer',value:1}},
  {op:'lt',left:{op:'value',kind:'date',value:'2026-02-30'},right:{op:'value',kind:'date',value:'2026-03-01'}},
  {op:'gt',left:{op:'value',kind:'string',value:'a'},right:{op:'value',kind:'string',value:'b'}},
  {op:'lt',left:{op:'value',kind:'date',value:'0000-01-01'},right:{op:'value',kind:'date',value:'0001-01-01'}},
 ]){const d=addPredicate(ready(ctx),'判断');d.graph.predicates.condition_1=ast;assert.ok(collectGraphEditorIssues(d,ctx,[]).some(i=>i.path.startsWith('graph.predicates')));}
 const valid=addPredicate(ready(ctx),'全部明确');valid.graph.predicates.condition_1={op:'all',args:[{op:'value',kind:'boolean',value:false},{op:'not',arg:{op:'value',kind:'boolean',value:true}}]};valid.graph.nodes[0].guards.precondition='condition_1';valid.owner_bindings.find(o=>o.owner_path==='/global_claims/0').clauses[0].targets.push({kind:'predicate',id:'condition_1'});
 assert.deepEqual(collectGraphEditorIssues(valid,ctx,[]),[]);assert.equal(buildGraphPreviewCommand(valid,ctx,[]).graph.predicates.condition_1.args[0].value,false);
});
test('source owners cannot be deleted, global requirements cannot become informational, and stale targets persist as gaps',()=>{
 const ctx=context(),draft=ready(ctx),removed=clone(draft);removed.owner_bindings.pop();assert.ok(collectGraphEditorIssues(removed,ctx,[]).some(i=>i.message.includes('条目数量')));
 const ignored=clone(draft);ignored.owner_bindings.find(o=>o.owner_path==='/global_claims/0').resolution='informational';assert.ok(collectGraphEditorIssues(ignored,ctx,[]).some(i=>i.message.includes('一键忽略')));
 const stale=clone(draft);stale.owner_bindings[2].clauses[0].targets=[{kind:'node',id:'not_a_real_node'}];assert.ok(collectGraphEditorIssues(stale,ctx,[]).some(i=>i.message.includes('对应项已不存在')));
 const shifted=clone(draft);shifted.owner_bindings[1].scope_businesses=[];assert.ok(collectGraphEditorIssues(shifted,ctx,[]).some(i=>i.message.includes('影响的业务')));
});
test('unconnected controls, fake joins and missing goals cannot produce preview commands',()=>{
 const ctx=context(),draft=ready(ctx),fork=addControl(draft,'fork');
 assert.deepEqual(fork.graph.nodes.find(n=>n.kind==='fork').branch_ids,['branch_1','branch_2']);assert.equal(fork.graph.edges.length,1);
 assert.throws(()=>buildGraphPreviewCommand(fork,ctx,[]),e=>e.issues.some(i=>i.message.includes('出口')));
 const bypass=clone(draft);bypass.graph.goals[0].required_nodes=['end_1'];assert.ok(collectGraphEditorIssues(bypass,ctx,[]).some(i=>i.message.includes('真实人工')));
 assert.throws(()=>removeControl(draft,'step_1'),/原业务步骤必须保留/);
});
test('all-branch joins are validated by fork scopes rather than edge counts',()=>{
 const ctx=context(),draft=ready(ctx),forked=addControl(draft,'fork'),fork=forked.graph.nodes.find(n=>n.kind==='fork'),join=forked.graph.nodes.find(n=>n.kind==='join');
 forked.graph.entry=fork.id;forked.graph.edges=[];
 // Both purported arrivals carry the same branch identity; a count of two is
 // insufficient, even though the destination and expected length are present.
 let v=connectNodes(forked,fork.id,'step_1','branch','branch_1');
 v=connectNodes(v,'step_1',join.id);v=connectNodes(v,join.id,'end_1');
 assert.ok(collectGraphEditorIssues(v,ctx,[]).some(i=>i.message.includes('每一条不同分支')||i.message.includes('每个出口')));
});
test('unbounded loops, nonreturn loop edges, invalid caps and waiting sources stay blocked',()=>{
 const ctx=context(),draft=ready(ctx);
 assert.throws(()=>setLoop(draft,'edge_1',0),/1至100/);assert.throws(()=>setLoop(draft,'edge_1',101),/1至100/);
 const notBack=setLoop(draft,'edge_1',2);assert.ok(collectGraphEditorIssues(notBack,ctx,[]).some(i=>i.message.includes('返回本次路径')));
 const cycle=clone(draft);cycle.graph.edges[0].to='step_1';assert.ok(collectGraphEditorIssues(cycle,ctx,[]).some(i=>i.message.includes('没有明确次数边界')));
 const wait=addControl(draft,'wait');assert.ok(collectGraphEditorIssues(wait,ctx,[]).some(i=>i.message==='当前没有可登记的真实事件接收来源'));
 assert.throws(()=>buildGraphPreviewCommand(wait,ctx,[]));
});
test('closed wire refuses extra graph fields, NUL, nonfinite values and source-path rewrites',()=>{
 const ctx=context();for(const change of [d=>{d.graph.arbitrary_code='not executed';},d=>{d.graph.nodes[0].output_schema.properties.answer_1.title='bad\0text';},d=>{d.graph.nodes[0].output_schema.properties.answer_1.maximum=Infinity;},d=>{d.owner_bindings[0].owner_path='/global_claims/0';}]){const draft=ready(ctx);change(draft);assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[]));}
});
test('removing a declared value never rewrites its original owner or consumer silently',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption(),draft=ready(ctx,[cap]),key=draft.graph.nodes[0].execution.input_bindings.audio_ref,removed=removeReference(draft,key);
 assert.equal(removed.graph.nodes[0].execution.input_bindings.audio_ref,key);assert.ok(removed.owner_bindings.some(o=>o.clauses.some(c=>c.targets.some(t=>t.kind==='value_ref'&&t.id===key))));assert.ok(collectGraphEditorIssues(removed,ctx,[cap]).length>0);assert.ok(draft.graph.references[key]);
});

test('numeric text parsing retains decimal, negative and exponent editing without coercing incomplete values',()=>{
 for(const text of ['', '-', '1.', '-1.', '1e', '1e-', ' 1', '01', 'Infinity', 'NaN','1.2.3'])assert.equal(parseNumberText(text).complete,false,text);
 for(const [text,value] of [['1.5',1.5],['-1.5',-1.5],['-0',-0],['1e-3',0.001],['2',2]])assert.deepEqual(parseNumberText(text),{complete:true,value});
 assert.equal(parseNumberText('1.5','integer').complete,false);assert.equal(parseNumberText('9007199254740992','number').complete,false);
 assert.equal(parseNumberText('0','integer',{minimum:1,maximum:100}).complete,false);assert.equal(parseNumberText('101','integer',{minimum:1,maximum:100}).complete,false);
 assert.deepEqual(parseNumberText('12','integer',{minimum:1,maximum:100}),{complete:true,value:12});
});

test('V132 finite float tokens accept scientific integers while bare unsafe integers remain blocked',()=>{
 for(const [raw,value]of [['-0.0',-0],['1.0',1],['1e21',1e21],['9007199254740992.0',9007199254740992],['1e-21',1e-21]])assert.deepEqual(parseNumberText(raw,'number'),{complete:true,value});
 for(const raw of ['1e21','9007199254740992','9007199254740992.0','-0'])assert.equal(parseNumberText(raw,'integer').complete,false,raw);
 for(const raw of ['9007199254740992','1e309','NaN','1e','1.'])assert.equal(parseNumberText(raw,'number').complete,false,raw);
});

test('V132 actual ability drafts stay JSON-storable and preview alone creates numeric atoms after reload',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema.properties.threshold={type:'number',title:'判断阈值'};
 for(const raw of ['-0.0','1.0','1e21']){
  let draft=ready(ctx,[cap]);const key='ability:step_1:threshold',value=Number(raw);
  draft=applyNumericEdit(draft,{key,text:raw,kind:'number',label:'判断阈值'},d=>setAbilityInput(d,'step_1','threshold',{mode:'literal',value}));
  const stored=JSON.stringify(draft),cold=JSON.parse(stored),before=JSON.stringify(cold);
  assert.equal(numericDraftDisplay(cold,key,cold.graph.nodes[0].execution.arguments.threshold),raw);
  const body=buildGraphPreviewCommand(cold,ctx,[cap],{typedNumbers:true}),literal=body.graph.nodes[0].execution.arguments.threshold;
  assert.ok(isTypedNumber(literal));assert.equal(numericToken(literal),raw);assert.ok(graphWireRequestJSON(body).includes('"threshold":'+raw));
  assert.equal(JSON.stringify(cold),before);assert.equal(JSON.stringify(draft),stored);
  const restored=restoreGraphEditorDraftNumbers(cold);assert.equal(typeof restored.graph.nodes[0].execution.arguments.threshold,'number');
  assert.equal(Object.is(restored.graph.nodes[0].execution.arguments.threshold,-0),raw==='-0.0');
  assert.equal(buildGraphPreviewCommand(restored,ctx,[cap]).schema,'report-full-semantic-graph-preview-command-v3');
 }
});

test('V132 sibling edits and ability history preserve original schema tokens and numeric input text',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema=graphWireParseCanonical('{"additionalProperties":false,"properties":{"settings":{"properties":{"threshold":{"minimum":-0.0,"type":"number"},"title":{"type":"string"}},"type":"object"}},"required":["settings"],"type":"object"}');cap.fixed_bindings={};
 let draft=ready(ctx,[cap]);draft=setAbilityInput(draft,'step_1','settings',{mode:'literal',value:{threshold:1,title:'原名称'}});
 draft=updateGraphDraft(draft,d=>{d.graph.nodes[0].execution.arguments.settings=graphWireParseCanonical('{"threshold":1.0,"title":"原名称"}');});
 draft=setAbilityInput(draft,'step_1','settings',{mode:'literal',value:{...draft.graph.nodes[0].execution.arguments.settings,title:'先修改文字'}});
 assert.equal(graphWireNumericTokens(draft.graph)['/nodes/0/execution/arguments/settings/threshold'],'1.0');
 const key='ability:step_1:settings/threshold';draft=applyNumericEdit(draft,{key,text:'1.0',kind:'number',label:'嵌套阈值'},d=>d);
 draft=setAbilityInput(draft,'step_1','settings',{mode:'literal',value:{threshold:1,title:'新名称'}});
 const next={...cap,id:uid(45),release_version:'2.0.0'};draft=selectAbility(draft,'step_1',next);
 const cold=JSON.parse(JSON.stringify(draft));draft=restoreAbilityDraft(cold,'step_1',0);draft=setNodeAction(draft,'step_1','observe');
 assert.equal(draft.ui.number_texts[key].text,'1.0');assert.equal(draft.graph.nodes[0].execution.arguments.settings.title,'新名称');
 assert.equal(graphWireNumericTokens(draft.graph)['/nodes/0/execution/input_schema/properties/settings/properties/threshold/minimum'],'-0.0');
 const command=buildGraphPreviewCommand(draft,ctx,[cap],{typedNumbers:true}),encoded=graphWireRequestJSON(command);
 assert.ok(encoded.includes('"minimum":-0.0'));assert.ok(encoded.includes('"threshold":1.0'));
 assert.equal(cold.ui.previous_executions.step_1.length,1);assert.doesNotThrow(()=>JSON.stringify(draft));
});

test('V132 cloned source schemas and numeric enum kinds distinguish typed floats from integers',()=>{
 const source=graphWireParseCanonical('{"enum":[-0.0,1.0,1e+21]}'),copy=clone(source);
 assert.deepEqual(schemaKind(copy),{kind:'number',nullable:false});
 assert.equal(graphWireRequestJSON(graphWireTypedCopy(copy)),'{"enum":[-0.0,1.0,1e+21]}');
 assert.deepEqual(schemaKind({enum:[1,2]}),{kind:'integer',nullable:false});
 assert.equal(schemaKind({enum:[9007199254740992]}),null);
 assert.equal(Object.is(clone({value:-0}).value,-0),true);
});
test('incomplete numeric input is durable, blocks preview and never writes null, zero or a coerced old result',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema.properties.threshold={type:'number',title:'候选阈值'};cap.input_schema.required.push('threshold');
 let draft=ready(ctx,[cap]);draft=setAbilityInput(draft,'step_1','threshold',{mode:'literal',value:1});draft=mapOwners(draft,ctx);
 const before=clone(draft),key='ability:step_1:threshold',change=n=>d=>setAbilityInput(d,'step_1','threshold',{mode:'literal',value:n});
 const typed=(text,n)=>{draft=applyNumericEdit(draft,{key,label:'候选阈值',text,kind:'number'},change(n));};
 typed('1.',undefined);assert.equal(draft.graph.nodes[0].execution.arguments.threshold,1);assert.equal(draft.ui.number_texts[key].text,'1.');
 assert.ok(collectGraphEditorIssues(draft,ctx,[cap]).some(i=>i.path==='ui.number_texts.'+key));assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[cap]),e=>e.issues.some(i=>i.path==='ui.number_texts.'+key));
 draft=clone(draft);typed('1.5',1.5);assert.equal(draft.graph.nodes[0].execution.arguments.threshold,1.5);assert.equal(draft.ui.number_texts[key].text,'1.5');assert.deepEqual(collectGraphEditorIssues(draft,ctx,[cap]),[]);
 typed('-',undefined);assert.equal(draft.graph.nodes[0].execution.arguments.threshold,1.5);typed('-1.5',-1.5);assert.equal(draft.graph.nodes[0].execution.arguments.threshold,-1.5);
 const command=buildGraphPreviewCommand(draft,ctx,[cap]);assert.equal(command.graph.nodes[0].execution.arguments.threshold,-1.5);assert.ok(!('ui' in command));assert.equal(before.graph.nodes[0].execution.arguments.threshold,1);
 draft=setAbilityInput(draft,'step_1','threshold',{mode:'unset'});assert.ok(!draft.ui.number_texts[key]);
});
test('numeric predicate and bounded repetition buffers stay separate and incomplete text cannot alter either wire value',()=>{
 const ctx=context(),draft=ready(ctx);let value=addPredicate(draft,'数值判断');value.graph.predicates.condition_1={op:'gt',left:{op:'value',kind:'number',value:1},right:{op:'value',kind:'number',value:0}};
 const original=clone(value),predicateKey='predicate:condition_1/left';
 value=applyNumericEdit(value,{key:predicateKey,text:'1.',kind:'number',label:'判断左值'},d=>updateGraphDraft(d,x=>{x.graph.predicates.condition_1.left.value=null;}));
 assert.equal(value.graph.predicates.condition_1.left.value,1);assert.equal(value.ui.number_texts[predicateKey].text,'1.');
 value=applyNumericEdit(value,{key:'loop:edge_1',text:'-',kind:'integer',minimum:1,maximum:100,label:'重复次数'},d=>setLoop(d,'edge_1',0));
 assert.deepEqual(value.graph.loops,[]);assert.equal(value.ui.number_texts['loop:edge_1'].text,'-');assert.deepEqual(original.ui.number_texts,undefined);
 value=applyNumericEdit(value,{clearPrefix:'predicate:condition_1'},d=>updateGraphDraft(d,x=>{x.graph.predicates.condition_1={op:'value',kind:'boolean',value:false};}));
 assert.ok(!value.ui.number_texts[predicateKey]);assert.equal(value.ui.number_texts['loop:edge_1'].text,'-');
 value=applyNumericEdit(value,{clearPrefix:'loop:edge_1'},d=>setLoop(d,'edge_1',null));assert.deepEqual(value.ui.number_texts,{});
});
test('unsupported complex human contracts remain exact and cannot create an unusable human task',()=>{
 const ctx=context(),draft=ready(ctx);draft.graph.nodes[0].output_schema.properties.answer_1={type:'object',properties:{confirmed:{type:'boolean'}},required:['confirmed'],additionalProperties:false};
 const before=clone(draft);assert.equal(humanSchemaSupport(draft.graph.nodes[0].output_schema).supported,false);assert.ok(collectGraphEditorIssues(draft,ctx,[]).some(i=>i.message.includes('当前人工待办尚不支持')));assert.throws(()=>buildGraphPreviewCommand(draft,ctx,[]));assert.deepEqual(draft,before);
 const scalar=ready(ctx).graph.nodes[0].output_schema;assert.equal(humanSchemaSupport(scalar).supported,true);
});
test('removing a numeric list item preserves the remaining unfinished text at its new position',()=>{
 let value=ready(context());value.ui.number_texts={'items/0/amount':{key:'items/0/amount',text:'1.',kind:'number'},'items/1/amount':{key:'items/1/amount',text:'-',kind:'number'},'unrelated':{key:'unrelated',text:'2.',kind:'number'}};
 value=applyNumericEdit(value,{removeIndexPrefix:'items',index:0},clone);assert.equal(value.ui.number_texts['items/0/amount'].text,'-');assert.equal(value.ui.number_texts['items/0/amount'].key,'items/0/amount');assert.ok(!value.ui.number_texts['items/1/amount']);assert.equal(value.ui.number_texts.unrelated.text,'2.');
});
test('capability changes archive unfinished numeric text, restore it only with its original inputs, and ignore unrelated history',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema.properties.threshold={type:'number',title:'阈值'};
 let value=ready(ctx,[cap]);value=setAbilityInput(value,'step_1','threshold',{mode:'literal',value:1});const key='ability:step_1:threshold';
 value=applyNumericEdit(value,{key,text:'1.',kind:'number',label:'阈值'},d=>d);assert.ok(collectGraphEditorIssues(value,ctx,[cap]).some(i=>i.path==='ui.number_texts.'+key));
 const next={...clone(cap),release_version:'1.0.2'};value=selectAbility(value,'step_1',next);assert.ok(!value.ui.number_texts[key]);assert.equal(value.ui.previous_number_texts.step_1[0][key].text,'1.');
 assert.ok(!collectGraphEditorIssues(value,ctx,[next]).some(i=>i.path.startsWith('ui.number_texts.')));
 value=restoreAbilityDraft(value,'step_1',0);assert.equal(value.ui.number_texts[key].text,'1.');assert.equal(value.graph.nodes[0].execution.arguments.threshold,1);assert.ok(collectGraphEditorIssues(value,ctx,[cap]).some(i=>i.path==='ui.number_texts.'+key));
 value=applyNumericEdit(value,{key,text:'1.5',kind:'number',label:'阈值'},d=>setAbilityInput(d,'step_1','threshold',{mode:'literal',value:1.5}));value.graph.nodes[0].execution.arguments.threshold=2;
 const mismatchedBytes=JSON.stringify(value),mismatchedIssues=collectGraphEditorIssues(value,ctx,[cap]);
 // Durable numeric tokens now detect this corruption before the field-level
 // comparison. Both paths must block preview and preserve the two originals.
 assert.ok(mismatchedIssues.some(i=>(i.path==='draft'||i.path==='ui.number_texts.'+key)&&/原编辑文本与当前保存值不一致|原数值文本与完整设计无法核对/.test(i.message)));
 assert.throws(()=>buildGraphPreviewCommand(value,ctx,[cap]));assert.equal(JSON.stringify(value),mismatchedBytes);
 assert.equal(value.ui.number_texts[key].text,'1.5');assert.equal(value.graph.nodes[0].execution.arguments.threshold,2);
 value.ui.number_texts['wait:removed_node']={key:'wait:removed_node',text:'-',kind:'number'};assert.ok(!collectGraphEditorIssues(value,ctx,[cap]).some(i=>i.path==='ui.number_texts.wait:removed_node'));
});
test('editing a sibling of an unfinished structured numeric parameter preserves its raw text',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema.properties.settings={type:'object',properties:{threshold:{type:'number'},label:{type:'string'}},required:['threshold','label'],additionalProperties:false};
 let value=ready(ctx,[cap]);value=setAbilityInput(value,'step_1','settings',{mode:'literal',value:{threshold:1,label:'原名称'}});const key='ability:step_1:settings/threshold';value=applyNumericEdit(value,{key,text:'1.',kind:'number'},d=>d);value=setAbilityInput(value,'step_1','settings',{mode:'literal',value:{threshold:1,label:'新名称'}});assert.equal(value.ui.number_texts[key].text,'1.');assert.equal(value.graph.nodes[0].execution.arguments.settings.label,'新名称');assert.ok(collectGraphEditorIssues(value,ctx,[cap]).some(i=>i.path==='ui.number_texts.'+key));
});
test('every live unadded form blocks preview at its own path and never enters the graph implicitly',()=>{
 const ctx=context(),base=ready(ctx);assert.deepEqual(collectGraphEditorIssues(base,ctx,[]),[]);
 for(const [scratch,path] of [
  [{new_input_label:'补充业务资料'},'ui.new_input_label'],
  [{new_input_label:'   '},'ui.new_input_label'],
  [{new_input_kind:'number',new_input_dirty:true},'ui.new_input_label'],
  [{new_role_label:'复核负责人'},'ui.new_role_label'],
  [{new_role_actor:ACTOR},'ui.new_role_label'],
  [{schema_field_forms:{'step_1:root':{label:'现场备注',kind:'string'}}},'ui.schema_field_forms.step_1:root'],
  [{schema_field_forms:{'step_1:root':{label:'',kind:'number',dirty:true}}},'ui.schema_field_forms.step_1:root'],
 ]){const value=clone(base);Object.assign(value.ui,scratch);const before=clone(value);assert.equal(pendingGraphEditorItems(value).length,1);assert.ok(collectGraphEditorIssues(value,ctx,[]).some(i=>i.path===path));assert.throws(()=>buildGraphPreviewCommand(value,ctx,[]),e=>e.issues.some(i=>i.path===path));assert.deepEqual(value,before);assert.deepEqual(value.graph,base.graph);}
});
test('finishing input, role and result-field buffers applies their exact content and clears pending state atomically',()=>{
 const ctx=context();let value=ready(ctx);value.ui.new_input_label='补充数值';value.ui.new_input_kind='number';value.ui.new_input_dirty=true;const before=clone(value);
 value=completeGraphEditorBuffer(value,'new_input');assert.deepEqual(value.graph.input_schema.properties.input_1,{type:'number',title:'补充数值'});assert.equal(pendingGraphEditorItems(value).length,0);assert.equal(value.ui.new_input_kind,undefined);assert.deepEqual(before.graph.input_schema.properties,{});
 value.ui.new_role_label='补充复核职责';value.ui.new_role_actor=ACTOR;value=completeGraphEditorBuffer(value,'new_role');assert.equal(value.role_bindings['补充复核职责'],ACTOR);assert.equal(value.ui.new_role_actor,undefined);
 const original=clone(value.graph.nodes[0].output_schema.properties.answer_1);value.ui.schema_field_forms={'step_1:root':{label:'实际到访日期',kind:'date',dirty:true}};value=completeGraphEditorBuffer(value,'schema_field:step_1:root');assert.deepEqual(value.graph.nodes[0].output_schema.properties.answer_1,original);assert.deepEqual(value.graph.nodes[0].output_schema.properties.answer_2,{type:'string',format:'date',title:'实际到访日期'});assert.ok(value.graph.nodes[0].output_schema.required.includes('answer_2'));assert.equal(value.ui.schema_field_forms['step_1:root'],undefined);assert.equal(pendingGraphEditorItems(value).length,0);assert.ok(!('ui' in buildGraphPreviewCommand(value,ctx,[])));
 const incomplete=ready(ctx);incomplete.ui.new_role_actor=ACTOR;const retained=clone(incomplete);assert.throws(()=>completeGraphEditorBuffer(incomplete,'new_role'),/补全名称/);assert.deepEqual(incomplete,retained);
});
test('cancelling an unadded item archives exact text and allows preview without changing execution or losing history',()=>{
 const ctx=context();let value=ready(ctx);const wire=buildGraphPreviewCommand(value,ctx,[]);value.ui.new_input_label=' 原始名称  ';value.ui.new_input_kind='integer';value.ui.new_input_dirty=true;const before=clone(value);
 value=archiveGraphEditorBuffer(value,'new_input');assert.equal(pendingGraphEditorItems(value).length,0);assert.equal(value.ui.new_input_label,undefined);assert.equal(graphEditorBufferHistory(value)[0].form.label,' 原始名称  ');assert.equal(graphEditorBufferHistory(value)[0].form.kind,'integer');assert.equal(graphEditorBufferHistory(value)[0].restorable,true);assert.deepEqual(value.graph,before.graph);assert.deepEqual(buildGraphPreviewCommand(value,ctx,[]),wire);assert.equal(before.ui.new_input_label,' 原始名称  ');
 value=restoreGraphEditorBuffer(value,'pending_1');assert.equal(value.ui.new_input_label,' 原始名称  ');assert.equal(value.ui.new_input_kind,'integer');assert.equal(value.ui.pending_item_history.length,1);assert.ok(collectGraphEditorIssues(value,ctx,[]).some(i=>i.path==='ui.new_input_label'));assert.deepEqual(value.graph,before.graph);
});
test('restore never overwrites current work and never maps a cancelled field to a different source node',()=>{
 const ctx=context();let value=ready(ctx);value.ui.schema_field_forms={'step_1:root':{label:'已输入结果字段',kind:'boolean',dirty:true}};value=archiveGraphEditorBuffer(value,'schema_field:step_1:root');value.ui.schema_field_forms['step_1:root']={label:'正在写的新字段',kind:'string',dirty:true};const busy=clone(value);assert.throws(()=>restoreGraphEditorBuffer(value,'pending_1'),/不会覆盖/);assert.deepEqual(value,busy);
 value=archiveGraphEditorBuffer(value,'schema_field:step_1:root');value=restoreGraphEditorBuffer(value,'pending_1');assert.equal(value.ui.schema_field_forms['step_1:root'].label,'已输入结果字段');assert.equal(value.ui.selected_node_id,'step_1');value=focusGraphEditorBuffer(value,'schema_field:step_1:root');assert.equal(value.ui.pending_focus,'schema_field:step_1:root');assert.equal(value.ui.pending_item_history[1].form.label,'正在写的新字段');
 const rebound=clone(value);rebound.graph.nodes[0].source_step_id=uid(999);assert.equal(graphEditorBufferHistory(rebound)[0].restorable,false);assert.throws(()=>restoreGraphEditorBuffer(rebound,'pending_1'),/不能自动映射/);
 const stale=clone(value);stale.graph.nodes=stale.graph.nodes.filter(n=>n.id!=='step_1');assert.equal(graphEditorBufferHistory(stale)[0].restorable,false);const retained=clone(stale);assert.throws(()=>restoreGraphEditorBuffer(stale,'pending_1'),/不能自动映射/);assert.deepEqual(stale,retained);
});
test('history, replaced working drafts and buffers for removed fields do not permanently block an otherwise complete current design',()=>{
 const ctx=context(),value=ready(ctx);value.ui.new_input_kind='number';value.ui.owner_filter='查找原要求';value.ui.previous_working_draft={ui:{new_input_label:'旧来源未加内容'}};value.ui.schema_field_forms={'removed_node:root':{label:'旧步骤字段',kind:'number',dirty:true}};value.ui.pending_item_history=[{id:'pending_1',key:'new_role',kind:'role',title:'新增执行职责',business_name:value.business_name,form:{label:'旧职责',actor_id:''}}];
 assert.deepEqual(pendingGraphEditorItems(value),[]);assert.deepEqual(collectGraphEditorIssues(value,ctx,[]),[]);assert.ok(buildGraphPreviewCommand(value,ctx,[]));assert.equal(graphEditorBufferHistory(value).length,1);
});
test('directly added goal and predicate drafts remain explicit graph gaps instead of hidden text buffers',()=>{
 const ctx=context(),value=addPredicate(addGoal(ready(ctx),'新完成标准'),'新判断');assert.deepEqual(pendingGraphEditorItems(value),[]);const issues=collectGraphEditorIssues(value,ctx,[]);assert.ok(issues.some(i=>i.path==='graph.goals'));assert.ok(issues.some(i=>i.path==='graph.predicates.condition_1'));assert.throws(()=>buildGraphPreviewCommand(value,ctx,[]),e=>e.issues.some(i=>i.path==='graph.goals')&&e.issues.some(i=>i.path==='graph.predicates.condition_1'));assert.deepEqual(value.graph.predicates.condition_1,{op:'ref',key:''});assert.deepEqual(value.graph.goals[1].required_nodes,[]);
});
test('a current optional literal parameter must be provided or explicitly withdrawn, while historical choices do not block',()=>{
 const ctx=context('多模感知智能体'),cap=audioOption();cap.input_schema.properties.threshold={type:'number',title:'候选阈值'};let value=ready(ctx,[cap]);assert.deepEqual(collectGraphEditorIssues(value,ctx,[cap]),[]);
 value=updateGraphDraft(value,d=>{d.ui.input_modes={...d.ui.input_modes,'step_1:threshold':'literal'};});const before=clone(value),path='graph.nodes.step_1.threshold';assert.ok(collectGraphEditorIssues(value,ctx,[cap]).some(i=>i.path===path&&i.message.includes('填写该可选参数或明确改为不提供')));assert.throws(()=>buildGraphPreviewCommand(value,ctx,[cap]),e=>e.issues.some(i=>i.path===path));assert.equal(Object.hasOwn(value.graph.nodes[0].execution.arguments,'threshold'),false);assert.deepEqual(value,before);
 const provided=setAbilityInput(value,'step_1','threshold',{mode:'literal',value:1.5});assert.deepEqual(collectGraphEditorIssues(provided,ctx,[cap]),[]);assert.equal(buildGraphPreviewCommand(provided,ctx,[cap]).graph.nodes[0].execution.arguments.threshold,1.5);
 const omitted=setAbilityInput(value,'step_1','threshold',{mode:'unset'});omitted.ui.previous_working_draft={ui:{input_modes:{'step_1:threshold':'literal'}}};omitted.ui.previous_input_choices=[{field:'threshold',mode:'literal'}];omitted.ui.input_modes['removed_node:threshold']='literal';omitted.ui.input_modes['step_1:removed_field']='literal';assert.deepEqual(collectGraphEditorIssues(omitted,ctx,[cap]),[]);assert.equal(Object.hasOwn(buildGraphPreviewCommand(omitted,ctx,[cap]).graph.nodes[0].execution.arguments,'threshold'),false);
});
test('the real builtin aggregate operation enum needs no invented type and count produces the unchanged frozen contract',()=>{
 const ctx=context('数据管理智能体'),cap=aggregateOption(),original=clone(cap),value=aggregateDraft(ctx,cap,'count');assert.deepEqual(schemaKind(cap.input_schema.properties.operation),{kind:'string',nullable:false});assert.deepEqual(collectGraphEditorIssues(value,ctx,[cap]),[]);
 const command=buildGraphPreviewCommand(value,ctx,[cap]),pin=command.graph.nodes[0].execution;assert.deepEqual(pin.arguments,{field:'数量',operation:'count'});assert.deepEqual(pin.input_schema,original.input_schema);assert.deepEqual(pin.input_schema.properties.operation,{enum:['mean','sum','count']});assert.equal(Object.hasOwn(pin.input_schema.properties.operation,'type'),false);assert.equal(pin.capability_hash,original.content_hash);assert.equal(command.node_actions.step_1,'data');assert.deepEqual(cap,original);
 const bad=setAbilityInput(value,'step_1','operation',{mode:'literal',value:'median'}),before=clone(bad);assert.ok(collectGraphEditorIssues(bad,ctx,[cap]).some(i=>i.path==='graph.nodes.step_1.operation'&&i.message.includes('合同列出的取值')));assert.throws(()=>buildGraphPreviewCommand(bad,ctx,[cap]),e=>e.issues.some(i=>i.path==='graph.nodes.step_1.operation'));assert.deepEqual(bad,before);
});
test('enum-only primitive inference distinguishes booleans, integers, finite numbers and explicit null without guessing mixed types',()=>{
 for(const [spec,expected] of [[{enum:['count','sum']},{kind:'string',nullable:false}],[{enum:[true,false]},{kind:'boolean',nullable:false}],[{enum:[1,2]},{kind:'integer',nullable:false}],[{enum:[1,2.5]},{kind:'number',nullable:false}],[{enum:[1,null]},{kind:'integer',nullable:true}],[{enum:[null,'count']},{kind:'string',nullable:true}]])assert.deepEqual(schemaKind(spec),expected);
 for(const spec of [{enum:['count',1]},{enum:[true,1]},{enum:[{}]},{enum:[[]]},{enum:[]},{enum:[null]},{enum:[Infinity]},{enum:[9007199254740992]}])assert.equal(schemaKind(spec),null);
 const root={type:'object',properties:{operation:{$ref:'#/$defs/operation'}},$defs:{operation:{enum:['count','sum']}}};assert.deepEqual(schemaKind(root.properties.operation,root),{kind:'string',nullable:false});assert.equal(referenceSchemaKind(root.properties.operation,root),null);assert.equal(compatible({kind:'string',nullable:false},root.properties.operation,root),false);assert.throws(()=>addReference(ready(context()),{source:'input',field:'operation',spec:{enum:['count']}}),/显式引用类型/);
});
test('nullable enum membership is enforced before null handling and ambiguous enums stay blocked',()=>{
 const ctx=context('数据管理智能体');for(const operation of [{enum:['count',null]},{type:['string','null'],enum:['count',null]}]){const cap=aggregateOption(operation),value=aggregateDraft(ctx,cap,null);assert.deepEqual(collectGraphEditorIssues(value,ctx,[cap]),[]);assert.equal(buildGraphPreviewCommand(value,ctx,[cap]).graph.nodes[0].execution.arguments.operation,null);}
 const intersection={type:['string','null'],enum:['count']};assert.deepEqual(schemaKind(intersection),{kind:'string',nullable:false});assert.deepEqual(referenceSchemaKind(intersection),{kind:'string',nullable:true});
 for(const [operation,literal] of [[{enum:['count']},null],[intersection,null],[{enum:[null]},null],[{enum:['count',1]},'count'],[{enum:[false,1]},false]]){const cap=aggregateOption(operation),invalid=aggregateDraft(ctx,cap,literal);assert.ok(collectGraphEditorIssues(invalid,ctx,[cap]).some(i=>i.path==='graph.nodes.step_1.operation'));assert.throws(()=>buildGraphPreviewCommand(invalid,ctx,[cap]),e=>e.issues.some(i=>i.path==='graph.nodes.step_1.operation'));}
});
