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
import {digest} from '../src/report-full-reading-contract.mjs';
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`,sha=x=>x.repeat(64);let Component,tmp;
test.before(async()=>{tmp=await mkdtemp(join(tmpdir(),'catalogue-flow-'));const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/CatalogueFlowQuestions.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const f=join(tmp,'component.mjs');await writeFile(f,out.output.find(x=>x.isEntry).code);Component=(await import(pathToFileURL(f))).default;});test.after(()=>rm(tmp,{recursive:true,force:true}));
const account={user_id:id(1),workspace_id:id(2)},row={id:id(3)},plan={id:id(4),version:2,revision:1,plan_hash:sha('a')},cat={draft_id:id(5),version:2,document_hash:sha('b')},base={actor_id:account.user_id,workspace_id:account.workspace_id,producer_id:row.id,revision:1,plan_id:plan.id,plan_version:2,plan_hash:plan.plan_hash,scope_id:'b1'},q={key:'q1',kind:'missing_info',input_positions:[0,3],candidate_business_keys:['b1'],question:'两处数量不一致',status:'open',resolution:''};
const signed=async(v,k)=>({...v,[k]:await digest(v)}),Btn=({children,...props})=>React.createElement('button',props,children),props=c=>({controller:c,account,row,plan,scopeId:'b1',businessName:'测试业务',Btn});
async function scene(run){const dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});const root=createRoot(document.getElementById('app'));try{await run(root);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}}
async function waitFor(text){for(let i=0;i<60&&!document.body.textContent.includes(text);i++)await act(async()=>new Promise(r=>setTimeout(r,5)));assert.ok(document.body.textContent.includes(text),document.body.textContent);}
test('failed evidence can be retried without losing the selected question',async()=>scene(async root=>{
 const questions=await signed({schema:'report-catalogue-flow-questions-v1',...base,business_name:'测试业务',status:'available',catalogue:cat,offset:0,limit:20,total:1,has_more:false,questions:[q],sent_to_model:false,source_confirmed:false},'context_hash');
 const records=[0,3].map((n,i)=>({input_position:n,claim_hash:sha('c'),claim:{statement:`声明${i+1}`,status:'explicit',explanation:''},source_spans:[{span_ref:`w:p${n+1}/s1`,record:{source_ref:`w:p${n+1}`,text:`来源内容${i+1}`}}]}));
 const evidence=await signed({schema:'report-catalogue-flow-question-evidence-v1',...base,catalogue:cat,question_key:q.key,offset:0,limit:10,total:2,has_more:false,records,sent_to_model:false,source_confirmed:false},'evidence_hash');
 let questionCalls=0,evidenceCalls=0;
 const c={async http(path){if(!path.includes('question-evidence')){questionCalls++;return questions;}if(++evidenceCalls===1)throw Error('connection lost');return evidence;},notify(){}};
 await act(async()=>root.render(React.createElement(Component,props(c))));await waitFor('两处数量不一致');
 await act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='查看原文依据').click());await waitFor('原文依据暂时无法核对');
 await act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='重试读取').click());await waitFor('来源内容2');
 assert.equal(questionCalls,1);assert.equal(evidenceCalls,2);assert.match(document.body.textContent,/两处数量不一致/);
}));
test('switching questions hides the first evidence until the second is verified',async()=>scene(async root=>{
 const first={...q,input_positions:[0]},second={...q,key:'q2',input_positions:[3],question:'另一处来源待核'};
 const questions=await signed({schema:'report-catalogue-flow-questions-v1',...base,business_name:'测试业务',status:'available',catalogue:cat,offset:0,limit:20,total:2,has_more:false,questions:[first,second],sent_to_model:false,source_confirmed:false},'context_hash');
 const evidence=async(item,n)=>signed({schema:'report-catalogue-flow-question-evidence-v1',...base,catalogue:cat,question_key:item.key,offset:0,limit:10,total:1,has_more:false,records:[{input_position:n,claim_hash:sha('c'),claim:{statement:`声明${n}`,status:'explicit',explanation:''},source_spans:[{span_ref:`w:p${n+1}/s1`,record:{source_ref:`w:p${n+1}`,text:`来源内容${n}`}}]}],sent_to_model:false,source_confirmed:false},'evidence_hash');
 const one=await evidence(first,0),two=await evidence(second,3);let resolveSecond;
 const c={async http(path){if(!path.includes('question-evidence'))return questions;if(path.includes('question_key=q1'))return one;return new Promise(resolve=>{resolveSecond=resolve;});},notify(){}};
 await act(async()=>root.render(React.createElement(Component,props(c))));await waitFor('另一处来源待核');
 await act(async()=>[...document.querySelectorAll('button')].filter(b=>b.textContent==='查看原文依据')[0].click());await waitFor('来源内容0');
 await act(async()=>[...document.querySelectorAll('button')].filter(b=>b.textContent==='查看原文依据')[1].click());
 assert.doesNotMatch(document.body.textContent,/来源内容0/);assert.match(document.body.textContent,/正在读取原文依据/);
 await act(async()=>resolveSecond(two));await waitFor('来源内容3');assert.doesNotMatch(document.body.textContent,/来源内容0/);
}));
test('shows saved conflicting question and both sources, read only',async()=>scene(async root=>{const questions=await signed({schema:'report-catalogue-flow-questions-v1',...base,business_name:'测试业务',status:'available',catalogue:cat,offset:0,limit:20,total:1,has_more:false,questions:[q],sent_to_model:false,source_confirmed:false},'context_hash');const records=[0,3].map((n,i)=>({input_position:n,claim_hash:sha('c'),claim:{statement:`声明${i+1}`,status:'explicit',explanation:''},source_spans:[{span_ref:`w:p${n+1}/s1`,record:{source_ref:`w:p${n+1}`,text:`来源内容${i+1}`}}]}));const evidence=await signed({schema:'report-catalogue-flow-question-evidence-v1',...base,catalogue:cat,question_key:q.key,offset:0,limit:10,total:2,has_more:false,records,sent_to_model:false,source_confirmed:false},'evidence_hash');const calls=[],c={async http(path){calls.push(path);return path.includes('question-evidence')?evidence:questions;},notify(){}};await act(async()=>root.render(React.createElement(Component,props(c))));await waitFor('两处数量不一致');assert.match(document.body.textContent,/不会自动加入本次模型发送内容/);await act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='查看原文依据').click());await waitFor('来源内容2');assert.match(document.body.textContent,/来源内容1/);assert.equal(calls.length,2);}));
test('missing or invalid catalogue is not described as having no issues',async()=>scene(async root=>{const v=await signed({schema:'report-catalogue-flow-questions-v1',...base,business_name:'测试业务',status:'no_catalogue',catalogue:null,offset:0,limit:20,total:null,has_more:false,questions:[],sent_to_model:false,source_confirmed:false},'context_hash'),c={async http(){return v;},notify(){}};await act(async()=>root.render(React.createElement(Component,props(c))));await waitFor('无法判断是否存在待核事项');c.http=async()=>({...v,scope_id:'other'});await act(async()=>root.render(React.createElement(Component,{...props(c),key:'new'})));await waitFor('暂时无法核对目录');assert.doesNotMatch(document.body.textContent,/没有与本业务相关的待核事项/);}));
