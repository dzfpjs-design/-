import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('proposal evidence, separate outputs, unknowns and reference-only history render in business language',async()=>{
 const previous=globalThis.sessionStorage;globalThis.sessionStorage={getItem(){return null;}};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const {ProposalMeaning,SupportingQueryResults}=await server.ssrLoadModule('/src/NativeQueryProposals.jsx'),render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
  const catalog={objects:[{id:'hidden-object',label:'场景甲'}],fields:[{key:'hidden-title',label:'标题',type:'string',unit:null},{key:'hidden-amount',label:'实测温度',type:'number',unit:'℃'}],relation_types:[{id:'hidden-relation',title:'包含档案'}],query_tools:[{id:'hidden-keyword',method:'keyword'},{id:'hidden-mean',method:'aggregate'}]};
  const row={candidate:{summary:'筛热记录并核对均值',fields:['hidden-title','hidden-amount'],relation_query:{root_ids:['hidden-object'],relation_types:['hidden-relation'],direction:'outgoing',max_hops:1,include_roots:false},tool_steps:[{id:'first',capability_id:'hidden-keyword',input_from:'evidence',parameters:{field:'hidden-title',text:'热'}},{id:'last',capability_id:'hidden-mean',input_from:'first',parameters:{field:'hidden-amount',operation:'mean'}}],result_criterion:{field:'value',operator:'eq',expected:20},questions:['数量是证据记录数还是现场对象数？'],claims:[],limitations:['计数单位为证据记录，不是现场对象数。']},claim_spans:[{kind:'operation',key:'last',quote:'算均值并核对20',start:10}],constraints:{evidence_requirement:{source:'archive',time:{mode:'archive'},minimum_verification:'recorded'},limitations:['原话引用只证明出处，仍需人工核对含义。']}};
  const html=render(ProposalMeaning,{row,catalog});for(const text of ['场景甲','包含档案','标题','实测温度','平均值','第1步返回的资料','20','℃','数量是证据记录数还是现场对象数','原话引用只证明出处','算均值并核对20'])assert.ok(html.includes(text),text);
  for(const token of ['hidden-object','hidden-relation','hidden-keyword','hidden-amount','&quot;tool_steps&quot;'])assert.equal(html.includes(token),false,token);
  const result=render(SupportingQueryResults,{catalog,view:{items:[{id:'hidden-run',status:'sufficient',reference_only:true,current_valid:false,scope_error:'原来源已更正',result:{evidence:[{entity_id:'hidden-object',version:2,attributes:{'hidden-title':'热记录原文','hidden-amount':20},observed_at:'2026-10-14T00:00:00Z'}],tool_trace:[{status:'succeeded',title:'真实本机均值',inputs:{records:[{},{}]},output:{records:[{}]}}]},result_summary:{fields:['hidden-title','hidden-amount'],rows:[{values:{value:20,sample_count:2,field:'hidden-amount',operation:'mean'}}],record_count:1,limitations:['证据记录数2，不是现场去重对象数。']}}]}});
  for(const text of ['只读参考结果','原目标','正式验收','当前依据未通过','原来源已更正','热记录原文','真实本机均值','平均值','实测温度','20 ℃','证据记录数2'])assert.ok(result.includes(text),text);
  for(const token of ['hidden-run','hidden-amount','&quot;values&quot;'])assert.equal(result.includes(token),false,token);
  const empty=render(SupportingQueryResults,{catalog,view:{items:[{id:'empty-filter',status:'insufficient',reference_only:true,current_valid:true,created_at:'2026-10-14T00:00:00Z',completed_at:'2026-10-14T00:01:00Z',result:{evidence:[{entity_id:'hidden-object',version:2,attributes:{'hidden-title':'原资料仍保留，但不属于筛选结果'}}],tool_trace:[{status:'succeeded',inputs:{records:[{}]},output:{records:[]}}]},result_summary:{fields:[],rows:[],record_count:0,limitations:[]}}]}});
  for(const text of ['资料不足，未形成查询答案','本次返回0条结果','本次筛选范围内没有返回匹配记录','请核对关键词','核对本次完整原来源'])assert.ok(empty.includes(text),text);
  assert.equal(empty.includes('查询状态待核实'),false);assert.equal(empty.includes('<dt>标题</dt><dd>原资料仍保留'),true);assert.equal(empty.includes('本次返回1条结果'),false);
  for(const [status,label] of [['needs_clarification','查询条件待补充'],['needs_review','结果待核对']]){const pending=render(SupportingQueryResults,{catalog,view:{items:[{id:status,status,reference_only:true,result_summary:{fields:[],rows:[],record_count:0,limitations:[]}}]}});assert.ok(pending.includes(label));assert.equal(pending.includes('查询状态待核实'),false);}
 }finally{await server.close();globalThis.sessionStorage=previous;}
});

const traceCatalog={fields:[{key:'hidden-mass',label:'实测质量',type:'number',unit:'kg'}]};
function savedTraceView(){
 const row=(value,operation)=>({values:{value,field:'hidden-mass',operation,sample_count:2},entity_ids:['hidden-object'],evidence_refs:['hidden-evidence']});
 return {items:[{id:'hidden-run',status:'sufficient',reference_only:true,current_valid:true,
  result:{tool_trace:[
   {status:'succeeded',title:'计算质量均值',inputs:{field:'hidden-mass',operation:'mean',records:[{values:{'hidden-mass':3}},{values:{'hidden-mass':7}}]},output:{records:[row(5,'mean')]}},
   {status:'succeeded',title:'统计原筛选证据记录',inputs:{field:'hidden-mass',operation:'count',records:[{},{}]},output:{records:[row(2,'count')]}}]},
  result_summary:{fields:[{key:'value',label:'计算结果',unit:'条证据记录'}],rows:[row(2,'count')],record_count:1,limitations:[]}}]};
}
async function renderSavedTrace(view,catalog=traceCatalog){
 const previous=globalThis.sessionStorage;globalThis.sessionStorage={getItem(){return null;}};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{const {SupportingQueryResults}=await server.ssrLoadModule('/src/NativeQueryProposals.jsx');return renderToStaticMarkup(React.createElement(SupportingQueryResults,{view,catalog}));}
 finally{await server.close();globalThis.sessionStorage=previous;}
}
test('saved H09 mean and final count are both readable with their own units and no internal identifiers',async()=>{
 const view=savedTraceView(),html=await renderSavedTrace(view);
 for(const text of ['第1步已保存输出','第2步已保存输出','平均值','5 kg','2 条证据记录','实测质量'])assert.ok(html.includes(text),text);
 for(const internal of ['hidden-mass','hidden-object','hidden-evidence','hidden-run','&quot;values&quot;'])assert.equal(html.includes(internal),false,internal);
 // The renderer must show the durable receipt, never recompute from its 3/7 inputs.
 view.items[0].result.tool_trace[0].output.records[0].values.value=6.25;
 const changed=await renderSavedTrace(view);assert.ok(changed.includes('6.25 kg'));assert.equal(changed.includes('<dd>5 kg</dd>'),false);
});
test('saved step numeric output with an unknown source unit is explicitly undeclared',async()=>{
 const html=await renderSavedTrace(savedTraceView(),{fields:[{key:'hidden-mass',label:'实测质量',type:'number',unit:null}]});
 assert.ok(html.includes('5（单位未声明，保持原值）'));assert.ok(html.includes('2 条证据记录'));assert.equal(html.includes('5 kg'),false);
});
test('failed steps preserve their errors and cannot present a stale output as a successful result',async()=>{
 const view=savedTraceView(),step=view.items[0].result.tool_trace[0];
 view.items[0].status='failed';view.items[0].result_summary={fields:[],rows:[],record_count:0,limitations:[]};
 step.status='failed';step.error='输入资料未通过核对，原回执保留';step.output.records[0].values.value=999;
 view.items[0].result.tool_trace=[step];
 const html=await renderSavedTrace(view);
 for(const text of ['本机查询未完成','输入资料未通过核对，原回执保留','本步骤尚无成功输出'])assert.ok(html.includes(text),text);
 assert.equal(html.includes('999'),false);assert.equal(html.includes('真实回执已保存'),false);
});
test('historical intermediate and final values remain readable while changed sources stay visibly invalid',async()=>{
 const view=savedTraceView();view.items[0].current_valid=false;view.items[0].scope_error='原来源已更正，请重新核对';
 const html=await renderSavedTrace(view);
 for(const text of ['当前依据未通过','原来源已更正，请重新核对','role="alert"','5 kg','2 条证据记录','只读参考结果'])assert.ok(html.includes(text),text);
});
