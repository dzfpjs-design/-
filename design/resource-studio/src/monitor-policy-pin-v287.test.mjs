// Pure helper/draft and explicit GET spies; not React, IAM or live API proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {monitorPolicyPinInputs,monitorPolicyDraft,monitorPolicyDraftValue,monitorPolicyScopeKey,
 checkedMonitorPolicyOptions,checkedMonitorPolicySelection,loadMonitorPolicyOptions,monitorPolicyOptionLabel} from './monitor-policy-pin-v287.mjs';
import {taskInputForm,createTaskInputDraft,checkedTaskInputDraft,editTaskInput,buildTaskGraphInputs,
 taskInputHistoryLabel,persistTaskCreation,readTaskCreation,taskCreationJSON} from './graph-task-inputs.mjs';
import {typedRequestJSON} from './typed-numbers-v132.mjs';

const id=n=>'28700000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>JSON.parse(JSON.stringify(v));
const scope={actor_id:id(1),workspace_id:id(2),instance_id:id(3),release_id:id(4),business_id:'weather',configuration_hash:'a'.repeat(64)};
const entities=[id(5)],role='operator',pin={policy_id:id(6),approved_hash:'b'.repeat(64)},now=Date.parse('2026-11-01T12:00:00Z');
const port={type:['object','null'],properties:{policy_id:{type:'string',pattern:'^[0-9a-f-]{36}$'},
 approved_hash:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['policy_id','approved_hash'],additionalProperties:false};
const response=()=>({schema:'monitor-policy-options-v287',instance_id:scope.instance_id,release_id:scope.release_id,
 entity_ids:[...entities],options:[{...pin,title:'温湿度技术观测',fields:[{key:'temperature_c',label:'温度'}],
  valid_until:'2099-11-01T12:00:00Z',scope_label:'本次已选对象'}],can_dispatch:false});
const record=()=>({scope_key:monitorPolicyScopeKey(scope,entities,role),value:response()});
const schema=()=>({type:'object',properties:{policy_pin:copy(port),count:{type:'integer'},factor:{type:'number'},
 analysis_question:{type:'string'}},required:['policy_pin','count','factor','analysis_question'],additionalProperties:false});
const metadata=()=>({schema:'workflow-graph-task-inputs-v1',mode:'graph',instance_id:scope.instance_id,
 release_id:scope.release_id,business_id:scope.business_id,configuration_hash:scope.configuration_hash,
 hash:'c'.repeat(64),graph_hash:'d'.repeat(64),binding_hash:'e'.repeat(64),execution_granted:false,
 entity_fields:[],system_fields:[],input_schema:schema(),user_input_schema:schema()});
const options=()=>({typedNumbers:true,monitorPolicyRole:role,monitorPolicyOptions:{policy_pin:record()},now});
function filled(meta=metadata()){
 let draft=createTaskInputDraft(meta,scope,{typedNumbers:true});
 for(const [key,value] of Object.entries({policy_pin:monitorPolicyDraft(pin),count:{mode:'value',raw:'9007199254740991'},
  factor:{mode:'value',raw:'1.0'},analysis_question:{mode:'value',raw:'按当前资料决定是否申请温度观测'}}))
  draft=editTaskInput(draft,meta,scope,key,value,{typedNumbers:true});
 return draft;
}

test('only the exact original policy port is recognized without changing its schema',()=>{
 const original=schema(),before=copy(original),fields=monitorPolicyPinInputs(original);
 assert.equal(fields.length,1);assert.equal(fields[0].nullable,true);assert.deepEqual(original,before);
 const wrapped=copy(original),objectPort={...copy(port),type:'object'};
 wrapped.properties.policy_pin={anyOf:[objectPort,{type:'null'}]};assert.equal(monitorPolicyPinInputs(wrapped)[0].nullable,true);
 for(const damage of [v=>v.properties.policy_pin.properties.extra={type:'string'},
  v=>v.properties.policy_pin.properties.approved_hash.pattern='.*',
  v=>v.properties.policy_pin.required.pop(),v=>v.properties.policy_pin.additionalProperties=true]){
  const broken=copy(original);damage(broken);assert.throws(()=>monitorPolicyPinInputs(broken));
 }
});

test('task form uses a typed selector and preserves scalar lossless values',()=>{
 const meta=metadata(),before=copy(meta),draft=filled(meta),form=taskInputForm(meta,scope,{typedNumbers:true});
 assert.deepEqual(form.policyInputs.map(row=>row.key),['policy_pin']);assert.equal(form.fields.some(row=>row.key==='policy_pin'),false);
 const inputs=buildTaskGraphInputs(meta,scope,draft,entities,options()),wire=typedRequestJSON(inputs);
 assert.deepEqual(inputs.policy_pin,pin);assert.match(wire,/"count":9007199254740991/);assert.match(wire,/"factor":1\.0/);
 assert.deepEqual(meta,before);assert.deepEqual(checkedTaskInputDraft(draft,meta,scope,{typedNumbers:true}),draft);
 assert.doesNotMatch(taskInputHistoryLabel(meta,'policy_pin',draft.values.policy_pin),/\{|approved_hash|28700000|bbbbbbbb/);
});

test('nullable is an explicit choice and never a default or first option',()=>{
 const meta=metadata(),draft=createTaskInputDraft(meta,scope,{typedNumbers:true});
 assert.deepEqual(draft.values.policy_pin,{mode:'unset',raw:''});
 assert.throws(()=>monitorPolicyDraftValue(draft.values.policy_pin,{nullable:true,required:true}));
 const chosen=editTaskInput(filled(meta),meta,scope,'policy_pin',monitorPolicyDraft(null),{typedNumbers:true});
 assert.equal(buildTaskGraphInputs(meta,scope,chosen,entities,{typedNumbers:true}).policy_pin,null);
 assert.equal(taskInputHistoryLabel(meta,'policy_pin',chosen.values.policy_pin),'不自动补充观测');
});

test('entity pickers and post-draft document SourceSet ports keep their original paths',()=>{
 const meta=metadata(),source={type:'object',properties:{source_set_id:{type:'string',pattern:'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},
  source_set_hash:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['source_set_id','source_set_hash'],
  additionalProperties:false,'x-runtime-document-source-set-v207':true};
 meta.entity_fields=[{field:'target',label:'关联对象',required:true,waits:['原对象步骤']}];
 for(const root of [meta.input_schema,meta.user_input_schema]){
  root.properties.target={type:'string',enum:entities};root.properties.selected_sources=copy(source);
  root.required.push('target','selected_sources');
 }
 const before=copy(meta),model=taskInputForm(meta,scope,{typedNumbers:true});
 assert.deepEqual(model.sourceInputs.map(row=>row.key),['selected_sources']);assert.equal(model.entities[0].field,'target');
 let draft=filled(meta);draft=editTaskInput(draft,meta,scope,'target',{mode:'value',raw:entities[0]},{typedNumbers:true});
 const values=buildTaskGraphInputs(meta,scope,draft,entities,options());assert.equal(values.target,entities[0]);
 assert.equal(Object.hasOwn(values,'selected_sources'),false);assert.equal(Object.hasOwn(draft.values,'selected_sources'),false);
 assert.deepEqual(meta,before);assert.throws(()=>buildTaskGraphInputs(meta,scope,draft,[id(9)],options()));
});

test('bad or extra pin fields cannot enter the draft or serializer',()=>{
 for(const value of [{...pin,can_dispatch:true},{...pin,policy_id:'not-an-id'},{...pin,approved_hash:'bad'},
  {policy_id:pin.policy_id},[pin]])assert.throws(()=>monitorPolicyDraft(value));
 const meta=metadata(),draft=filled(meta),damaged=copy(draft);damaged.values.policy_pin.raw=JSON.stringify({...pin,permissions_verified:true});
 assert.throws(()=>checkedTaskInputDraft(damaged,meta,scope,{typedNumbers:true}));assert.deepEqual(draft,filled(meta));
});

test('loading, missing, expired and changed option hashes preserve but block the original pin',()=>{
 const value=monitorPolicyDraft(pin),before=copy(value),field=monitorPolicyPinInputs(schema())[0];
 for(const saved of [null,{scope_key:record().scope_key,value:null},
  {...record(),value:{...response(),options:[]}},
  {...record(),value:{...response(),options:[{...response().options[0],approved_hash:'f'.repeat(64)}]}},
  {...record(),value:{...response(),options:[{...response().options[0],valid_until:'2025-01-01T00:00:00Z'}]}}])
  assert.throws(()=>checkedMonitorPolicySelection(value,field,scope,entities,role,saved,{now}));
 assert.deepEqual(value,before);assert.deepEqual(checkedMonitorPolicySelection(value,field,scope,entities,role,record(),{now}),pin);
 assert.throws(()=>checkedMonitorPolicySelection(value,field,scope,[id(7)],role,record(),{now}));
 assert.throws(()=>checkedMonitorPolicySelection(value,field,{...scope,actor_id:id(8)},entities,role,record(),{now}));
 assert.throws(()=>checkedMonitorPolicySelection(value,field,scope,entities,'admin',record(),{now}));
});

test('returned scope, exact response metadata and malformed roles are checked',()=>{
 const good=response();assert.deepEqual(checkedMonitorPolicyOptions(good,scope,entities,role),good);
 for(const damage of [v=>v.can_dispatch=true,v=>v.entity_ids.push(id(7)),v=>v.release_id=id(9),v=>v.read_only=true,
  v=>v.options[0].approved_hash='bad',v=>v.options[0].fields[0].label=null,v=>v.options[0].role='admin']){
  const bad=response();damage(bad);assert.throws(()=>checkedMonitorPolicyOptions(bad,scope,entities,role));
 }
 for(const badRole of [undefined,null,{},'owner','ADMIN'])assert.throws(()=>monitorPolicyScopeKey(scope,entities,badRole));
 const label=monitorPolicyOptionLabel(response().options[0]);assert.match(label,/温湿度技术观测/);assert.match(label,/温度/);
 assert.doesNotMatch(label,/28700000|approved_hash|bbbbbbbb/);
});

test('only scoped GET is issued; invalid scope, aborted and superseded replies never apply',async()=>{
 const calls=[],api=async(path,options)=>{calls.push({path,options});return response();};
 await assert.rejects(loadMonitorPolicyOptions(api,scope,[],role));assert.equal(calls.length,0);
 await assert.rejects(loadMonitorPolicyOptions(api,scope,entities,{}));assert.equal(calls.length,0);
 const controller=new AbortController();controller.abort();await assert.rejects(loadMonitorPolicyOptions(api,scope,entities,role,{signal:controller.signal}));assert.equal(calls.length,0);
 assert.deepEqual(await loadMonitorPolicyOptions(api,scope,entities,role),response());assert.equal(calls[0].options.method,'GET');
 assert.match(calls[0].path,/monitor-policy-options-v287\?release_id=/);assert.match(calls[0].path,/entity_ids=/);
 let finish,current=true;const pending=loadMonitorPolicyOptions(()=>new Promise(resolve=>{finish=resolve;}),scope,entities,role,{isCurrent:()=>current});
 current=false;finish(response());await assert.rejects(pending);
});

test('policy/scalar creation outbox roundtrips and old scope never migrates',()=>{
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
 const meta=metadata(),draft=filled(meta),graph_inputs=buildTaskGraphInputs(meta,scope,draft,entities,options());
 const payload={request_id:'policy_roundtrip_287',release_id:scope.release_id,business_id:scope.business_id,graph_inputs};
 const form={graphInputDraft:draft,selectedObjects:entities,creationAction:null},exact=persistTaskCreation(storage,'forms',form,payload,{typedNumbers:true});
 const restored=readTaskCreation(storage,'forms');assert.equal(taskCreationJSON(restored.payload),exact);
 assert.deepEqual(restored.form.graphInputDraft,draft);assert.deepEqual(restored.payload.graph_inputs.policy_pin,pin);
 assert.throws(()=>checkedTaskInputDraft(draft,meta,{...scope,release_id:id(10)},{typedNumbers:true}));
 assert.deepEqual(restored.form.graphInputDraft.scope,draft.scope);
 const legacy={...meta,mode:'legacy',entity_fields:[],graph_hash:null,binding_hash:null,user_input_schema:null};
 assert.deepEqual(buildTaskGraphInputs(legacy,scope,null,entities),{});
});

// The selector must not weaken the established bounded integer contract.
test('policy selection preserves rejection of an out-of-range integer',()=>{
 const meta=metadata(),draft=editTaskInput(filled(meta),meta,scope,'count',{mode:'value',raw:'9007199254740993'},{typedNumbers:true});
 assert.throws(()=>buildTaskGraphInputs(meta,scope,draft,entities,options()),/整数超出/);
});
