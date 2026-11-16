import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultNativeGoal,buildNativeGoal,taskGoalMetadata,chinaDeadline,nativeGoalToolMetadata} from '../src/native-goal-contract.mjs';

const catalog=[{key:'tree',fields:[{key:'name',label:'名称',kind:'文本'},{key:'count',label:'数量',kind:'整数'}]},{key:'camera',fields:[{key:'online',label:'在线',kind:'是否'}]}];
const metadata=taskGoalMetadata(catalog,[{id:'object-a',entity_type:'tree'}]);
const form=()=>({...defaultNativeGoal('核查对象资料并完成业务'),fields:['name']});

test('new task goal uses selected domain fields and preserves explicit manual acceptance',()=>{
 assert.deepEqual(Object.keys(metadata.fields),['name','count']);
 const spec=buildNativeGoal(form(),metadata);assert.equal(spec.observation_mode,'existing_only');assert.equal(spec.evidence.time.timezone,'Asia/Shanghai');
 assert.deepEqual(spec.entity_ids,['object-a']);assert.equal(spec.criteria[1].kind,'human_confirmation');assert.equal(spec.max_tool_calls,12);
});
test('a changed object selection cannot silently reuse fields from another domain object',()=>{
 const changed=taskGoalMetadata(catalog,[{id:'object-b',entity_type:'camera'}]);
 assert.throws(()=>buildNativeGoal(form(),changed),/所选对象/);
 assert.throws(()=>buildNativeGoal({...form(),targetField:'count',targetValue:'1'},metadata),/目标所需字段/);
});
test('field criteria retain zero but reject fractional integers and nonfinite values',()=>{
 const base={...form(),fields:['count'],targetField:'count',targetValue:'0',targetOperator:'gte'};
 assert.equal(buildNativeGoal(base,metadata).criteria[2].expected,0);
 for(const value of ['1.5','Infinity','abc',''])assert.throws(()=>buildNativeGoal({...base,targetValue:value},metadata));
});
test('new observations require bounded freshness and separately permit authorized requests only',()=>{
 const base={...form(),source:'observations'};let spec=buildNativeGoal(base,metadata);assert.equal(spec.evidence.minimum_verification,'confirmed');assert.equal(spec.observation_mode,'existing_only');
 spec=buildNativeGoal({...base,allowNew:true,hours:2},metadata);assert.equal(spec.evidence.time.max_age_seconds,7200);assert.equal(spec.observation_mode,'allow_new');
 for(const hours of [0,1.5,721])assert.throws(()=>buildNativeGoal({...base,hours},metadata),/整数小时/);
});
test('non numeric business fields cannot acquire numeric comparison semantics',()=>{
 assert.throws(()=>buildNativeGoal({...form(),targetField:'name',targetValue:'名称',targetOperator:'gt'},metadata),/等于/);
 const booleans=taskGoalMetadata(catalog,[{id:'object-c',entity_type:'camera'}]);
 assert.equal(buildNativeGoal({...form(),fields:['online'],targetField:'online',targetValue:'false'},booleans).criteria[2].expected,false);
});

test('deadline is China business time regardless of browser system timezone',()=>{
 assert.equal(chinaDeadline('2026-10-10T23:45'),'2026-10-10T15:45:00.000Z');
 assert.equal(buildNativeGoal({...form(),deadline:'2026-10-10T23:45'},metadata).deadline,'2026-10-10T15:45:00.000Z');
 assert.equal(buildNativeGoal(form(),metadata).deadline,null);
});
test('deadline rejects normalized nonexistent dates and incomplete time input',()=>{
 for(const value of ['2026-02-30T09:00','2026-13-01T09:00','2026-10-10T25:00','2026-10-10','2026-10-10T09:00Z'])assert.throws(()=>chinaDeadline(value));
});

const tool={id:'registered-calculation',name:'登记资料计算',version:3,content_hash:'fixed-hash',step_names:['核对资料'],sources:['planning_tool'],output_fields:[
 {key:'total',label:'合计',kind:'number',unit:'项'},
 {key:'count',label:'数量',kind:'integer',unit:null},
 {key:'checked',label:'已核对',kind:'boolean',unit:null},
 {key:'summary',label:'说明',kind:'string',unit:null},
]};
const withTools=(options={goal_tools:{tools:[tool]}})=>taskGoalMetadata(catalog,[{id:'object-a',entity_type:'tree'}],options);
const toolRow=(patch={})=>({id:'local_row',capabilityId:tool.id,kind:'tool_value',field:'total',operator:'gte',expected:'0',...patch});
const toolForm=(row=toolRow())=>({...form(),toolCriteria:[row]});
const criterion=(row,meta=withTools())=>buildNativeGoal(toolForm(row),meta).criteria.at(-1);

test('tool criteria are optional for new goals and older missing drafts, with independent defaults',()=>{
 assert.deepEqual(defaultNativeGoal().toolCriteria,[]);
 const first=defaultNativeGoal();first.toolCriteria.push(toolRow());assert.deepEqual(defaultNativeGoal().toolCriteria,[]);
 const old=form();delete old.toolCriteria;
 assert.deepEqual(buildNativeGoal(old,metadata),buildNativeGoal(form(),metadata));
 assert.equal(buildNativeGoal(form(),withTools({goal_tools_error:'暂不可用'})).criteria.length,2);
});

test('task metadata composes registered tools from an array or API envelope without losing entity fields',()=>{
 for(const options of [[tool],{tools:[tool]},{goal_tools:[tool]},{goal_tools:{schema:'native-goal-tools-v1',tools:[tool]}}]){
  const result=withTools(options);assert.deepEqual(result.tools,[tool]);assert.equal(result.goal_tools_error,null);
  assert.deepEqual(result.entity_ids,['object-a']);assert.equal(result.fields.count,'数量');assert.equal(result.field_kinds.count,'integer');
 }
 assert.deepEqual(nativeGoalToolMetadata({goal_tools:{tools:[tool],error:'版本待核对'}}),{tools:[tool],error:'版本待核对'});
 assert.equal(withTools({error:'工具读取失败'}).goal_tools_error,'工具读取失败');
 assert.equal(nativeGoalToolMetadata({fields:{},tools:[tool],error:'后台目标核查尚未完成'}).error,null);
 assert.equal(withTools([{...tool,version:'2026.10'}]).goal_tools_error,null);
});

test('tool success and output criteria link to the original subgoal and keep business field criteria',()=>{
 const original={...form(),fields:['count'],targetField:'count',targetValue:'0',toolCriteria:[toolRow({id:'success',kind:'capability_success'}),toolRow({id:'result'})]};
 const before=structuredClone(original);const spec=buildNativeGoal(original,withTools());
 assert.deepEqual(original,before);assert.deepEqual(spec.required_fields,['count']);
 assert.equal(spec.criteria[2].kind,'field_value');assert.equal(spec.criteria[2].expected,0);
 assert.deepEqual(spec.criteria[3],{id:'tool_1',kind:'capability_success',capability_id:tool.id,description:tool.name+'需成功完成并核对真实结果'});
 assert.equal(spec.criteria[4].kind,'tool_value');assert.equal(spec.criteria[4].expected,0);
 assert.deepEqual(spec.subgoals[0].criterion_ids,spec.criteria.map(item=>item.id));
 assert.equal(new Set(spec.subgoals[0].criterion_ids).size,spec.criteria.length);
});

test('metadata failure, stale capability and stale field preserve drafts and block tool submission',()=>{
 const original=toolForm();const before=structuredClone(original);
 for(const meta of [withTools({goal_tools_error:'读取失败'}),withTools({goal_tools:{tools:[tool],error:'版本变化'}}),withTools({goal_tools:{tools:'损坏'}}),withTools([])])assert.throws(()=>buildNativeGoal(original,meta),/工具/);
 assert.throws(()=>criterion(toolRow({field:'removed'})),/单值输出字段/);
 assert.deepEqual(original,before);
});

test('malformed persisted tool criteria and rows are never silently discarded',()=>{
 for(const malformed of [null,{},'broken',false,undefined])assert.throws(()=>buildNativeGoal({...form(),toolCriteria:malformed},withTools()),/草稿/);
 for(const malformed of [null,[],{},toolRow({id:''}),toolRow({kind:'field_value'})])assert.throws(()=>buildNativeGoal(toolForm(malformed),withTools()));
 assert.throws(()=>buildNativeGoal({...form(),toolCriteria:[toolRow(),toolRow()]},withTools()),/草稿/);
 for(const tools of [null,{},[tool,tool],[{...tool,output_fields:null}],[{...tool,step_names:'核对资料'}],[{...tool,output_fields:[{key:'all',label:'全部',kind:'object',unit:null}]}]]){
  const meta=withTools({goal_tools:{tools}});assert.match(meta.goal_tools_error,/格式/);assert.throws(()=>buildNativeGoal(toolForm(),meta),/工具清单/);
 }
});

test('registered tool metadata requires version, hash and labeled primitive fields before it can be used',()=>{
 const changes=[{version:null},{version:0},{version:true},{content_hash:null},{content_hash:''},{step_names:null},{step_names:['']},{sources:null},{sources:['unknown_source']},
  {output_fields:[{key:'total',label:null,kind:'number',unit:null}]},
  {output_fields:[{key:'total',label:'合计',kind:'number',unit:2}]},
  {output_fields:[{key:'total',label:'合计',kind:'number'}]},
  {output_fields:[tool.output_fields[0],tool.output_fields[0]]},
 ];
 for(const change of changes){const result=withTools([{...tool,...change}]);assert.match(result.goal_tools_error,/格式/);assert.throws(()=>buildNativeGoal(toolForm(),result),/工具清单/);}
});

test('numeric tool expectations reject coercion, nonfinite results and fractional integers',()=>{
 for(const value of [false,true,null,[],[1],{},'', ' ', 'Infinity','NaN','0x10','1abc'])assert.throws(()=>criterion(toolRow({expected:value})),/数值/);
 for(const value of [Infinity,-Infinity,NaN])assert.throws(()=>criterion(toolRow({expected:value})),/有限/);
 for(const value of ['1.5',1.5])assert.throws(()=>criterion(toolRow({field:'count',expected:value})),/整数/);
 for(const [input,expected] of [['0',0],[0,0],['-2.5',-2.5],['1e2',100],[' 3 ',3]])assert.equal(criterion(toolRow({expected:input})).expected,expected);
 assert.equal(criterion(toolRow({field:'count',expected:'2.0'})).expected,2);
 assert.throws(()=>criterion(toolRow({operator:'contains'})),/比较方式/);
});

test('boolean tool expectations keep false and strings keep literal content with equality only',()=>{
 for(const [input,expected] of [[true,true],[false,false],['true',true],['false',false]])assert.equal(criterion(toolRow({field:'checked',operator:'eq',expected:input})).expected,expected);
 for(const value of ['TRUE','0',0,1,{},[],null,''])assert.throws(()=>criterion(toolRow({field:'checked',operator:'eq',expected:value})),/是.*否/);
 assert.equal(criterion(toolRow({field:'summary',operator:'eq',expected:'  已核对  '})).expected,'  已核对  ');
 for(const value of [true,2,{},[],null,'',' '])assert.throws(()=>criterion(toolRow({field:'summary',operator:'eq',expected:value})),/内容/);
 for(const field of ['checked','summary'])for(const operator of ['gt','gte','lt','lte'])assert.throws(()=>criterion(toolRow({field,operator,expected:field==='checked'?false:'已核对'})),/等于/);
});

test('all original and tool criteria fit the twelve linked condition limit',()=>{
 const rows=count=>Array.from({length:count},(_,i)=>toolRow({id:'local_'+i,kind:'capability_success'}));
 const spec=buildNativeGoal({...form(),toolCriteria:rows(10)},withTools());assert.equal(spec.criteria.length,12);assert.equal(spec.subgoals[0].criterion_ids.length,12);
 assert.throws(()=>buildNativeGoal({...form(),toolCriteria:rows(11)},withTools()),/最多12/);
 const fieldForm={...form(),fields:['count'],targetField:'count',targetValue:'0'};
 assert.equal(buildNativeGoal({...fieldForm,toolCriteria:rows(9)},withTools()).criteria.length,12);
 assert.throws(()=>buildNativeGoal({...fieldForm,toolCriteria:rows(10)},withTools()),/最多12/);
});
