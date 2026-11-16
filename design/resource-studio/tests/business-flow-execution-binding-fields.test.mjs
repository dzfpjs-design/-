// Actual React DOM form events and pure helpers. No API, DB, model, source
// authority, durable server draft or adoption is exercised by this file.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {dirname,resolve,join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';
import * as fields from '../src/business-flow-execution-binding-fields.mjs';

const id=n=>'10000000-0000-4000-8000-'+n.toString(16).padStart(12,'0'),copy=fields.copy;
const source={business:{name:'合成范围',flows:[{flow_id:'scope_flow',name:'人工受理与分析',trigger:{kind:'manual',description:'操作员明确发起'},start_node:'receive',end_nodes:['finish'],nodes:[
 {node_id:'receive',name:'受理资料',actor:'human',role:'受理员',responsibility:'核对资料是否齐全',inputs:['任务说明'],outputs:['资料是否齐全']},
 {node_id:'analyze',name:'分析处理',actor:'business_agent',role:'业务核对',responsibility:'结合审核标志计算结论',inputs:['审核标志'],outputs:['核对结论']},
 {node_id:'finish',name:'保存核对单',actor:'human',role:'归档员',responsibility:'保存实际核对结果',inputs:['核对结果'],outputs:['归档记录']}],edges:[
 {kind:'condition',from_node:'receive',to_node:'analyze',condition:'资料齐全'},
 {kind:'condition',from_node:'receive',to_node:'finish',condition:'资料不齐全'},
 {kind:'sequence',from_node:'analyze',to_node:'finish'}],deliverables:[{name:'核对结论',description:'本次实际分析结论',produced_by:['analyze']}]}]},evidence:[{span_ref:'w:p1/s1',quote:'公开合成原文，不能当作真实授权。'}]};
const flow=source.business.flows[0],members=[{id:id(1),name:'实际操作员甲',role:'operator'},{id:id(2),name:'实际操作员乙',role:'operator'},{id:id(3),name:'只读成员',role:'viewer'}];
function ability(n,output='conclusion'){return {id:id(10+n),name:'登记核对能力'+n,status:'published',content_hash:String(n).repeat(64),release_version:'1.0.'+n,agents:['业务智能体'],adapter:'builtin',max_attempts:1,external:n===2,destination:n===2?'registered.example':null,input_schema:{type:'object',additionalProperties:false,required:['flag','threshold'],properties:{flag:{type:'boolean',title:'审核标志'},threshold:{type:'number',title:'判定阈值'}}},output_schema:{type:'object',additionalProperties:false,required:[output],properties:{[output]:{type:'string',title:'核对结论'}}}};}
const abilities=[ability(1),ability(2,'result')];

test('new binding is exactly the closed ten keys and never chooses an account, capability or condition',()=>{
 const value=fields.newBindings(flow);assert.deepEqual(Object.keys(value),['schema','input_schema','references','predicates','guards','nodes','conditions','parallel_groups','loops','goals']);
 assert.deepEqual(value.nodes,{receive:null,analyze:null,finish:null});assert.deepEqual(value.predicates,{});assert.deepEqual(value.conditions,[]);
 const initialized=fields.ensureNode(value,flow.nodes[1]);assert.equal(initialized.nodes.analyze.actor_id,'');assert.equal(initialized.nodes.analyze.execution.capability_id,'');assert.equal(initialized.nodes.analyze.execution.max_attempts,null);assert.equal(initialized.nodes.analyze.input_ports['审核标志'],'');
 assert.throws(()=>fields.ensureNode(value,{...flow.nodes[1],actor:'task_center'}),/尚无实际运行适配/);assert.deepEqual(value.nodes,{receive:null,analyze:null,finish:null});
});
test('capability changes retain original arguments, bindings, unknown fields and a full history while invalidating external consent',()=>{
 let value=fields.ensureNode(fields.newBindings(flow),flow.nodes[1]);value.retained={unknown:[false,0,null]};value.nodes.analyze.execution.arguments={threshold:1};value.nodes.analyze.execution.input_bindings={flag:'source_ref'};
 let result=fields.selectPublishedAbility(value,{retained_ui:'仍需保存'},flow.nodes[1],abilities[0]);result.value.nodes.analyze.execution.retained_unknown={original:true};const prior=copy(result.value.nodes.analyze.execution);
 result=fields.selectPublishedAbility(result.value,result.buffers,flow.nodes[1],abilities[1]);assert.deepEqual(result.value.nodes.analyze.execution.arguments,prior.arguments);assert.deepEqual(result.value.nodes.analyze.execution.input_bindings,prior.input_bindings);assert.deepEqual(result.value.nodes.analyze.execution.retained_unknown,{original:true});assert.deepEqual(result.buffers.previous_executions.analyze,[prior]);assert.equal(result.buffers.node_reviews.analyze.required,true);assert.deepEqual(result.buffers.clear_external_node_ids,['analyze']);assert.equal(result.buffers.retained_ui,'仍需保存');assert.deepEqual(result.value.retained,{unknown:[false,0,null]});
 assert.throws(()=>fields.selectPublishedAbility(value,{},flow.nodes[1],{...abilities[0],status:'revoked'}),/已发布能力/);
});
test('number text, integer/float spelling and shifted unfinished rows survive separate buffer round trips',()=>{
 let value=fields.newBindings(flow);value.loops=[{edge_index:2,max_iterations:null},{edge_index:0,max_iterations:null}];
 let result=fields.numericEdit(value,{retained:true},['loops',1,'max_iterations'],'-','integer');assert.equal(result.value.loops[1].max_iterations,null);assert.equal(result.buffers.number_texts['/loops/1/max_iterations'].text,'-');
 result=fields.removeBindingArrayItem(result.value,result.buffers,['loops'],0);assert.equal(result.buffers.number_texts['/loops/0/max_iterations'].text,'-');assert.equal(result.buffers.retained,true);
 value=fields.ensureNode(fields.newBindings(flow),flow.nodes[1]);result=fields.numericEdit(value,{},['nodes','analyze','execution','arguments','threshold'],'1.0');assert.match(fields.bindingRequestJSON(result.value,result.buffers),/"threshold":1\.0/);
 const decoded=JSON.parse(fields.bindingRequestJSON(result.value,result.buffers)),restored=fields.cloneBindings(decoded,JSON.parse(JSON.stringify(result.buffers)));assert.match(fields.bindingRequestJSON(restored,result.buffers),/"threshold":1\.0/);
 const changed=fields.patchBindings(restored,result.buffers,['nodes','analyze','execution','arguments','threshold'],1);assert.match(fields.bindingRequestJSON(changed.value,changed.buffers),/"threshold":1[},]/);assert.equal(changed.buffers.number_tokens['/nodes/analyze/execution/arguments/threshold'],undefined);assert.ok(changed.buffers.retained_number_edits.length>0);
});

let directory,Component;const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
test.before(async()=>{
 assert.equal(process.env.NODE_ENV,'development','run actual form events with NODE_ENV=development');directory=await mkdtemp(join(tmpdir(),'flow-execution-fields-'));
 const output=await build({root:project,configFile:false,envDir:directory,mode:'test',cacheDir:join(directory,'cache'),logLevel:'silent',build:{ssr:join(project,'src/BusinessFlowExecutionBindingFields.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
 const file=join(directory,'form.mjs');await writeFile(file,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);Component=(await import(pathToFileURL(file).href)).default;
});
test.after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});

test('actual source-oriented form binds accounts, ports and real fields without first-option defaults or paid/save/adopt actions',{timeout:30000,concurrency:false},async()=>{
 const network=[];class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('No external resources');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://execution-fields.invalid/',resources:new NoResources()}),container=dom.window.document.getElementById('root');
 const deny=url=>{network.push(String(url));throw Error('No transport in field authoring');},globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,fetch:deny,IS_REACT_ACT_ENVIRONMENT:true},previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 let state={value:{...fields.newBindings(flow),retained_unknown:{complete:true}},buffers:{retained_ui:{unfinished:'勿丢失'}}},root,disabled=false;const original=copy(source),changes=[];
 const buttons=(owner=container)=>[...owner.querySelectorAll('button')],button=(label,owner=container)=>{const found=buttons(owner).find(node=>node.textContent===label);assert.ok(found,'actual button: '+label);return found;};
 function draw(){root.render(React.createElement(Component,{sourceValue:source,flowId:flow.flow_id,...state,members,abilities,disabled,onChange:(value,metadata)=>{assert.deepEqual(Object.keys(metadata),['buffers']);state={value:copy(value),buffers:copy(metadata.buffers)};changes.push(copy(state));draw();}}));}
 async function click(label,owner=container){const target=button(label,owner);assert.equal(target.disabled,false,label);await act(async()=>{target.click();});}
 async function choose(label,value){const target=container.querySelector('select[aria-label="'+label+'"]');assert.ok(target,'actual select: '+label);assert.equal(target.disabled,false);assert.ok([...target.options].some(option=>option.value===value),'explicit available choice');await act(async()=>{target.value=value;target.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});}
 async function input(label,value){const target=container.querySelector('input[aria-label="'+label+'"]');assert.ok(target,'actual input: '+label);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(target,value);target.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});}
 try{
  const {createRoot}=await import('react-dom/client');root=createRoot(container);await act(async()=>{draw();});assert.equal(changes.length,0);assert.deepEqual(state.value.nodes,{receive:null,analyze:null,finish:null});
  await input('新增任务输入名称','本次任务说明');await choose('新增任务输入类型','string');await click('添加输入并登记引用');const taskRef=Object.entries(state.value.references).find(([,ref])=>ref.source==='input')[0];
  await click('受理资料');await click('明确设置此步骤的执行绑定');assert.equal(state.value.nodes.receive.actor_id,'');assert.equal(container.querySelector('select[aria-label="此步骤实际执行账号"]').value,'');assert.equal(container.textContent.includes('只读成员'),false);
  await choose('此步骤实际执行账号',members[0].id);await choose('原输入：任务说明',taskRef);await choose('人工产出类型：资料是否齐全','boolean');await click('为此原产出定义人工结果字段');
  const receiveField=state.value.nodes.receive.output_ports['资料是否齐全'];assert.equal(state.value.nodes.receive.output_schema.properties[receiveField].type,'boolean');
  await click('登记此实际结果供其他步骤和条件使用');const resultRef=Object.entries(state.value.references).find(([,ref])=>ref.source==='output'&&ref.node_id==='receive')[0];
  await click('分析处理');await click('明确设置此步骤的执行绑定');assert.equal(state.value.nodes.analyze.execution.capability_id,'');await choose('此步骤已发布能力',abilities[0].id);assert.equal(state.value.nodes.analyze.actor_id,'');await choose('此步骤实际执行账号',members[1].id);await choose('原输入：审核标志',resultRef);
  await choose('能力输入来源 flag','ref:'+resultRef);await choose('能力输入来源 threshold','literal');await input('能力参数 threshold','-');assert.equal(state.value.nodes.analyze.execution.arguments.threshold,null);assert.equal(state.buffers.number_texts['/nodes/analyze/execution/arguments/threshold'].text,'-');assert.ok(container.textContent.includes('有数值尚未填写完整'));
  // The enclosing draft owns persistence. Re-create props from a simulated
  // stored value+buffers round trip; the unfinished text must remain editable.
  state={value:JSON.parse(fields.bindingRequestJSON(state.value,state.buffers)),buffers:JSON.parse(JSON.stringify(state.buffers))};await act(async()=>{draw();});assert.equal(container.querySelector('input[aria-label="能力参数 threshold"]').value,'-');
  await input('能力参数 threshold','1.0');assert.match(fields.bindingRequestJSON(state.value,state.buffers),/"threshold":1\.0/);await choose('原产出：核对结论','conclusion');
  await choose('此步骤已发布能力',abilities[1].id);assert.equal(state.value.nodes.analyze.execution.input_bindings.flag,resultRef);assert.equal(state.value.nodes.analyze.execution.arguments.threshold,1);assert.equal(state.value.nodes.analyze.output_ports['核对结论'],'conclusion','old output mapping is retained for explicit review');assert.deepEqual(state.buffers.clear_external_node_ids,['analyze']);assert.equal(state.buffers.node_reviews.analyze.required,true);assert.ok(state.buffers.previous_executions.analyze.length===1);await choose('原产出：核对结论','result');
  await click('添加一个明确条件');const predicate=Object.keys(state.value.predicates)[0];assert.deepEqual(state.value.predicates[predicate],{op:'ref',key:''});assert.equal(container.textContent.includes('明确的是否常量'),false);await choose('条件引用字段',resultRef);
  await click('明确配对原条件成立与不成立连线');assert.equal(state.value.conditions[0].true_edge_index,null);assert.equal(state.value.conditions[0].false_edge_index,null);await choose('条件成立原连线 1','0');await choose('条件不成立原连线 1','1');await choose('这对分支使用的条件 1',predicate);assert.deepEqual(state.value.conditions[0],{true_edge_index:0,false_edge_index:1,predicate_id:predicate});
  await click('明确一条有次数上限的返回');await click('明确一条有次数上限的返回');await input('最多返回次数 2','-');
  const firstReturn=buttons().find(node=>node.textContent==='移除此返回声明');await act(async()=>{firstReturn.click();});assert.equal(container.querySelector('input[aria-label="最多返回次数 1"]').value,'-');assert.equal(state.buffers.number_texts['/loops/0/max_iterations'].text,'-','actual array deletion shifts the later unfinished buffer');
  await click('明确一个完成标准');assert.deepEqual(state.value.goals[0].required_nodes,[]);assert.deepEqual(state.value.goals[0].deliverable_indices,[]);
  assert.deepEqual(state.value.retained_unknown,{complete:true});assert.deepEqual(state.buffers.retained_ui,{unfinished:'勿丢失'});assert.deepEqual(source,original);
  assert.equal(buttons().some(node=>/^(保存草稿|保存绑定|采用本次安排|确认采用|启用流程)$/.test(node.textContent)),false,'field authoring has no server save, adoption or execution controls');assert.equal(container.querySelector('textarea'),null,'no raw JSON editor');
  const count=changes.length;disabled=true;await act(async()=>{draw();});assert.equal(button('添加一个明确条件').disabled,true);await act(async()=>{button('添加一个明确条件').click();});assert.equal(changes.length,count);assert.deepEqual(network,[]);
 }finally{if(root)await act(async()=>{root.unmount();});for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}dom.window.close();}
});
