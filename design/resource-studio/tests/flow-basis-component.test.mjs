// Real component handlers + in-memory compilation only. No listener or browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'vite';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';
import {flowConfirmed,flowProblems} from '../src/setup-schema.js';

const front=resolve(fileURLToPath(new URL('..',import.meta.url)));
const nodes=(value,out=[])=>{if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value&&typeof value==='object'&&value.props){out.push(value);nodes(value.props.children,out);}return out;};
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
const cite=(id,quote)=>({material_id:id,block_id:'位置-'+id,quote});
const account={workspace_id:'synthetic-basis',username:'operator',role:'operator'};
function workspace(){return {instanceProfile:{name:'核查',domain:'核查',goal:'核对资料'},businesses:['核查业务'],materials:['A','B','C'].map(id=>({id,name:'资料'+id+'.txt',hash:'hash-'+id})),understandingRecords:[{id:'paid-answer',raw:'原付费回答必须保留'}],flows:{核查业务:{trigger:'人工',output:'核查结论',citations:[cite('C','业务整体原文-C')],inferenceReason:'业务模型解释-C',steps:[{id:'first',name:'核查',purpose:'核对原件',agent:'人工确认',role:'负责人',output:'记录',sourceId:'A',citation:'修改前依据-A',citations:[cite('A','原文-A')],inferenceReason:'步骤模型解释-A',nextId:'end'},{id:'second',name:'归档',agent:'人工确认',role:'负责人',output:'归档记录',sourceId:'C',citation:'独立依据-C',citations:[cite('C','独立原文-C')],inferenceReason:'独立解释-C',nextId:'end'}]}}};}

async function component(directory){
 const output=await build({root:front,configFile:false,cacheDir:join(directory,'cache'),logLevel:'silent',build:{ssr:join(front,'src/SetupStudio.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(front,'node_modules/react/index.js')).href:id}}}});
 const path=join(directory,'SetupStudio.mjs');await writeFile(path,output.output.find(value=>value.type==='chunk'&&value.isEntry).code);return (await import(pathToFileURL(path))).default;
}

test('confirmation page exposes business/step evidence, retires stale refs before confirmation, and reloads manual/history separately',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'flow-basis-component-')),previous=Object.fromEntries(['fetch','location','history','window','localStorage','sessionStorage'].map(key=>[key,globalThis[key]]));let ui;
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,fetch:async()=>{throw Error('component regression forbids network');},location:{search:''},history:{replaceState(){}},window:{addEventListener(){},removeEventListener(){}}});
 try{
  const Setup=await component(directory),initial=workspace();let saved=initial;
  const props={runtime:true,account,step:1,workspace:initial,onCommit:value=>{saved=structuredClone(value);return true;},onSave:async()=>{},onGo(){},onRegisterGuard(){},Icon:'span',Btn:'button',Field:'label',Dialog:'section'};
  ui=mount(Setup,props);await ui.flush();
  let rendered=text(ui.render());for(const value of ['业务整体原文-C','业务模型解释-C','步骤模型解释-A','模型推断说明','不是原文证明'])assert.ok(rendered.includes(value),value);
  function edit(label,value){const field=nodes(ui.render()).find(node=>node.type==='label'&&node.props.label===label);assert.ok(field,label);nodes(field).find(node=>['input','textarea','select'].includes(node.type)).props.onChange({target:{value}});ui.render();}
  edit('资料依据','B');
  let first=saved.flows.核查业务.steps[0];assert.equal(first.sourceId,'B');assert.equal(first.citation,'');assert.deepEqual(first.citations,[]);assert.equal(first.inferenceReason,'');assert.equal(first.basisHistory[0].sourceId,'A');assert.equal(saved.flows.核查业务.confirmedSignature,null);
  edit('依据位置或说明','第二章新人工依据');
  rendered=text(ui.render());assert.ok(rendered.includes('当前依据由人工修改'));assert.ok(rendered.includes('修改前依据与原建议'));assert.ok(rendered.includes('不证明当前值'));assert.ok(rendered.includes('步骤模型解释-A'));
  await ui.click('确认当前业务流程');first=saved.flows.核查业务.steps[0];assert.equal(first.citation,'第二章新人工依据');assert.deepEqual(first.citations,[]);assert.equal(first.basisHistory.length,1);assert.ok(flowConfirmed(saved,saved.flows.核查业务));assert.deepEqual(saved.flows.核查业务.steps[1],initial.flows.核查业务.steps[1]);assert.deepEqual(saved.understandingRecords,initial.understandingRecords);
  ui.unmount();ui=mount(Setup,{...props,workspace:JSON.parse(JSON.stringify(saved))});await ui.flush();assert.ok(text(ui.render()).includes('当前依据由人工修改'));assert.equal(nodes(ui.render()).find(node=>node.type==='label'&&node.props.label==='依据位置或说明').props.children.props.value,'第二章新人工依据');
  await ui.click('将业务整体建议依据转入历史');assert.deepEqual(saved.flows.核查业务.citations,[]);assert.equal(saved.flows.核查业务.inferenceReason,'');assert.deepEqual(saved.flows.核查业务.basisHistory[0].citations,[cite('C','业务整体原文-C')]);assert.deepEqual(saved.flows.核查业务.steps[1],initial.flows.核查业务.steps[1]);assert.equal(saved.flows.核查业务.confirmedSignature,null);
  const pending=workspace(),flow=pending.flows.核查业务;
  const canonicalMaterials=[{...pending.materials[0],status:'needs_input'}];
  Object.assign(flow.steps[0],{role:'',citation:'原报告第101页待核',citations:[],inferenceReason:''});
  ui.unmount();ui=mount(Setup,{...props,workspace:pending,canonicalMaterials});await ui.flush();
  for(const label of ['负责人待定','待核依据','原报告第101页待核','资料解析尚未完成或待核对','查看原资料'])
   assert.ok(text(ui.render()).includes(label),label);
  const confirm=nodes(ui.render()).find(node=>node.type==='button'&&text(node)==='确认当前业务流程');
  assert.equal(confirm.props.disabled,true);
  await ui.click('保存草稿');
  assert.equal(saved.flows.核查业务.steps[0].sourceId,'A');
  assert.equal(saved.flows.核查业务.steps[0].citation,'原报告第101页待核');
  assert.equal(saved.flows.核查业务.steps[0].role,'');
  assert.equal(Object.hasOwn(saved.materials[0],'status'),false);
 }finally{ui?.unmount();Object.assign(globalThis,previous);await rm(directory,{recursive:true,force:true});}
});

test('flow confirmation blocks explicit incomplete material but preserves historical drafts without status',()=>{
 const value=workspace(),flow=value.flows.核查业务;
 assert.deepEqual(flowProblems(value,flow),[]);
 const current={...value.materials[0],status:'parsed'},issues=(rows,readStatus)=>flowProblems(value,flow,rows,readStatus).join('；');
 assert.match(issues([{...current,status:'needs_input'}]),/关联资料解析尚未完成/);
 assert.match(issues([],'ready'),/暂未找到关联资料/);
 assert.match(issues([{...current,archived:true}],'ready'),/服务器已归档/);
 assert.match(issues([{...current,hash:'different'}],'ready'),/原件摘要不一致/);
 assert.match(issues([current],'loading'),/正在核对/);
 assert.match(issues([current],'failed'),/读取失败/);
 for(const status of ['needs_input','queued','failed']){
  value.materials[0].status=status;
  assert.ok(flowProblems(value,flow).some(message=>message.includes('关联资料解析尚未完成')));
 }
 assert.deepEqual(flowProblems(value,flow,[current,{...value.materials[2],status:'parsed'}]),[]);
 value.materials[0].status='parsed';
 assert.deepEqual(flowProblems(value,flow),[]);
});
