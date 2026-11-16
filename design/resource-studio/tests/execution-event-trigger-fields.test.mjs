// Actual field events only. No server, source, route, Task or device authority.
import test from 'node:test';import assert from 'node:assert/strict';
import React,{act} from 'react';import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import {JSDOM,ResourceLoader} from 'jsdom';import {build} from 'vite';
import {newBindings,selectAnomalyTrigger,EVENT_BINDINGS_SCHEMA,ANOMALY_TRIGGER_MODE,bindingRequestJSON} from '../src/business-flow-execution-binding-fields.mjs';
const flow={flow_id:'event_flow',name:'合成疑似异常核查',trigger:{kind:'event',description:'原资料中的疑似异常，尚需明确选择实际启动方式'},nodes:[{node_id:'review',name:'核查疑似异常',actor:'human',role:'值班核查',responsibility:'核对来源后记录判断',inputs:[],outputs:['核查结论']}],edges:[],deliverables:[],start_node:'review',end_nodes:['review']};
test('new event arrangement never guesses a mode or selects an actor and preserves original bindings on explicit choice',()=>{
 const value=newBindings(flow),before=bindingRequestJSON(value);assert.equal(value.schema,EVENT_BINDINGS_SCHEMA);assert.deepEqual(value.trigger,{mode:''});assert.equal(value.nodes.review,null);
 const selected=selectAnomalyTrigger(value,flow,ANOMALY_TRIGGER_MODE);assert.deepEqual(selected.trigger,{mode:ANOMALY_TRIGGER_MODE});assert.equal(selected.nodes.review,null);assert.equal(bindingRequestJSON(value),before);
 assert.throws(()=>selectAnomalyTrigger(value,{...flow,trigger:{kind:'schedule'}},ANOMALY_TRIGGER_MODE));assert.throws(()=>selectAnomalyTrigger(value,flow,'automatic_by_text'));
 assert.deepEqual(selectAnomalyTrigger(selected,flow,'').trigger,{mode:''});
});
test('actual event selector requires a user choice and explains separate instance grant, approval and device collection',async()=>{
 assert.equal(process.env.NODE_ENV,'development');const project=resolve(fileURLToPath(new URL('..',import.meta.url))),dir=await mkdtemp(join(tmpdir(),'event-trigger-fields-'));
 class Closed extends ResourceLoader{fetch(){throw Error('Network forbidden');}}
 const dom=new JSDOM('<div id="root"></div>',{url:'http://fields.invalid/',resources:new Closed(),pretendToBeVisual:true});const names=['window','document','navigator','HTMLElement','Event','Node','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));let root;
 try{
  for(const k of names)Object.defineProperty(globalThis,k,{value:k==='IS_REACT_ACT_ENVIRONMENT'?true:dom.window[k],configurable:true,writable:true});
  const output=await build({root:project,configFile:false,envDir:dir,mode:'test',cacheDir:join(dir,'cache'),logLevel:'silent',build:{ssr:join(project,'src/BusinessFlowExecutionBindingFields.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  const target=join(dir,'fields.mjs');await writeFile(target,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);const Component=(await import(pathToFileURL(target).href)).default;const {createRoot}=await import('react-dom/client');root=createRoot(dom.window.document.querySelector('#root'));
  let value=newBindings(flow);const draw=()=>root.render(React.createElement(Component,{sourceValue:{business:{flows:[flow]}},flowId:flow.flow_id,value,buffers:{},onChange:next=>{value=next;draw();}}));await act(async()=>draw());
  let control=dom.window.document.querySelector('select[aria-label="事件启动方式"]');assert.equal(control.value,'');assert.equal(value.trigger.mode,'');assert.equal(dom.window.document.body.textContent.includes('原绑定版本不匹配'),false);assert.ok(dom.window.document.body.textContent.includes('预览并明确授权'));assert.ok(dom.window.document.body.textContent.includes('审批和设备补采仍分别确认'));
  await act(async()=>{control.value=ANOMALY_TRIGGER_MODE;control.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});assert.equal(value.trigger.mode,ANOMALY_TRIGGER_MODE);assert.equal(value.nodes.review,null);assert.equal(dom.window.document.querySelector('select[aria-label="事件启动方式"]').value,ANOMALY_TRIGGER_MODE);
  control=dom.window.document.querySelector('select[aria-label="事件启动方式"]');await act(async()=>{control.value='';control.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});assert.equal(value.trigger.mode,'');
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const k of names)if(old[k])Object.defineProperty(globalThis,k,old[k]);else delete globalThis[k];await rm(dir,{recursive:true,force:true});}
});
