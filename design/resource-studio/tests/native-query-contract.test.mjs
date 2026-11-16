import test from 'node:test';
import assert from 'node:assert/strict';
import {buildNativeQuery,nativeQueryMetadata,queryInputFields,queryOutputFields} from '../src/native-query-contract.mjs';
import {defaultNativeGoal,buildNativeGoal,taskGoalMetadata} from '../src/native-goal-contract.mjs';
import {queryTools,queryMetadata as metadata,queryRow} from './native-query-fixtures.mjs';

const form=(extra={})=>({...defaultNativeGoal('核对原资料并完成业务'),fields:['name','count','checked'],querySteps:[queryRow()],...extra});
const condition=(extra={})=>({enabled:true,field:'value',operator:'gte',expected:'0',...extra});
test('native metadata contains only versioned local typed methods and preserves unavailable errors',()=>{
 assert.equal(nativeQueryMetadata(metadata).error,null);
 assert.deepEqual(nativeQueryMetadata({}),{tools:[],error:null});
 assert.equal(nativeQueryMetadata({...metadata,query_tools_error:'版本待核对'}).error,'版本待核对');
 for(const change of [{external:true},{content_hash:''},{version:0},{method:'sql'},{parameters_schema:null},{output_fields:[{key:'rows',label:'全部',kind:'object',unit:null}]},{output_fields:[{key:'value',label:'结果',kind:'number'}]}])assert.match(nativeQueryMetadata({query_tools:[{...queryTools[0],...change}]}).error,/格式|契约/);
 assert.match(nativeQueryMetadata({query_tools:[queryTools[0],queryTools[0]]}).error,/格式/);
 const bound=structuredClone(queryTools[0]);bound.parameters_schema.properties.records={type:'string'};assert.match(nativeQueryMetadata({query_tools:[bound]}).error,/目录/);
});
test('new and older direct goals work without query metadata and defaults do not share drafts',()=>{
 const first=defaultNativeGoal();first.querySteps.push(queryRow());first.queryCriterion.enabled=true;
 assert.deepEqual(defaultNativeGoal().querySteps,[]);assert.equal(defaultNativeGoal().queryCriterion.enabled,false);
 const legacy=form({querySteps:[]});delete legacy.querySteps;delete legacy.queryCriterion;
 assert.deepEqual(buildNativeQuery(legacy,{}),{dataQuery:null,criterion:null});
 assert.equal(Object.hasOwn(buildNativeGoal(legacy,metadata),'data_query'),false);
 assert.equal(buildNativeQuery(form({querySteps:[]}),{query_tools_error:'连接中'}).dataQuery,null);
});
test('goal includes the frozen request and typed final criterion in the original subgoal',()=>{
 const original=form({queryCriterion:condition()}),before=structuredClone(original),spec=buildNativeGoal(original,metadata);
 assert.deepEqual(original,before);assert.deepEqual(spec.data_query,{tool_steps:[{id:'first',capability_id:'aggregate-fixed',input_from:'evidence',parameters:{field:'count',operation:'sum'}}],max_tool_seconds:60,allow_external_tools:false});
 const criterion=spec.criteria.find(row=>row.kind==='query_value');assert.equal(criterion.expected,0);assert.equal(criterion.field,'value');assert.ok(spec.subgoals[0].criterion_ids.includes(criterion.id));assert.equal(spec.criteria[1].kind,'human_confirmation');assert.equal(spec.max_tool_calls,12);assert.equal(spec.max_steps,12);
});
test('original query drafts are not discarded when metadata, scope or parameters become unavailable',()=>{
 const original=form(),before=structuredClone(original);
 for(const meta of [{...metadata,query_tools:[]},{...metadata,query_tools:null},{...metadata,query_tools_error:'读取失败'}])assert.throws(()=>buildNativeGoal(original,meta),/查询/);
 for(const bad of [null,false,{},'corrupt',undefined])assert.throws(()=>buildNativeQuery(form({querySteps:bad}),metadata),/草稿/);
 for(const bad of [null,[],{},queryRow({id:'bad-id'}),queryRow({inputFrom:'future'}),queryRow({parameters:[]})])assert.throws(()=>buildNativeQuery(form({querySteps:[bad]}),metadata));
 assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow({parameters:{field:'count',operation:'sum',records:[]}})]}),metadata),/重置/);
 assert.deepEqual(original,before);
});
test('typed parameters reject coercion, unsupported operations and out of range fulltext settings',()=>{
 const full=extra=>queryRow({capabilityId:'fulltext-fixed',parameters:{field:'name',text:'杉木',top_k:'20',min_score:'0',match_mode:'all_terms',...extra}});
 const value=buildNativeQuery(form({querySteps:[full()]}),metadata).dataQuery.tool_steps[0].parameters;assert.equal(value.top_k,20);assert.equal(value.min_score,0);
 for(const extra of [{top_k:'1.5'},{top_k:'21'},{top_k:0},{top_k:true},{min_score:'Infinity'},{min_score:-1},{min_score:'0x10'},{match_mode:'semantic'},{text:''},{text:{sql:'SELECT'}}])assert.throws(()=>buildNativeQuery(form({querySteps:[full(extra)]}),metadata));
 for(const operation of ['median','SQL',false])assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow({parameters:{field:'count',operation}})]}),metadata));
});
test('count can measure nonmissing string or boolean evidence records but other aggregation needs numeric data',()=>{
 for(const field of ['count','name','checked'])assert.equal(buildNativeQuery(form({querySteps:[queryRow({parameters:{field,operation:'count'}})]}),metadata).dataQuery.tool_steps[0].parameters.operation,'count');
 for(const field of ['name','checked'])assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow({parameters:{field,operation:'sum'}})]}),metadata),/有限数值字段/);
 assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow({capabilityId:'keyword-fixed',parameters:{field:'checked',text:'true'}})]}),metadata),/文本字段/);
});
test('step dependencies expose only the actual chosen frame and preserve fields through filtering',()=>{
 const rows=[queryRow({capabilityId:'keyword-fixed',parameters:{field:'name',text:'杉'}}),queryRow({id:'second',inputFrom:'first'}),queryRow({id:'third',capabilityId:'keyword-fixed',inputFrom:'second',parameters:{field:'operation',text:'sum'}})];
 const value=form({querySteps:rows});assert.deepEqual(Object.keys(queryInputFields(value,metadata,1)),['name','count','checked']);assert.deepEqual(Object.keys(queryInputFields(value,metadata,2)),['value','sample_count','field','operation']);
 assert.equal(buildNativeQuery(value,metadata).dataQuery.tool_steps.length,3);
 assert.deepEqual(queryOutputFields(value,metadata,2).map(field=>field.key),['value','sample_count','field','operation']);
 value.queryCriterion=condition();assert.equal(buildNativeQuery(value,metadata).criterion.field,'value');
 value.querySteps[2].parameters.field='name';assert.throws(()=>buildNativeQuery(value,metadata),/当前输入步骤/);
 value.querySteps[2].parameters.field='operation';value.queryCriterion.field='name';assert.throws(()=>buildNativeQuery(value,metadata),/最后一步/);
});
test('removed, cyclic, forward and repeated step references remain blocked without remapping',()=>{
 for(const rows of [[queryRow(),queryRow()],[queryRow({inputFrom:'first'})],[queryRow({inputFrom:'second'}),queryRow({id:'second'})],[queryRow(),queryRow({id:'second',inputFrom:'removed'})]])assert.throws(()=>buildNativeQuery(form({querySteps:rows}),metadata),/依赖/);
 const seven=Array.from({length:7},(_,index)=>queryRow({id:'step_'+index}));assert.throws(()=>buildNativeQuery(form({querySteps:seven}),metadata),/最多6/);
});
test('preserved final outputs retain string and boolean values and numeric criteria reject coercion',()=>{
 const filtered=form({querySteps:[queryRow({capabilityId:'keyword-fixed',parameters:{field:'name',text:'杉'}})]});
 for(const [field,expected] of [['name','原杉木'],['checked',false],['count',0]]){
  const criterion=buildNativeQuery({...filtered,queryCriterion:condition({field,operator:'eq',expected})},metadata).criterion;assert.equal(criterion.expected,expected);
 }
 for(const [field,expected] of [['name','名称'],['checked',false]])assert.throws(()=>buildNativeQuery({...filtered,queryCriterion:condition({field,operator:'gt',expected})},metadata),/仅支持等于/);
 for(const expected of [[],{},null,true,'0x10','Infinity',''])assert.throws(()=>buildNativeQuery(form({queryCriterion:condition({expected})}),metadata));
 assert.throws(()=>buildNativeQuery(form({queryCriterion:condition({field:'sample_count',expected:'1.5'})}),metadata),/有限整数/);
 assert.throws(()=>buildNativeQuery(form({querySteps:[],queryCriterion:condition()}),metadata),/先设置查询/);
 for(const queryCriterion of [null,[],{},false])assert.throws(()=>buildNativeQuery(form({queryCriterion}),metadata),/草稿/);
});
test('final criteria stay within the shared twelve condition limit and field values are strictly typed',()=>{
 const tool={id:'business-tool',name:'原工具',version:1,content_hash:'fixed',step_names:[],sources:['planning_tool'],output_fields:[]};
 const rows=Array.from({length:9},(_,index)=>({id:'r'+index,kind:'capability_success',capabilityId:tool.id}));
 const meta={...metadata,tools:[tool]};assert.equal(buildNativeGoal(form({queryCriterion:condition(),toolCriteria:rows}),meta).criteria.length,12);
 assert.throws(()=>buildNativeGoal(form({queryCriterion:condition(),toolCriteria:[...rows,{...rows[0],id:'extra'}]}),meta),/最多12/);
 for(const targetValue of [false,[],{},'0x10','Infinity','1.5'])assert.throws(()=>buildNativeGoal(form({querySteps:[],targetField:'count',targetOperator:'eq',targetValue}),metadata));
 assert.throws(()=>buildNativeGoal(form({querySteps:[],targetField:'count',targetOperator:'contains',targetValue:'1'}),metadata),/比较方式/);
});
test('task creation metadata passes query tools, source kinds and explicit units without inventing defaults',()=>{
 const result=taskGoalMetadata([{key:'tree',fields:[{key:'count',label:'数量',kind:'整数',unit:'株'}]}],[{id:'object-a',entity_type:'tree'}],metadata);
 assert.deepEqual(result.query_tools,queryTools);assert.equal(result.query_tools_error,null);assert.equal(result.field_units.count,'株');
 assert.equal(taskGoalMetadata([],[],{query_tools_error:'版本未固定'}).query_tools_error,'版本未固定');
});
