import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {nestedPlan,action,attr,A} from './nested-flow-review-fixture.mjs';

// Real SSR-bundled JSX + ReactDOM; synthetic scale/source data only. This is
// neither browser latency nor actual model/source/IAM/persistence acceptance.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),copy=value=>structuredClone(value);
let directory,compiled;
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{directory=await mkdtemp(join(tmpdir(),'nested-flow-review-react-'));const result=await build({root:project,configFile:false,envDir:directory,cacheDir:join(directory,'cache'),logLevel:'silent',mode:'test',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/NestedFlowReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});const output=result.output.find(row=>row.type==='chunk'&&row.isEntry),target=join(directory,'nested.mjs');assert.ok(output);await writeFile(target,output.code);return (await import(pathToFileURL(target).href)).default;})();
 return compiled;
}
async function browser(){
 assert.equal(process.env.NODE_ENV,'development');const errors=[],network=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('external resource forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-nested.invalid/',resources:new NoResources(),virtualConsole:console});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,fetch:url=>{network.push(String(url));throw Error('network forbidden');}},previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const Component=await component(),{createRoot}=await import('react-dom/client'),container=dom.window.document.getElementById('root'),root=createRoot(container,{onCaughtError:error=>errors.push(error),onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
 function paint(props){root.render(React.createElement(React.StrictMode,null,React.createElement(Component,props)));}
 async function render(props){await act(async()=>paint(props));assert.deepEqual(errors,[]);}
 async function until(check,label){const end=Date.now()+5000;while(Date.now()<end){await act(async()=>{await new Promise(done=>setImmediate(done));});if(check())return;}assert.ok(check(),label);}
 async function type(node,value){await act(async()=>{const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});}
 async function close(){try{await act(async()=>root.unmount());assert.deepEqual(errors,[]);assert.deepEqual(network,[]);}finally{dom.window.close();for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}
 return {container,render,paint,until,type,close,text:()=>container.textContent||''};
}

test('143 allowed sources and 36 actions keep the initial DOM bounded and every last source remains selectable',{concurrency:false,timeout:30000},async()=>{
 const h=await browser();let value=nestedPlan(),changes=[];
 value.business.flows=Array.from({length:2},(_,flow)=>({name:'公开规模流程 '+(flow+1),trigger:{kind:'manual',description:'公开合成触发条件',attribution:attr()},phase_order:attr(),phases:[{kind:'steps',attribution:attr(),steps:Array.from({length:17},(_,i)=>action('合成工作 '+flow+'-'+i))}],completion:action('完成流程 '+flow)}));value.business.questions=[];
 const sources=Array.from({length:143},(_,i)=>({span_ref:'w:p'+(i+1)+'/s1',source_text:'公开目录第'+(i+1)+'条：完整资料、限定条件与待核事项均保留。'}));value.business.flows[0].trigger.attribution.citations=[A,A];const before=copy(value);
 const props=()=>({value,flowIndex:0,sources,onChange:(next,options)=>{value=next;changes.push({value:copy(next),options});h.paint(props());}});
 try{
  await h.render(props());assert.equal(value.business.flows.reduce((count,flow)=>count+flow.phases[0].steps.length+1,0),36);
  const initialNodes=h.container.querySelectorAll('*').length,initialBytes=new TextEncoder().encode(h.container.outerHTML).length;assert.ok(initialNodes<4000,'initial DOM must not repeat all 143 sources per attribution: '+initialNodes);assert.ok(initialBytes<700000,'initial DOM serialization stays bounded: '+initialBytes);assert.equal(h.container.querySelectorAll('input[type="checkbox"][aria-label*="关联原文"]').length,0,'closed choices mount no candidate catalog');assert.equal(h.text().includes('公开目录第143条'),false);
  const attribution=[...h.container.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='流程触发条件的依据与说明');await act(async()=>attribution.querySelector('summary').click());const chooser=[...attribution.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='选择或调整关联原文');await act(async()=>chooser.querySelector('summary').click());await h.until(()=>!!chooser.querySelector('input[aria-label="流程触发条件搜索全部原文"]'),'source catalog mounts lazily on native toggle');
  assert.equal(chooser.querySelectorAll('input[type="checkbox"]').length,20);const more=[...chooser.querySelectorAll('button')].find(button=>button.textContent==='继续显示后20段原文');await act(async()=>more.click());assert.equal(chooser.querySelectorAll('input[type="checkbox"]').length,40);
  const search=chooser.querySelector('input[aria-label="流程触发条件搜索全部原文"]');await h.type(search,'第143条');assert.equal(chooser.querySelectorAll('input[type="checkbox"]').length,1);const last=chooser.querySelector('input[aria-label="流程触发条件关联原文 143"]');assert.ok(last);await act(async()=>last.click());await h.until(()=>changes.length===1,'explicit last allowed source changes only its original citation array');assert.deepEqual(value.business.flows[0].trigger.attribution.citations,[A,A,'w:p143/s1']);assert.deepEqual(value.business.flows[1],before.business.flows[1]);assert.equal(changes[0].options.backup,true);
  await act(async()=>chooser.querySelector('summary').click());await h.until(()=>chooser.querySelectorAll('input[type="checkbox"]').length===0,'closing unmounts the full candidate page');assert.deepEqual(value.business.flows[0].trigger.attribution.citations,[A,A,'w:p143/s1']);assert.equal(changes.length,1);assert.equal(before.business.flows[0].trigger.attribution.citations.length,2);
 }finally{await h.close();}
});

test('recursive source rows use exact paths and preserve original ancestry after local edits without leaking pointers',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),value=nestedPlan(),path='/business/flows/0/phases/0/branches/0/phases/0/branches/1/phases/0/steps/0',citations=[],view={schema:'business-flow-nested-causality-view-v193',original_candidate_only:true,runtime_entry_binding_verified:false,current_draft_matches_original:true,rows:[{flow_index:0,flow_name:'修复建议审批与并行补核',action_path:path,step_name:'补充现场观察',action_name:'补充现场观察',actor:'perception_agent',role:'观测岗位',control_ancestors:[{path:'/business/flows/0/phases/0',kind:'decision',branch_index:0},{path:'/business/flows/0/phases/0/branches/0/phases/0',kind:'parallel',branch_index:1},{path:'/business/flows/0/phases/0/branches/0/phases/0/branches/1/phases/0',kind:'repeat',branch_index:null}],input_path:path+'/inputs/0',input_ordinal:0,input_name:'原候选观测请求',sources:[{kind:'entry',entry_path:'/entry_ports/0',label:'流程触发或配置需提供的资料（待绑定）'},{kind:'step',action_path:'/business/flows/0/phases/0/decision',step_name:'确认处置建议',output_path:'/business/flows/0/phases/0/decision/outputs/0',output_ordinal:0,output_name:'确认处置建议记录',actor:'human',role:'值班负责人'}],citations:[A]}]};
 const props={value,generationCausality:view,onCitation:ref=>citations.push(ref)},card=()=>h.container.querySelector('[data-review-path="'+path+'"]').querySelector('[aria-label="递归步骤资料来源核对"]');
 try{
  await h.render(props);for(const phrase of ['条件分支第 1 支','并行分支第 2 支','循环核查','备选来源（或）','流程入口资料：待绑定','尚未证明资料真实存在'])assert.ok(card().textContent.includes(phrase),phrase);const original=copy(view);await act(async()=>card().querySelector('button').click());assert.deepEqual(citations,[A]);
  const changed=copy(value);changed.business.flows[0].phases[0].branches[0].phases[0].branches[1].phases[0].steps[0].name='人工重命名的当前观察';await h.render({...props,value:changed,currentChanged:true});assert.ok(card().textContent.includes('本稿已修改'));assert.ok(card().textContent.includes('原候选步骤：补充现场观察'));assert.equal(card().textContent.includes('人工重命名的当前观察'),false);
  const missing=copy(view);missing.rows[0].action_path='/not-the-current-action';await h.render({...props,value:changed,generationCausality:missing,currentChanged:true});assert.ok(card().textContent.includes('当前位置没有对应'));assert.equal(card().textContent.includes('原候选观测请求'),false);assert.equal(card().querySelectorAll('button').length,0);
  await h.render({...props,generationCausality:{...copy(view),schema:'wrong'},currentChanged:true});assert.ok(card().textContent.includes('暂不能完整核对'));for(const hidden of [path,'/entry_ports/0','/not-the-current-action','input_path','flow-v193'])assert.equal(h.text().includes(hidden),false,hidden);assert.deepEqual(view,original);
 }finally{await h.close();}
});
