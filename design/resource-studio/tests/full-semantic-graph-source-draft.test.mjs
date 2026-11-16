/** Invented source contracts exercise the actual authoring model, never DB authority. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyGraphWire,graphWireCanonical,graphWireRequestJSON} from '../src/full-semantic-graph-wire.mjs';
import {isTypedNumber,numericToken} from '../src/typed-numbers-v132.mjs';
import {createGraphEditorDraft,createNewDesignRevision,buildGraphPreviewCommand,collectGraphEditorIssues,
 clone,updateGraphDraft,setRoleMember,addOutputField,addControl,connectNodes,addGoal,
 selectGraphEventSource,readGraphEventSource,addWaitEntityInput,DRAFT_SCHEMA,DRAFT_SCHEMA_V2,
 CONTEXT_SCHEMA,CONTEXT_SCHEMA_V2,applyNumericEdit,numericDraftDisplay} from '../src/full-semantic-graph-editor-model.mjs';
import {graphEventDraftIssues,graphEventPreviewFields,restoreGraphCanonicalFragment} from '../src/full-semantic-graph-source-draft.mjs';

const uid=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0'),H='a'.repeat(64),ACTOR=uid(1);
const wire=canonical=>verifyGraphWire({schema:'report-full-semantic-graph-canonical-wire-v1',value:JSON.parse(canonical),canonical});
const hash=value=>createHash('sha256').update(graphWireCanonical(value),'utf8').digest('hex');
const seal=value=>wire(graphWireCanonical(value));
const POLICY={schema:'graph-wait-source-policy-v1',arrival_basis:'durable_admission',sequence:{open:'exclusive',closed_cut:'inclusive',order:'ascending'},
 deadline:{clock:'database_clock_timestamp',boundary:'exclusive',basis:'admission_decision_time'},transition:'buffer_complete_batch_before_advance',
 clock_regression:'block',result_scope:'candidate_only',source_time_verified:false,physical_state_verified:false,
 revocation:{new_admission:'block',new_execution:'block',history:'preserve',draft:'preserve',cancel:'preserve'}};

function sourceFixture(){
 // These are Python-style JSON number spellings from a synthetic server envelope.
 const result=wire('{"additionalProperties":false,"properties":{"观测数量🙂":{"maximum":1.0,"minimum":-0.0,"multipleOf":1e-05,"title":"观测数量","type":"number"}},"required":["观测数量🙂"],"type":"object"}');
 const identity={type:'string',format:'uuid'},properties={schema:{const:'graph-event-observation-output-v1'},adapter_id:{const:'processed-observation-v1'},
  observation_id:identity,entity_id:identity,source_id:identity,extractor_id:identity,invocation_id:identity,
  observed_at:{type:['string','null'],format:'date-time'},candidate_only:{type:'boolean'},result:{$ref:'#/$defs/extractor_result'},
  source_time_verified:{const:false},physical_state_verified:{const:false}};
 const output={$defs:{extractor_result:result},type:'object',additionalProperties:false,required:Object.keys(properties).sort(),properties};
 const contract={schema:'graph-event-observation-source-contract-v1',adapter_id:'processed-observation-v1',event_kind:'observation.data',correlation_kind:'entity_id',
  source_id:uid(40),source_hash:'b'.repeat(64),source_release_version:'1.2.0',extractor_id:uid(41),extractor_hash:'c'.repeat(64),extractor_release_version:'3.0.1',
  result_schema:result,output_schema:output,output_schema_hash:hash(output)};
 contract.contract_hash=hash(contract);
 return seal({producer_id:uid(42),label:'林地巡查资料处理',status:'active',contract_hash:hash(contract),contract,available:true,reason:null});
}
function contextFixture({legacy=false,source=sourceFixture()}={}){
 const step={name:'核对到达资料',agent:'人工确认',role:'资料负责人',purpose:'核对完整候选',output:'人工核对结果',next_index:null,branch_condition:'',else_index:null};
 const business={name:'巡查资料核对',trigger:'人工发起',output:'保存本次候选与核对结果',steps:[step],participants:[],interactions:[],conditions:[],coordination:[]};
 const owners=[{owner_path:'/domain',kind:'domain',source_fields:['value']},
  {owner_path:'/businesses/0',kind:'business',source_fields:['trigger','output']},
  {owner_path:'/businesses/0/steps/0',kind:'step',source_fields:['name','agent','role','purpose','output','next_index','branch_condition','else_index']},
  {owner_path:'/global_claims/0',kind:'global_claim',source_fields:['kind','statement']}].map(row=>({...row,value_hash:H,ref_hashes:[]}));
 return {schema:legacy?CONTEXT_SCHEMA:CONTEXT_SCHEMA_V2,handoff_id:uid(10),active:true,
  design:{handoff_id:uid(10),design_hash:H,result:{domain:'完全合成的协议测试',businesses:[business],objects:[],relationships:[],
   global_claims:[{kind:'要求',statement:'保留资料候选和等待范围'}],questions:[]},business_bindings:[{candidate_index:0,step_ids:[uid(20)]}]},
  manifest:{owners:owners.map(row=>({path:row.owner_path,value_hash:H,ref_hashes:[]})),refs:[]},owners,
  configuration:{revision:7,hash:H},proofs:[{id:uid(30),proof_hash:H,current_source_verified:true,reason:null}],
  schema_origins:{objects:[],relationships:[]},adoptions:[],
  event_wait:legacy?{available:false,reason:'原版未冻结消息来源'}:{available:true,reason:null,policy:POLICY,sources:[source]}};
}
function mapOwners(value,context){return updateGraphDraft(value,d=>{for(const row of d.owner_bindings){
 const spec=context.owners.find(item=>item.owner_path===row.owner_path);row.reason='明确采用合成工作区安排，仍不认证原文事实';
 if(spec.kind==='domain'){row.resolution='informational';continue;}row.resolution='typed';
 for(const clause of row.clauses){if(spec.kind==='business')clause.targets=[{kind:clause.source_field==='trigger'?'node':'goal',id:clause.source_field==='trigger'?'step_1':'goal_1'}];
  else if(spec.kind==='step')clause.targets=clause.source_field==='next_index'?[{kind:'edge',id:'edge_1'}]:['branch_condition','else_index'].includes(clause.source_field)?[]:[{kind:'node',id:'step_1'}];
  else clause.targets=[{kind:'goal',id:'goal_1'},...d.graph.nodes.filter(node=>node.kind==='wait').map(node=>({kind:'wait',id:node.id})),...Object.keys(d.graph.references).map(id=>({kind:'value_ref',id}))];}
 }});}
function ready(context,{wait=true,select=true}={}){
 let value=createGraphEditorDraft(context,'巡查资料核对');value.proof_id=uid(30);
 value=setRoleMember(value,'资料负责人',ACTOR);value=addOutputField(value,'step_1','是否已核对','boolean');
 value=addControl(value,'end');
 if(wait){value=addControl(value,'wait');value=updateGraphDraft(value,d=>{d.graph.nodes.find(node=>node.kind==='wait').timeout_seconds=60;});
  value=addWaitEntityInput(value,'subscription_1','本次巡查关联对象');if(select)value=selectGraphEventSource(value,context,'subscription_1',uid(42));
  value=connectNodes(value,'step_1','wait_1');value=connectNodes(value,'wait_1','end_1');value=connectNodes(value,'wait_1','end_1','timeout');
 }else value=connectNodes(value,'step_1','end_1');
 value.graph.entry='step_1';value=addGoal(value,'实际取得本次核对结果');value.graph.goals[0].required_nodes=wait?['step_1','wait_1']:['step_1'];
 return mapOwners(value,context);
}
function adoptedContext(context,value){
 const command=buildGraphPreviewCommand(value,context,[]),binding={schema:'report-full-semantic-graph-binding-v4',binding_hash:H,
  proof_id:command.proof_id,graph:command.graph,owner_bindings:command.owner_bindings,event_source_bindings:command.event_source_bindings,
  event_admission_policy:command.event_admission_policy,role_bindings:{资料负责人:{user_id:ACTOR,username:'合成核对员',membership_role:'operator'}},
  source_design:context.design,source_business:context.design.result.businesses[0]};
 return {...context,adoptions:[{business_name:value.business_name,origin:{adoption_id:uid(50)},receipt:{receipt_hash:H},binding,
  root_id:uid(50),parent_id:null,is_current:true,execution_scope:{external_node_ids:[],node_actions:{}}}]};
}

test('v2 source selection freezes the entire nested contract and explicit object scope',()=>{
 const context=contextFixture(),before=graphWireCanonical(context.event_wait.sources[0]),draft=ready(context);
 assert.equal(draft.schema,DRAFT_SCHEMA_V2);assert.deepEqual(collectGraphEditorIssues(draft,context,[]),[]);
 const body=buildGraphPreviewCommand(draft,context,[]),pin=body.event_source_bindings.subscription_1;
 assert.equal(body.schema,'report-full-semantic-graph-preview-command-v4');
 assert.deepEqual(Object.keys(body).sort(),['schema','configuration_revision','configuration_hash','proof_id','business_name','graph','owner_bindings','role_bindings','external_node_ids','node_actions','event_source_bindings','event_admission_policy'].sort());
 assert.deepEqual(pin.scope,{entity_policy:'frozen_task_entities',correlation_ref:draft.graph.subscriptions[0].correlation_ref,source_policy:'exact_registered_source'});
 assert.equal(graphWireCanonical(pin.contract),graphWireCanonical(context.event_wait.sources[0].contract));
 assert.equal(graphWireCanonical(body.graph.subscriptions[0].output_schema),graphWireCanonical(pin.contract.output_schema));
 assert.deepEqual(body.event_admission_policy,POLICY);assert.equal(graphWireCanonical(context.event_wait.sources[0]),before);
 assert.equal('event_source_fragments' in body,false);assert.equal('event_admission_policy_canonical' in body,false);
});

test('JSON local persistence and repeated edits retain server integral floats, negative zero and exponents',()=>{
 const context=contextFixture(),draft=ready(context),stored=JSON.stringify(draft);
 let restored=JSON.parse(stored);restored=updateGraphDraft(restored,d=>{d.ui.node_labels.wait_1='等候巡查处理';});
 const body=buildGraphPreviewCommand(restored,context,[]),canonical=graphWireCanonical(body);
 assert.ok(canonical.includes('"maximum":1.0'));assert.ok(canonical.includes('"minimum":-0.0'));assert.ok(canonical.includes('"multipleOf":1e-05'));
 assert.equal(canonical.split('"maximum":1.0').length,4); // result contract, nested envelope, subscription envelope.
 const source=readGraphEventSource(restored,'subscription_1');assert.ok(Object.isFrozen(source.contract));
 assert.equal(Object.is(source.contract.result_schema.properties['观测数量🙂'].minimum,-0),true);
 assert.equal(JSON.stringify(draft),stored);assert.equal('ui' in body,false);
});

test('a stale or absent source never refreshes pins, clears saved input, or permits a new preview',()=>{
 const context=contextFixture(),draft=ready(context),before=JSON.stringify(draft);
 for(const choices of [[{...context.event_wait.sources[0],available:false,reason:'来源发布版本变化'}],[]]){
  const stale={...context,event_wait:{...context.event_wait,available:false,reason:'请核对原来源',sources:choices}};
  assert.ok(collectGraphEditorIssues(draft,stale,[]).some(issue=>issue.message.includes('原选择')||issue.message.includes('原来源')));
  assert.throws(()=>buildGraphPreviewCommand(draft,stale,[]));
  assert.throws(()=>selectGraphEventSource(draft,stale,'subscription_1',uid(42)),/保留/);
  assert.equal(JSON.stringify(draft),before);assert.equal(readGraphEventSource(draft,'subscription_1').label,'林地巡查资料处理');
 }
});

test('same source identity with changed contract bytes requires explicit reselection',()=>{
 const context=contextFixture(),draft=ready(context),row=context.event_wait.sources[0];
 const changedContract=clone(row.contract);changedContract.source_release_version='1.2.1';
 const next={...context,event_wait:{...context.event_wait,sources:[{...row,contract_hash:'d'.repeat(64),contract:changedContract}]}};
 assert.ok(graphEventDraftIssues(draft,next).some(issue=>issue.message.includes('版本或字段规则已变化')));
 assert.equal(readGraphEventSource(draft,'subscription_1').contract.source_release_version,'1.2.0');
});

test('removed fields, invented bindings, changed policy and invalid correlation remain blockers',()=>{
 const context=contextFixture(),draft=ready(context),original=JSON.stringify(draft);
 const broken=[
  updateGraphDraft(draft,d=>{delete d.graph.subscriptions[0].output_schema.properties.result;}),
  updateGraphDraft(draft,d=>{d.event_source_fragments.unused=d.event_source_fragments.subscription_1;}),
  updateGraphDraft(draft,d=>{d.event_admission_policy_canonical=graphWireCanonical({...POLICY,source_time_verified:true});}),
  updateGraphDraft(draft,d=>{d.graph.references[d.graph.subscriptions[0].correlation_ref].nullable=true;}),
  updateGraphDraft(draft,d=>{d.graph.input_schema.required=[];}),
  updateGraphDraft(draft,d=>{d.graph.subscriptions[0].event_type='invented.event';}),
  updateGraphDraft(draft,d=>{d.event_source_fragments.subscription_1=d.event_source_fragments.subscription_1.replace('"maximum":1.0','"maximum":1');}),
 ];
 for(const value of broken){assert.ok(graphEventDraftIssues(value,context).length);assert.throws(()=>buildGraphPreviewCommand(value,context,[]));}
 assert.equal(JSON.stringify(draft),original);
});

test('v4 revision restores the actual parent and original source canonical metadata',()=>{
 const initial=contextFixture(),draft=ready(initial),context=adoptedContext(initial,draft),saved=createGraphEditorDraft(context,draft.business_name);
 assert.equal(saved.schema,DRAFT_SCHEMA_V2);assert.deepEqual(saved.parent,{id:uid(50),receipt_hash:H});
 const cold=JSON.parse(JSON.stringify(saved)),body=buildGraphPreviewCommand(cold,context,[]);
 assert.equal(body.schema,'report-full-semantic-graph-revision-preview-command-v4');assert.deepEqual(body.parent,saved.parent);
 assert.equal(graphWireCanonical(body.event_source_bindings),graphWireCanonical(context.adoptions[0].binding.event_source_bindings));
 assert.ok(graphWireCanonical(body).includes('"maximum":1.0'));
 const inactive={...context,event_wait:{...context.event_wait,available:false,sources:[]}};
 const retained=createGraphEditorDraft(inactive,draft.business_name);assert.equal(readGraphEventSource(retained,'subscription_1').contract_hash,readGraphEventSource(saved,'subscription_1').contract_hash);
 assert.throws(()=>buildGraphPreviewCommand(retained,inactive,[]));
});

test('an adopted v4 revision retains unchanged numeric expressions after an unrelated human edit',()=>{
 const initial=contextFixture(),draft=ready(initial),context=adoptedContext(initial,draft),binding=context.adoptions[0].binding;
 const predicates=wire('{"numeric_rule":{"left":{"kind":"number","op":"value","value":1.0},"op":"eq","right":{"kind":"number","op":"value","value":1.0}}}');
 binding.graph={...binding.graph,predicates,nodes:binding.graph.nodes.map(node=>node.kind==='human'?{...node,guards:{...node.guards,precondition:'numeric_rule'}}:node)};
 binding.owner_bindings=binding.owner_bindings.map(row=>row.owner_path==='/global_claims/0'?{...row,clauses:row.clauses.map(clause=>({...clause,targets:[...clause.targets,{kind:'predicate',id:'numeric_rule'}]}))}:row);
 let saved=createGraphEditorDraft(context,draft.business_name);saved=JSON.parse(JSON.stringify(saved));
 saved=updateGraphDraft(saved,d=>{d.graph.nodes.find(node=>node.kind==='human').output_schema.properties.answer_1.title='核对已完成';});
 const body=buildGraphPreviewCommand(saved,context,[]);
 assert.equal(graphWireCanonical(body.graph.predicates),graphWireCanonical(predicates));
 assert.equal(body.graph.nodes.find(node=>node.kind==='human').output_schema.properties.answer_1.title,'核对已完成');
 assert.ok(graphWireCanonical(body).includes('"value":1.0'));
});

function numericAdoptedContext(left='-0.0',right='0.0'){
 const initial=contextFixture(),context=adoptedContext(initial,ready(initial)),binding=context.adoptions[0].binding;
 const predicates=wire('{"numeric_rule":{"left":{"kind":"number","op":"value","value":'+left+'},"op":"eq","right":{"kind":"number","op":"value","value":'+right+'}}}');
 binding.graph={...binding.graph,predicates,nodes:binding.graph.nodes.map(node=>node.kind==='human'?{...node,guards:{...node.guards,precondition:'numeric_rule'}}:node)};
 binding.owner_bindings=binding.owner_bindings.map(row=>row.owner_path==='/global_claims/0'?{...row,clauses:row.clauses.map(clause=>({...clause,targets:[...clause.targets,{kind:'predicate',id:'numeric_rule'}]}))}:row);
 return context;
}
test('V132 adopted negative zero survives cold storage and an unrelated edit without changing frozen source fields',()=>{
 const context=numericAdoptedContext(),draft=createGraphEditorDraft(context,'巡查资料核对'),stored=JSON.stringify(draft);
 let cold=JSON.parse(stored);assert.equal(Object.is(cold.graph.predicates.numeric_rule.left.value,-0),false);
 assert.equal(numericDraftDisplay(cold,'predicate:numeric_rule/left',cold.graph.predicates.numeric_rule.left.value),'-0.0');
 cold=updateGraphDraft(cold,d=>{d.graph.nodes.find(node=>node.kind==='human').output_schema.properties.answer_1.title='新的核对说明';});
 assert.equal(Object.is(cold.graph.predicates.numeric_rule.left.value,-0),true);
 const body=buildGraphPreviewCommand(cold,context,[],{typedNumbers:true}),encoded=graphWireRequestJSON(body);
 assert.ok(encoded.includes('"left":{"kind":"number","op":"value","value":-0.0}'));
 assert.ok(encoded.includes('"right":{"kind":"number","op":"value","value":0.0}'));
 assert.ok(encoded.includes('"maximum":1.0'));assert.ok(encoded.includes('"minimum":-0.0'));
 assert.equal(cold.ui.adopted_graph_canonical,draft.ui.adopted_graph_canonical);
 assert.equal(graphWireCanonical(body.event_source_bindings),graphWireCanonical(context.adoptions[0].binding.event_source_bindings));
 assert.equal(JSON.stringify(draft),stored);assert.doesNotThrow(()=>JSON.stringify(cold));
});

test('V132 explicit zero and float edits never resurrect an old same-valued source subtree',()=>{
 for(const [original,text,expected]of [['-0.0','0','0'],['0.0','-0.0','-0.0'],['1.0','1','1'],['1.0','1.0','1.0'],['1.0','1e21','1e21']]){
  const context=numericAdoptedContext(original,'1.0');let draft=JSON.parse(JSON.stringify(createGraphEditorDraft(context,'巡查资料核对')));
  const originalCanonical=draft.ui.adopted_graph_canonical,key='predicate:numeric_rule/left';
  draft=applyNumericEdit(draft,{key,text,kind:'number',label:'判断左值'},d=>updateGraphDraft(d,item=>{item.graph.predicates.numeric_rule.left.value=Number(text);}));
  const cold=JSON.parse(JSON.stringify(draft)),body=buildGraphPreviewCommand(cold,context,[],{typedNumbers:true}),left=body.graph.predicates.numeric_rule.left.value;
  assert.ok(isTypedNumber(left),text);assert.equal(numericToken(left),expected);assert.equal(draft.ui.adopted_graph_canonical,originalCanonical);
  assert.ok(graphWireRequestJSON(body).includes('"left":{"kind":"number","op":"value","value":'+expected+'}'));
  assert.equal(graphWireCanonical(body.graph.predicates.numeric_rule.right),'{"kind":"number","op":"value","value":1.0}');
  assert.equal(body.schema,'report-full-semantic-graph-revision-preview-command-v4');assert.deepEqual(body.parent,draft.parent);
  assert.ok(graphWireRequestJSON(body).includes('"minimum":-0.0'));
 }
});

test('V132 pending numeric edits remain durable and cannot be hidden by an old adopted graph',()=>{
 const context=numericAdoptedContext('1.0','1.0');let draft=createGraphEditorDraft(context,'巡查资料核对');
 const key='predicate:numeric_rule/left';draft=applyNumericEdit(draft,{key,text:'1e',kind:'number',label:'判断左值'},()=>{throw Error('incomplete input cannot invoke its numeric setter');});
 const cold=JSON.parse(JSON.stringify(draft)),before=JSON.stringify(cold);
 assert.throws(()=>buildGraphPreviewCommand(cold,context,[],{typedNumbers:true}),error=>error.issues.some(issue=>issue.path==='ui.number_texts.'+key));
 assert.equal(JSON.stringify(cold),before);assert.equal(cold.ui.number_texts[key].text,'1e');
 const stale={...context,event_wait:{...context.event_wait,sources:context.event_wait.sources.map(source=>({...source,available:false,reason:'此来源已停止'}))}};
 assert.throws(()=>buildGraphPreviewCommand(cold,stale,[],{typedNumbers:true}));assert.equal(JSON.stringify(cold),before);
});

test('an incomplete adopted v4 policy is never filled from the current context',()=>{
 const initial=contextFixture(),draft=ready(initial),context=adoptedContext(initial,draft);
 delete context.adoptions[0].binding.event_admission_policy;
 assert.throws(()=>createGraphEditorDraft(context,draft.business_name),/等待接收规则/);
});

test('source design replacement retains the old source fragment for comparison but selects no new source',()=>{
 const initial=contextFixture(),draft=ready(initial),context=adoptedContext(initial,draft),retained=createGraphEditorDraft(context,draft.business_name);
 const replacement={...context,handoff_id:uid(11),design:{...context.design,handoff_id:uid(11),business_bindings:[{candidate_index:0,step_ids:[uid(21)]}]}};
 const next=createNewDesignRevision(retained,replacement);
 assert.deepEqual(next.event_source_fragments,{});assert.deepEqual(next.ui.previous_working_draft.event_source_fragments,retained.event_source_fragments);
 assert.equal(next.ui.previous_working_draft.event_admission_policy_canonical,retained.event_admission_policy_canonical);
 assert.deepEqual(next.parent,retained.parent);assert.deepEqual(next.graph.subscriptions,[]);assert.equal(next.graph.nodes[0].source_step_id,uid(21));
});

test('legacy context and v1 command bytes retain their old family and no added event defaults',()=>{
 const context=contextFixture({legacy:true}),draft=ready(context,{wait:false}),before=JSON.stringify(draft);
 assert.equal(draft.schema,DRAFT_SCHEMA);const body=buildGraphPreviewCommand(draft,context,[]);
 assert.equal(body.schema,'report-full-semantic-graph-preview-command-v3');assert.equal('event_source_bindings' in body,false);
 assert.equal('event_admission_policy' in body,false);assert.equal('event_source_fragments' in draft,false);
 const upgradedContext={...context,schema:CONTEXT_SCHEMA_V2,event_wait:contextFixture().event_wait};
 assert.equal(buildGraphPreviewCommand(draft,upgradedContext,[]).schema,'report-full-semantic-graph-preview-command-v3');assert.equal(JSON.stringify(draft),before);
});

test('legacy waiting draft upgrades only on an explicit real source selection',()=>{
 const legacy=contextFixture({legacy:true}),old=ready(legacy,{select:false}),context={...legacy,schema:CONTEXT_SCHEMA_V2,event_wait:contextFixture().event_wait};
 assert.equal(old.schema,DRAFT_SCHEMA);assert.ok(collectGraphEditorIssues(old,context,[]).some(issue=>issue.message.includes('不会自动升级')));
 const selected=selectGraphEventSource(old,context,'subscription_1',uid(42));assert.equal(selected.schema,DRAFT_SCHEMA_V2);
 assert.equal(buildGraphPreviewCommand(selected,context,[]).schema,'report-full-semantic-graph-preview-command-v4');
 assert.equal(old.graph.subscriptions[0].event_type,'');assert.equal('event_source_fragments' in old,false);
});

test('new object correlation is required, explicit, and never chooses an entity for the user',()=>{
 const context=contextFixture(),draft=ready(context),subscription=draft.graph.subscriptions[0],ref=draft.graph.references[subscription.correlation_ref];
 assert.equal(ref.source,'input');assert.equal(ref.kind,'string');assert.equal(ref.nullable,false);
 assert.ok(draft.graph.input_schema.required.includes(ref.field));assert.equal('default' in draft.graph.input_schema.properties[ref.field],false);
 assert.equal('graph_inputs' in draft,false);assert.equal('entity_ids' in draft,false);
});

test('corrupt canonical fragments and normalization without the original server envelope fail closed',()=>{
 const context=contextFixture(),draft=ready(context);
 const bad=updateGraphDraft(draft,d=>{d.event_source_fragments.subscription_1='{"schema":"bad","source":{}}';});
 assert.throws(()=>graphEventPreviewFields(bad,context),/保留/);
 assert.throws(()=>restoreGraphCanonicalFragment('{"b":1,"a":2}'));
 const normalized=clone(context);assert.throws(()=>selectGraphEventSource(draft,normalized,'subscription_1',uid(42)),/服务器/);
 assert.equal(readGraphEventSource(draft,'subscription_1').producer_id,uid(42));
});
