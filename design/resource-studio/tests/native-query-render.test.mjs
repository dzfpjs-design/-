import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {defaultNativeGoal,buildNativeGoal} from '../src/native-goal-contract.mjs';
import {queryMetadata as metadata,queryRow} from './native-query-fixtures.mjs';

test('shared query controls and saved evidence remain human readable and truthful',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const {default:Editor,SavedQueryGoal,NativeQueryResult}=await server.ssrLoadModule('/src/NativeQueryEditor.jsx');
  const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
  const form={...defaultNativeGoal('核查档案'),fields:['name','count','checked'],querySteps:[queryRow({parameters:{field:'name',operation:'count'}})],queryCriterion:{enabled:true,field:'sample_count',operator:'gte',expected:'0'}};
  const html=render(Editor,{form,onChange:()=>{},metadata});
  for(const text of ['数据管理已随业务版本配置本机处理','按文字筛选','按内容查找并排序','计算登记值','输入资料','证据记录数','不是去重对象数','名称（文本）','已核查（是否）','派生结果完成字段','合计12项约束','关系查询暂不可用'])assert.ok(html.includes(text),text);
  for(const text of ['aggregate-fixed','object-a','records&quot;','SELECT','dataagent'])assert.equal(html.replace(/<[^>]*>/g,'').includes(text),false,text);
  const malformed=render(Editor,{form:{...form,querySteps:'damaged',queryCriterion:null},onChange:()=>{},metadata:{...metadata,query_tools_error:'当前业务版本待恢复'}});
  for(const text of ['原查询草稿保留','明确移除无法恢复的查询草稿','原派生结果标准无法恢复','明确移除无法恢复的派生标准'])assert.ok(malformed.includes(text),text);
  const unavailable=render(Editor,{form:{...form,querySteps:[queryRow({capabilityId:'old-secret-id'})]},onChange:()=>{},metadata:{...metadata,query_tools:[]}});
  assert.ok(unavailable.includes('原选方式不属于当前版本'));assert.equal(unavailable.includes('old-secret-id</option>'),false);
  const spec=buildNativeGoal(form,metadata),goal={...metadata,entity_names:{'object-a':'一号林木'},goal:spec};
  const saved=render(SavedQueryGoal,{goal});for(const text of ['已确认资料处理要求','证据记录数','原查询条件已随目标固定','原人工步骤'])assert.ok(saved.includes(text),text);
  const original={entity_id:'object-a',version:2,source:'resource_entity',attributes:{name:'原始林木名称',count:0,checked:false},observed_at:'2026-10-14T00:00:00Z'};
  const records=Array.from({length:30},(_,index)=>({values:{name:'真实输入第'+index+'项',count:index,checked:false},entity_ids:['object-a'],evidence_refs:['entity:object-a:2']}));
  const derived=[{values:{value:0,sample_count:30,field:'name',operation:'count'},entity_ids:['object-a'],evidence_refs:['entity:object-a:2']}];
  const result={status:'sufficient',evidence:[original],observation_evidence:[],derived_records:derived,derivation_verification:'deterministic_registered_computation',tool_trace:[{title:'真实本机计算',capability_id:'aggregate-fixed',status:'succeeded',inputs:{records,field:'name',operation:'count'},output:{records:derived,summary:{method:'registered_evidence_record_aggregate',sample_unit:'evidence_records'}}}]};
  const complete=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',version:3,status:'sufficient',progress:{stage:'completed'},result}}});
  for(const text of ['当前目标的本机资料查询','核对完整原来源记录（1）','原始林木名称','本步骤已保存真实输出','核对本步骤完整实际输入与来源','真实输入第29项','一号林木','版本 2','最后一步派生结果','证据记录','现场状态','不会自动发布可信经验'])assert.ok(complete.includes(text),text);
  for(const text of ['entity:object-a:2','aggregate-fixed','&quot;values&quot;','query-a'])assert.equal(complete.includes(text),false,text);
  const blocked=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',status:'sufficient',result},check:{current_gate_error:'原来源已改变，旧结果不可复用',evidence:{data_query:{id:'query-a',status:'blocked_evidence',reason:'来源版本改变'}}}}});
  assert.ok(blocked.includes('来源已变化或不满足要求'));assert.ok(blocked.includes('原来源已改变，旧结果不可复用'));assert.equal(blocked.includes('来源与计算已核对'),false);
  const invalidScope=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',status:'sufficient',current_valid:false,scope_error:'原能力已撤回',result}}});assert.ok(invalidScope.includes('以下是已保存历史，当前依据未通过'));assert.ok(invalidScope.includes('原能力已撤回'));assert.equal(invalidScope.includes('来源与计算已核对'),false);
  for(const status of ['queued','running']){
   // Minimal sanitized projection of the retained M62 queued GET-goal receipt.
   const pending=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',status,current_valid:false,historical:false,scope_error:null,progress:{stage:status},result:{},history:[]}}});
   assert.ok(pending.includes(status==='queued'?'等待本机查询':'本机查询处理中'));assert.ok(pending.includes('尚无已保存的最后一步查询结果'));assert.equal(pending.includes('来源已变化或不满足要求'),false);
  }
  // M61's retained accepted API receipt has this historical/current_valid pair
  // and a deterministic mean of 24 from two records. No private IDs or data copied.
  const acceptedResult={status:'sufficient',derivation_verification:'deterministic_registered_computation',derived_records:[{values:{field:'count',value:24,operation:'mean',sample_count:2}}]};
  const accepted=render(NativeQueryResult,{goal:{...goal,acceptance:{reason:'已验收均值'},data_query:{id:'query-a',status:'sufficient',current_valid:false,historical:true,scope_error:null,result:acceptedResult}}});
  for(const text of ['已验收目标的保存查询结果','最后一步派生结果','计算结果','>24<','平均值'])assert.ok(accepted.includes(text),text);
  assert.equal(accepted.includes('当前依据未通过'),false);assert.equal(accepted.includes('来源已变化或不满足要求'),false);
  const stopped=render(NativeQueryResult,{goal:{...goal,cancelled:{reason:'人工停止'},scope_error:'正式目标已停止',data_query:{id:'query-a',status:'cancelled',current_valid:false,historical:true,scope_error:null,result}}});
  assert.ok(stopped.includes('后续查询已停止'));assert.equal(stopped.includes('来源已变化或不满足要求'),false);
  const recovery=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',status:'running',current_valid:false,historical:false,worker:{state:'waiting_recovery',reason:'原执行租约已过期，等待核对恢复'},result:{}}}});
  assert.ok(recovery.includes('等待核对恢复'));assert.ok(recovery.includes('不领取后台任务或重发调用'));assert.equal(recovery.includes('本机查询处理中'),false);
  const unknown=render(NativeQueryResult,{goal:{...goal,data_query:{id:'query-a',status:'running',current_valid:false,historical:false,worker:{state:'unverified',reason:'后台归属暂时无法核实'},result:{}}}});
  assert.ok(unknown.includes('后台执行状态待核实'));assert.equal(unknown.includes('本机查询处理中'),false);
 }finally{await server.close();}
});
