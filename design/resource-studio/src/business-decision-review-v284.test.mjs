/** Synthetic DTO display checks only: no browser, API, IAM, model or ACK proof. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {BUSINESS_DECISION_REVIEW_RESULT,BUSINESS_DECISION_REVIEW_CONTEXT,DECISION_PLAN_V285,checkedDecisionReviewContext,decisionReviewContextPath,decisionReviewView,decisionPlanView,decisionPlanSummaryView,isBusinessDecisionReviewResult,isDecisionPlanV285,factValueText,reviewDecisionBinding,unitText} from './business-decision-review-v284.mjs';

const id=n=>'28400000-0000-4000-8000-'+String(n).padStart(12,'0');
const clone=value=>JSON.parse(JSON.stringify(value));
const flags=['can_dispatch','permissions_verified','source_authenticity_verified','current_outcome_verified','goal_completion_verified','data_reception_verified'];
const quote=(source,text)=>({source_ref:source.source_ref,quote_start:0,quote_end:Array.from(text??source.text).length,quote:text??source.text});

function context(){
 const scope={task_id:id(1),activation_id:'reasoner:original',goal_snapshot_hash:'b'.repeat(64),entity_ids:[id(2)]};
 const source={source_ref:'capability_1',kind:'capability',source_id:id(3),content_hash:'c'.repeat(64),text:'{"operation":"resense_from_context","ports":["temperature_c"]}',readability:'readable'};
 const fact={fact_ref:'declaration_1',kind:'capability_declaration',entity_id:id(2),field:'capability_declaration',unit:null,semantics:'statement',time_scope:{start:null,end:null},value:'已登记可申请温度观测的能力。',source_refs:[quote(source)]};
 const requirement={requirement_ref:'need_temperature',entity_id:id(2),field:'temperature_c',value_type:'number',unit:'Cel',semantics:'instantaneous',time_scope:{start:'2026-11-01T12:00:00+00:00',end:'2026-11-01T12:10:00+00:00'},required_kind:'observation',time_mode:'bounded'};
 const capability={capability_ref:'cap_resense',capability_id:id(3),capability_hash:source.content_hash,release_version:'2.0.0',input_schema_hash:'d'.repeat(64),output_schema_hash:'e'.repeat(64),source_ref:source.source_ref,action_kind:'information_acquisition',output_ports:[],information_targets:[{port_ref:'port_1',pointer:'/requested_observation/temperature_c',field:'temperature_c',value_type:'number',unit:'Cel'}]};
 const candidate={candidate_ref:'candidate_2',proposal:'请求原批准范围内的模拟温度观测，取得后另行核查。',capability_refs:[capability.capability_ref],requirement_mappings:[{requirement_ref:requirement.requirement_ref,capability_ref:capability.capability_ref,delivery:'requested_observation',port_ref:'port_1'}],claims:[]};
 const decision_input={schema:'business-decision-input-v274',decision_intent:'information_acquisition',scope:clone(scope),requirements:[requirement],sources:[source],capabilities:[capability],candidates:[candidate],facts:[fact],known_conflicts:[]};
 const model_value={schema:'business-decision-review-value-v284',candidate_reviews:[{candidate_ref:candidate.candidate_ref,suitability:'suitable',reasoning:'登记端口可以申请所需字段，尚未取得未来观测。',supporting_fact_refs:[fact.fact_ref],blocking_conditions:[]}],source_reviews:[{source_ref:source.source_ref,assessment:'reviewed',reason:'已阅读原登记声明。'}],conflicts:[],recommendation:{candidate_ref:candidate.candidate_ref,reason:'建议补充信息，不把申请当作观测结果。',supporting_fact_refs:[fact.fact_ref]}};
 const mechanical_review={candidates:[{candidate_ref:candidate.candidate_ref,capability_refs:[capability.capability_ref],requirement_refs:[requirement.requirement_ref],covered_requirement_refs:[requirement.requirement_ref],uncovered_requirement_refs:[],unknown_requirement_refs:[],applicability:'applicable',support:'supported',blocking_conditions:[]}],sources:[{source_ref:source.source_ref,assigned_fact_refs:[fact.fact_ref],assessment:'reviewed'}],missing_requirement_refs:[requirement.requirement_ref],fact_consistency:'unknown',current_facts_sufficient:false,conflicts:[]};
 const result={schema:BUSINESS_DECISION_REVIEW_RESULT,input_hash:'a'.repeat(64),decision_intent:'information_acquisition',scope:clone(scope),model_value,mechanical_review,review_state:'ready_for_review',...Object.fromEntries(flags.map(key=>[key,false]))};
 return {schema:BUSINESS_DECISION_REVIEW_CONTEXT,task_id:id(1),activation_id:scope.activation_id,invocation_id:id(4),input_hash:result.input_hash,result,decision_input,fields:[{entity_id:id(2),field:'temperature_c',label:'温度',value_type:'number',unit:'Cel'}],fact_values:[{fact_ref:fact.fact_ref,text:'已登记可申请温度观测的能力。'}],simulation_only:true};
}
function checked(value,expected=value.result){return checkedDecisionReviewContext(value,{taskId:id(1),activationId:'reasoner:original',result:expected});}
function addObservation(value,{ref='temperature_1',scalar=25,text='25',sourceText='原记录温度为25。',unit='Cel'}={}){
 const source={source_ref:'source_'+ref,kind:'observation',source_id:id(value.decision_input.sources.length+10),content_hash:'f'.repeat(64),text:sourceText,readability:'readable'};
 const fact={fact_ref:ref,kind:'observation',entity_id:id(2),field:'temperature_c',unit,semantics:'instantaneous',time_scope:{start:'2026-11-01T12:05:00+00:00',end:'2026-11-01T12:05:00+00:00'},value:scalar,source_refs:[quote(source)]};
 value.decision_input.sources.push(source);value.decision_input.facts.push(fact);value.fact_values.push({fact_ref:ref,text});
 value.result.model_value.source_reviews.push({source_ref:source.source_ref,assessment:'reviewed',reason:'已核对原观测资料。'});
 value.result.mechanical_review.sources.push({source_ref:source.source_ref,assigned_fact_refs:[ref],assessment:'reviewed'});
 value.result.mechanical_review.missing_requirement_refs=[];value.result.mechanical_review.fact_consistency='consistent';value.result.mechanical_review.current_facts_sufficient=true;
 return fact;
}

test('separates mechanical port coverage from missing observations without assigning authority',()=>{
 const value=context(),before=clone(value);assert.equal(checked(value),value);
 const view=decisionReviewView(value);assert.equal(view.candidates[0].proposal,value.decision_input.candidates[0].proposal);
 assert.deepEqual(view.candidates[0].covered,['温度']);assert.deepEqual(view.missing,['温度']);assert.equal(view.sufficient,false);
 assert.equal(view.candidates[0].facts[0].declaration,true);assert.deepEqual(view.candidates[0].facts[0].targetLabels,['温度']);
 assert.equal(view.simulation,true);assert.deepEqual(value,before);assert.ok(flags.every(key=>value.result[key]===false));
});

test('nullable recommendation and review-required retain the original human-facing opinion',()=>{
 const value=context();value.result.model_value.recommendation.candidate_ref=null;value.result.model_value.recommendation.supporting_fact_refs=[];value.result.review_state='needs_information';
 assert.equal(decisionReviewView(checked(value)).recommendation.label,'暂不推荐具体方案');
 value.result.model_value.recommendation.candidate_ref='candidate_2';value.result.model_value.candidate_reviews[0].suitability='uncertain';value.result.mechanical_review.candidates[0].applicability='unknown';value.result.review_state='review_required';
 const view=decisionReviewView(checked(value));assert.equal(view.state,'需要人工核对');assert.equal(view.candidates[0].suitability,'模型尚不能判断');
 assert.equal(value.result.model_value.recommendation.candidate_ref,'candidate_2');
});

test('uses server-original numeric spelling instead of the JS-rounded scalar',()=>{
 const value=context(),fact=addObservation(value,{scalar:9007199254740992,text:'9007199254740993',sourceText:'原记录温度为9007199254740993。'});
 value.result.model_value.candidate_reviews[0].supporting_fact_refs.push(fact.fact_ref);
 const view=decisionReviewView(checked(value));assert.equal(view.candidates[0].facts[1].valueText,'9007199254740993');
 assert.match(factValueText(null,fact),/未显示近似值/);
 value.fact_values=value.fact_values.filter(row=>row.fact_ref!==fact.fact_ref);
 assert.match(decisionReviewView(checked(value)).candidates[0].facts[1].valueText,/未显示近似值/);
});

test('shows both unitless conflict records, original times and source quotes',()=>{
 const value=context(),left=addObservation(value,{ref:'left',scalar:true,text:'true',sourceText:'原状态为true。',unit:null}),right=addObservation(value,{ref:'right',scalar:false,text:'false',sourceText:'原状态为false。',unit:null});
 value.decision_input.requirements[0].value_type='boolean';value.decision_input.requirements[0].unit=null;
 value.decision_input.capabilities[0].information_targets[0].value_type='boolean';value.decision_input.capabilities[0].information_targets[0].unit=null;
 value.fields[0].value_type='boolean';value.fields[0].unit=null;value.fields[0].label='原状态';
 const conflict={conflict_ref:'conflict_state',fact_refs:[left.fact_ref,right.fact_ref],reason:'同刻原状态不同。'};
 value.decision_input.known_conflicts=[clone(conflict)];value.result.model_value.conflicts=[clone(conflict)];
 value.result.mechanical_review.conflicts=[{...conflict,entity_id:id(2),field:'temperature_c',unit:null,semantics:'instantaneous',time_scope:clone(left.time_scope),sides:[left,right].map(fact=>({fact_ref:fact.fact_ref,value:fact.value,source_refs:clone(fact.source_refs)}))}];
 value.result.mechanical_review.fact_consistency='contradictory';value.result.mechanical_review.current_facts_sufficient=false;value.result.review_state='conflicting_evidence';
 const view=decisionReviewView(checked(value));assert.equal(view.conflicts[0].unit,'无单位标注');
 assert.deepEqual(view.conflicts[0].sides.map(row=>row.valueText),['是','否']);
 assert.ok(view.conflicts[0].sides.every(row=>row.citations[0].quote));assert.match(view.conflicts[0].time,/12:05/);
 assert.notEqual(unitText(null),'明确为空');
});

for(const damage of ['context_schema','result_schema','input_schema','model_schema','task','activation','invocation','hash','scope_hash','scope_entities'])test('rejects unknown schema or original-scope mismatch: '+damage,()=>{
 const value=context();
 if(damage==='context_schema')value.schema='other';else if(damage==='result_schema')value.result.schema='business-decision-result-v274';else if(damage==='input_schema')value.decision_input.schema='other';else if(damage==='model_schema')value.result.model_value.schema='other';else if(damage==='task')value.task_id=id(99);else if(damage==='activation')value.activation_id='another';else if(damage==='invocation')value.invocation_id='bad';else if(damage==='hash')value.input_hash='0'.repeat(64);else if(damage==='scope_hash')value.decision_input.scope.goal_snapshot_hash='0'.repeat(64);else value.decision_input.scope.entity_ids=[id(99)];
 assert.throws(()=>checked(value));
});

for(const flag of flags)for(const fake of [true,0,null])test('rejects a forged or coerced authority flag: '+flag+'='+fake,()=>{
 const value=context();value.result[flag]=fake;assert.equal(isBusinessDecisionReviewResult(value.result),false);assert.throws(()=>checked(value));
});

test('cross-checks the loaded graph result while preserving wire-safe numeric display',()=>{
 const value=context(),expected=clone(value.result);value.result.model_value.candidate_reviews[0].reasoning='另一份意见。';
 assert.throws(()=>checked(value,expected));
});

for(const damage of ['unknown_fact','source_assignment','fake_quote','duplicate_spelling','unknown_spelling','fake_observation','missing_candidate_ref'])test('rejects forged original references: '+damage,()=>{
 const value=context();
 if(damage==='unknown_fact')value.result.model_value.candidate_reviews[0].supporting_fact_refs=['invented_fact'];else if(damage==='source_assignment')value.result.mechanical_review.sources[0].assigned_fact_refs=[];else if(damage==='fake_quote')value.decision_input.facts[0].source_refs[0].quote='编造引用';else if(damage==='duplicate_spelling')value.fact_values.push(clone(value.fact_values[0]));else if(damage==='unknown_spelling')value.fact_values[0].fact_ref='invented_fact';else if(damage==='fake_observation')value.decision_input.facts[0].kind='observation';else value.decision_input.candidates[0].candidate_ref=null;
 assert.throws(()=>checked(value));
});

test('packet review never relocates declaration facts or invents a packet observation',()=>{
 const value=context(),source={source_ref:'packet_1',kind:'packet',source_id:id(20),content_hash:'1'.repeat(64),text:null,readability:'readable'};
 value.decision_input.sources.push(source);value.result.model_value.source_reviews.push({source_ref:source.source_ref,assessment:'reviewed',reason:'已核对资料包。'});value.result.mechanical_review.sources.push({source_ref:source.source_ref,assigned_fact_refs:[],assessment:'reviewed'});
 const view=decisionReviewView(checked(value));assert.equal(view.sources[1].factCount,0);assert.ok(!value.decision_input.facts.some(row=>row.kind==='observation'));
 value.result.mechanical_review.sources[1].assigned_fact_refs=['declaration_1'];assert.throws(()=>checked(value));
});

test('matches Python codepoint quote offsets for Chinese plus astral characters',()=>{
 const value=context(),source=value.decision_input.sources[0];source.text='登记🌲能力';value.decision_input.facts[0].source_refs=[quote(source)];
 assert.equal(Array.from(source.text).length,5);assert.equal(checked(value),value);
});

test('builds only the fixed same-origin original-context path',()=>{
 assert.equal(decisionReviewContextPath(id(1),'reasoner:original'),'/workspace/tasks/'+id(1)+'/decision-review-context-v284?activation_id=reasoner%3Aoriginal');
 assert.throws(()=>decisionReviewContextPath('https://example.invalid','reasoner'));assert.throws(()=>decisionReviewContextPath(id(1),''));
});

function humanCase(){
 const value=context();value.result.model_value.candidate_reviews[0].suitability='uncertain';value.result.mechanical_review.candidates[0].applicability='unknown';value.result.review_state='review_required';
 const producer={schema:'workflow-decision-review-producer-v285',workspace_id:id(40),task_id:value.task_id,job_id:id(41),activation_id:value.activation_id,node_id:'reasoning',invocation_id:value.invocation_id,actor_id:id(42),capability_id:id(43),...Object.fromEntries(['capability_hash','input_hash','output_hash','request_key_hash','capture_hash','frame_hash','sources_hash'].map(key=>[key,'b'.repeat(64)])),decision_input_hash:value.input_hash};
 const plan={schema:DECISION_PLAN_V285,route:'review_required',reason_code:'selection_needs_review',acquisition_required:false,no_gap:false,selected_candidate_ref:'candidate_2',missing_requirement_refs:['need_temperature'],conflict_refs:[],resense_context:null,resense_capability:null,producer,simulation_only:true,can_dispatch:false,permissions_verified:false,physical_state_verified:false,independent_acceptance:false};
 const task={id:value.task_id,graph_runtime:{cancel_epoch:0,activations:{[value.activation_id]:{id:value.activation_id,node_id:'reasoning',status:'completed',epoch:0,output:value.result},planner:{id:'planner',node_id:'planner',status:'completed',epoch:0,output:plan}}}};
 return {value,task,plan,human:{node_id:'review_decision'}};
}

test('extra reviewer follows the explicit original plan producer and does not choose equal results',()=>{
 const s=humanCase();const bound=reviewDecisionBinding(s.task,s.human);assert.equal(bound.activationId,s.value.activation_id);assert.equal(bound.result,s.value.result);
 assert.equal(decisionPlanView(bound.plan,checked(s.value)).route,'原计划要求人工核对');
 s.plan.producer.activation_id='not_original';assert.equal(reviewDecisionBinding(s.task,s.human),null);
});

for(const damage of ['another_task','another_invocation','paid_hash','authority','ambiguous_plan','ordinary_human'])test('reviewer context cannot imply a substituted source or right: '+damage,()=>{
 const s=humanCase();
 if(damage==='another_task')s.plan.producer.task_id=id(99);else if(damage==='another_invocation')s.plan.producer.invocation_id=id(99);else if(damage==='paid_hash')s.plan.producer.decision_input_hash='0'.repeat(64);else if(damage==='authority')s.plan.can_dispatch=true;else if(damage==='ambiguous_plan')s.task.graph_runtime.activations.other_planner=clone(s.task.graph_runtime.activations.planner);else s.human.node_id='ordinary_human';
 if(damage==='another_invocation')assert.throws(()=>decisionPlanView(s.plan,s.value));else assert.equal(reviewDecisionBinding(s.task,s.human),null);
});

test('backend JSON scalar spellings are used only for numeric display',()=>{
 for(const [value,wire,display]of [['原文','"原文"','原文'],['','""','原记录为空文本'],[true,'true','是'],[false,'false','否'],[null,'null','原记录未提供值']]){
  const fact={fact_ref:'original_fact',value};assert.equal(factValueText({fact_values:[{fact_ref:fact.fact_ref,text:wire}]},fact),display);
 }
 assert.equal(factValueText({fact_values:[{fact_ref:'number',text:'9007199254740993'}]},{fact_ref:'number',value:9007199254740992}),'9007199254740993');
});

for(const route of ['acquire','continue','no_action','review_required'])test('strict plan summary explains '+route+' without producer pins or a new request',()=>{
 const s=humanCase(),plan=s.plan;plan.route=route;
 if(route==='acquire'){plan.reason_code='acquisition_ready';plan.acquisition_required=true;plan.resense_context={schema:'workflow-resense-context-v196',scope:{task_id:s.value.task_id},can_dispatch:false,permissions_verified:false};plan.resense_capability={capability_id:id(60),capability_hash:'c'.repeat(64),release_version:'2.0.0'};}
 if(route==='continue'){plan.reason_code='current_facts_ready';plan.missing_requirement_refs=[];plan.no_gap=true;}
 if(route==='no_action'){plan.reason_code='no_recommendation';plan.missing_requirement_refs=[];plan.no_gap=true;plan.selected_candidate_ref=null;}
 assert.equal(isDecisionPlanV285(plan),true);const view=decisionPlanSummaryView(plan,{taskId:s.value.task_id});
 assert.equal(view.simulation,true);assert.ok(view.wait&&view.reason&&view.route);assert.ok(!JSON.stringify(view).includes(id(41))&&!JSON.stringify(view).includes(plan.producer.capture_hash));
 assert.ok(!Object.hasOwn(view,'producer')&&!Object.hasOwn(view,'resense_context'));
});

for(const damage of ['old_schema','extra_field','authority','wrong_task','false_no_gap','nonacquire_context','wrong_reason','missing_producer','bad_producer_hash'])test('plan summary rejects forged/unknown '+damage,()=>{
 const s=humanCase(),plan=s.plan;
 if(damage==='old_schema')plan.schema='workflow-decision-plan-v277';else if(damage==='extra_field')plan.extra='unexpected';else if(damage==='authority')plan.can_dispatch=true;else if(damage==='wrong_task')plan.producer.task_id=id(90);else if(damage==='false_no_gap')plan.no_gap=true;else if(damage==='nonacquire_context')plan.resense_context={schema:'unexpected'};else if(damage==='wrong_reason')plan.reason_code='acquisition_ready';else if(damage==='missing_producer')delete plan.producer.actor_id;else plan.producer.capture_hash='not-original';
 assert.throws(()=>decisionPlanSummaryView(plan,{taskId:s.value.task_id}));
});
