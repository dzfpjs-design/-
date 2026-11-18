import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM} from 'jsdom';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {id,account as fixtureAccount} from './report-evidence-v3-fixture.mjs';
import {digest} from '../src/report-full-reading-contract.mjs';
const account={...fixtureAccount,role:'admin'},sha=x=>x.repeat(64);
let Component,tmp;
test.before(async()=>{tmp=await mkdtemp(join(tmpdir(),'reduce-react-'));const result=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/DelegatedReducePreview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}}),file=join(tmp,'component.mjs');await writeFile(file,result.output.find(x=>x.isEntry).code);Component=(await import(pathToFileURL(file))).default;});
test.after(()=>rm(tmp,{recursive:true,force:true}));
test('React DOM renders an honest read-only preview and hides it on account change',async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],previous=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const row={id:id(50),actor_id:account.user_id,workspace_id:account.workspace_id,request_id:'fl_'+id(51),revision:7,view_hash:sha('a'),status:'awaiting_merge',job_id:id(52),preview:{source_hash:sha('b')},maps:Array.from({length:14},(_,i)=>({chunk_id:'map-'+(i+1),status:'succeeded',result_hash:sha('c'),reserved_call_id:id(100+i)}))};
 const preflight={schema:'delegated-reduce-readonly-preflight-v1',draft_id:row.id,revision:row.revision,workspace_id:account.workspace_id,actor_id:account.user_id,original_job_id:row.job_id,source_hash:row.preview.source_hash,map_plan_hash:sha('d'),map_results_hash:await digest(row.maps.map(s=>[s.chunk_id,s.result_hash,s.reserved_call_id])),map_calls:14,reduction_preview_hash:sha('e'),reduction_preparation_hash:sha('f'),token_bound:40000,additional_call_count:1,additional_cost:true,user_confirmed_business:false,can_start:false,can_send:false};preflight.preflight_hash=await digest(preflight);
 const value={schema:'delegated-reduce-preview-v1',preflight,supported_map_count:14,maximum_call_count:1,token_upper_bound:40000,price_limit_available:false,price_upper_bound:null,price_note:'unknown',additional_cost:true,user_confirmed_business:false,can_start:false,can_send:false,start_blocker:'not enabled'},calls=[];
 const session={row,invalidIdentity:false,async identity(){},async delegatedCheck(){return {verified:true};},emit(){},async request(path,opts){calls.push({path,opts});return value;}};
 const root=createRoot(document.getElementById('app'));
 try{await act(async()=>root.render(React.createElement(Component,{account,row,session})));await act(async()=>document.querySelector('button').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true})));for(let i=0;i<50&&!document.body.textContent.includes('40,000 token');i++)await act(async()=>new Promise(resolve=>setTimeout(resolve,10)));assert.match(document.body.textContent,/40,000 token/);assert.match(document.body.textContent,/当前不能开始汇总/);assert.equal(calls.length,1);assert.equal(document.querySelectorAll('button').length,1);await act(async()=>root.render(React.createElement(Component,{account:{...account,user_id:id(99)},row,session})));assert.doesNotMatch(document.body.textContent,/40,000 token/);}finally{await act(async()=>root.unmount());dom.window.close();for(const [key,descriptor] of Object.entries(previous)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
});
