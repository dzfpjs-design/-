// Actual React/component/controller source over a synthetic closed transport.
// No native-browser, DB, publication, ACK, Goal or cloud-quality claim is made.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {goalDataFixture,id} from './authored-goal-data-workflow-v288-fixture.mjs';
import {OPERATIONS,OPERATION_LABELS,SCHEMAS} from '../src/authored-goal-data-workflow-v288-protocol.mjs';
const rootPath=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(rootPath,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href,temporary=[];
let bundled;
after(async()=>{for(const folder of temporary)await rm(folder,{recursive:true,force:true});});
async function components(){
 if(!bundled)bundled=(async()=>{
  const folder=await mkdtemp(join(tmpdir(),'authored-goal-data-v288-'));temporary.push(folder);const entry=join(folder,'entry.jsx');
  await writeFile(entry,`export {default as Panel} from ${JSON.stringify(join(rootPath,'src/AuthoredDecisionWorkflowSetupV286.jsx'))};export {default as Host} from ${JSON.stringify(join(rootPath,'src/AuthoredWorkflowDesignerV243.jsx'))};export {default as Setup} from ${JSON.stringify(join(rootPath,'src/SetupStudio.jsx'))};`);
  const output=await build({root:rootPath,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});
  const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry),file=join(folder,'actual.mjs');assert.ok(chunk);await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return bundled;
}
async function harness(run,{surface='panel',profile='v288'}={}){
 assert.equal(process.env.NODE_ENV,'development');const f=goalDataFixture(),errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoNetwork extends ResourceLoader{fetch(){throw Error('No actual network in synthetic React tests');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://goal-data-ui.invalid/?view=business',resources:new NoNetwork(),virtualConsole});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,location:dom.window.location,history:dom.window.history,localStorage:f.storage,sessionStorage:f.session,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window),fetch:async()=>new Response(JSON.stringify({detail:'此离线测试未配置附属能力，明确不可用'}),{status:503,headers:{'Content-Type':'application/json'}})};
 const prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 Object.defineProperty(dom.window.navigator,'locks',{value:f.locks,configurable:true});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const api=async(path,options={})=>{
  if(path.startsWith('/setup/authored-workflow-graphs/context'))return {schema:'authored-workflow-context-v208',configuration_revision:f.state.revision,configuration_hash:f.context().configuration_hash,business_name:f.scope.business_name,flow:f.flow,human_roles:[f.role],packet_role:'原资料岗位',reasoning_role:'原分析岗位',prior_origin:f.state.origin,members:f.people,capabilities:[],adoptions:[],can_preview:true,reason:null,can_execute:false,cloud_authorized:false};
  return f.api(path,options);
 };
 const modules=await components(),{createRoot}=await import('react-dom/client'),container=document.getElementById('root');let root,guard,props;
 let savedWorkspace={businesses:[f.scope.business_name],flows:{[f.scope.business_name]:f.flow},instanceProfile:{name:'资料准备实例',domain:'通用业务',goal:'资料充分后给出有依据的建议'},materials:[],resources:[],automation:{version:1,policies:{}}};
 const Button=({children,primary,ghost,icon,...rest})=>React.createElement('button',{type:'button',...rest},children),Field=({label,children})=>React.createElement('label',null,label,children),Icon=()=>React.createElement('span'),Dialog=({children})=>React.createElement('div',null,children);
 const until=async(predicate,label='expected actual React state')=>{for(let tick=0;tick<250;tick++){assert.deepEqual(errors,[]);if(predicate())return;await act(async()=>new Promise(resolve=>setTimeout(resolve,2)));}assert.ok(predicate(),label+': '+container.textContent.slice(0,600));};
 const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
 const click=async label=>{await until(()=>button(label)&&!button(label).disabled,label);await act(async()=>button(label).click());};
 const change=async(label,value)=>{
  const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);
  const type=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement:node.tagName==='INPUT'?dom.window.HTMLInputElement:dom.window.HTMLSelectElement;
  await act(async()=>{Object.getOwnPropertyDescriptor(type.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));if(node.tagName!=='SELECT')node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
 };
 const check=async text=>{const label=[...container.querySelectorAll('label')].find(node=>node.textContent.includes(text)&&node.querySelector('input[type="checkbox"]'));assert.ok(label,text);await act(async()=>label.querySelector('input').click());};
 const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(surface==='setup'?modules.Setup:surface==='host'?modules.Host:modules.Panel,props))));};
 const mount=async()=>{
  props=surface==='setup'?{runtime:true,step:1,workspace:savedWorkspace,account:f.account,authoredWorkflowAPI:api,authoredConfigurationSaved:true,onAuthoredAdopted:async result=>{const confirmed=f.readback(result);savedWorkspace={...savedWorkspace,authoredWorkflowGraphs:{[f.scope.business_name]:result.origin}};props={...props,workspace:savedWorkspace};await render();f.adopted.push(result);return confirmed;},onCommit:()=>true,onSave:async()=>true,onGo:()=>{},onRegisterGuard:fn=>{guard=fn;},Icon,Btn:Button,Field,Dialog}:{businessName:f.scope.business_name,account:f.account,api,profile,Btn:Button,configurationSaved:true,onRegisterGuard:(fn,previous)=>{if(fn)guard=fn;else if(!previous||guard===previous)guard=null;},onAdopted:async result=>{const confirmed=f.readback(result);f.adopted.push(result);return confirmed;}};
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();
 };
 const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await Promise.resolve();}};
 try{await run({f,container,until,click,change,check,button,mount,unmount,text:()=>container.textContent,guard:()=>guard,dom});assert.deepEqual(errors,[]);}finally{await unmount();dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
async function choose(h){
 for(const op of OPERATIONS){const cap=h.f.caps.find(item=>item.operation===op);await h.change(OPERATION_LABELS[op]+'能力',cap.id);await h.change(OPERATION_LABELS[op]+'执行人',h.f.account.user_id);}
 await h.change('原人工负责人：'+h.f.role,id(3));await h.change('资料与业务建议核对人',id(4));
 await h.click('添加候选方案');await h.change('候选方案 1 说明','使用已登记能力补充目标资料，再由业务负责人核对建议。');
 await h.change('候选方案 1 能力',h.f.caps[2].id);await h.change('候选方案 1 交付方式','requested_observation');await h.check('监测对象 · 温度');
 await h.change('资料时间范围','rolling_latest');await h.change('资料有效时长（秒）','3600');await h.check('资料不足时允许按已批准范围补采');
 await h.click('确认资料准备规则');await h.until(()=>h.text().includes('当前资料准备规则已确认'));
 await h.change('业务建议类型','business_conclusion');await h.check('明确在模拟范围加入上述六项');
}
test('six-capability panel persists incomplete rule input and exposes explicit policy intent and both review stages',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();await h.until(()=>h.container.querySelector('[aria-label="资料时间范围"]'));
 assert.equal(h.container.querySelector('[aria-label="资料时间范围"]').value,'');assert.equal(h.container.querySelector('[aria-label="读取目标资料能力"]').value,'');
 await h.change('资料时间范围','fixed_window');await h.change('资料有效时长（秒）','72');
 await h.unmount();await h.mount();await h.until(()=>h.container.querySelector('[aria-label="资料时间范围"]'));
 assert.equal(h.container.querySelector('[aria-label="资料时间范围"]').value,'fixed_window');assert.equal(h.container.querySelector('[aria-label="资料有效时长（秒）"]').value,'72');
 assert.equal(h.button('预览资料准备与业务建议流程').disabled,true);
 await choose(h);await h.click('预览资料准备与业务建议流程');await h.until(()=>h.container.querySelector('[aria-label="资料准备与业务建议采用预览"]'));
 assert.equal(h.f.state.previews[0].schema,SCHEMAS.previewCommand);assert.equal(h.f.state.previews[0].decision_intent,'business_conclusion');
 assert.equal(h.container.querySelectorAll('[aria-label="选择六项能力与执行人"] article').length,6);
 assert.ok(h.text().includes('平台新增资料核对'));assert.ok(h.text().includes('平台新增人工建议核对'));
 assert.ok(!h.text().includes(h.f.caps[2].id));assert.ok(!h.text().includes('policy_hash'));assert.ok(!h.text().includes('当前支持信息获取建议'));
 assert.equal(h.button('采用资料准备与业务建议流程并保存修订').disabled,true);assert.equal(h.f.state.posts.length,0);
}));
test('four-mode host restores exact unknown V288 request and prevents switching until original recovery',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();await h.until(()=>h.button('先准备资料再分析'));await h.click('先准备资料再分析');await h.until(()=>h.container.querySelector('[aria-label="资料时间范围"]'));
 await choose(h);await h.click('预览资料准备与业务建议流程');await h.check('我已核对真实能力');h.f.state.unknown=true;
 await h.click('采用资料准备与业务建议流程并保存修订');await h.until(()=>h.container.querySelector('[aria-label="业务建议原申请待核对"]'));
 const original=[...h.f.storage.rows.entries()].find(([key])=>key.includes('ga-authored-goal-data-workflow-v288:')&&key.includes(':original:'));assert.ok(original);assert.equal(h.guard()(),false);
 await h.click('业务建议与补充观测');assert.ok(h.container.querySelector('[aria-label="业务建议原申请待核对"]'));
 await h.unmount();await h.mount();await h.until(()=>h.button('按原编号与原内容继续'));
 assert.equal(h.f.state.posts.length,1);assert.ok(h.f.state.gets.length>=1);
 h.f.state.unknown=false;await h.click('按原编号与原内容继续');await h.until(()=>h.f.adopted.length===1&&h.guard()&&h.guard()()===true,'original adoption and page readback both completed');
 assert.deepEqual(h.f.state.posts,[JSON.parse(original[1]).raw,JSON.parse(original[1]).raw]);assert.equal(h.f.storage.getItem(original[0]),original[1]);
 await h.click('文档资料处理');await h.until(()=>h.text().includes('配置运行期资料前置'));assert.ok(h.button('图像原件技术复核'));assert.ok(h.button('业务建议与补充观测'));
},{surface:'host'}));
test('actual SetupStudio accepts only its V288 configuration readback and retries a saved readback without adopting twice',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();const summary=[...h.container.querySelectorAll('summary')].find(node=>node.textContent==='为当前业务配置运行期资料处理');assert.ok(summary);await act(async()=>summary.click());
 await h.until(()=>h.button('先准备资料再分析'));await h.click('先准备资料再分析');await h.until(()=>h.container.querySelector('[aria-label="资料时间范围"]'));
 await choose(h);await h.click('预览资料准备与业务建议流程');await h.check('我已核对真实能力');h.f.state.readbackError=true;
 await h.click('采用资料准备与业务建议流程并保存修订');await h.until(()=>h.button('重新读回已采用配置'));
 assert.equal(h.f.state.posts.length,1);assert.equal(h.guard()(),false);h.f.state.readbackError=false;
 await h.click('重新读回已采用配置');await h.until(()=>h.text().includes('采用配置已在当前页面读回')&&h.guard()&&h.guard()()===true,'saved adoption read back and durable UI guard released');assert.equal(h.f.adopted.length,1);assert.equal(h.f.state.posts.length,1);
 assert.ok(h.text().includes('采用配置已在当前页面读回'));assert.equal(h.guard()(),true);
},{surface:'setup'}));
test('unknown component profile is blocked before any transport or persisted command',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();await h.until(()=>h.text().includes('未知业务流程配置协议'));assert.equal(h.f.state.posts.length,0);assert.equal(h.f.state.previews.length,0);
},{profile:'unknown'}));
