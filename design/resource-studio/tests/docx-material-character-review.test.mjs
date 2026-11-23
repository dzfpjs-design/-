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
import {id,account as fixtureAccount} from './report-evidence-v3-fixture.mjs';

const account={...fixtureAccount,role:'admin'},material={id:id(20),version:4,hash:'a'.repeat(64),name:'合成.docx'},sha=x=>x.repeat(64);
let Component,tmp;
test.before(async()=>{tmp=await mkdtemp(join(tmpdir(),'docx-material-ui-'));const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/DocxMaterialCharacterReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const f=join(tmp,'component.mjs');await writeFile(f,out.output.find(x=>x.isEntry).code);Component=(await import(pathToFileURL(f))).default;});
test.after(()=>rm(tmp,{recursive:true,force:true}));
async function fixture(){const value={schema:'docx-visible-text-comparison-v2',actor_id:account.user_id,workspace_id:account.workspace_id,material:{id:material.id,version:material.version,hash:material.hash},legacy_reading_hash:sha('b'),visible_reading_hash:sha('c'),visible_text_policy:'preserve-ooxml-run-hyphens-v2',paragraph_count:1,changes:[{paragraph_index:1,body_index:1,legacy_id:'p1',visible_id:'v2:p1',before:'20℃',after:'‑20℃',nonbreaking_hyphens:1,optional_hyphens:0}],legacy_preserved:true,saved_new_view:false,can_apply:false,can_send:false};value.comparison_hash=await digest(value);return value;}
async function environment(fn){const dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});const root=createRoot(document.getElementById('app'));try{await fn(root,dom);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}}
async function waitFor(text){for(let i=0;i<60&&!document.body.textContent.includes(text);i++)await act(async()=>new Promise(r=>setTimeout(r,5)));assert.ok(document.body.textContent.includes(text),text);}
async function expand(dom){await act(async()=>{const el=document.querySelector('details');el.open=true;el.dispatchEvent(new dom.window.Event('toggle'));});}
async function click(){await act(async()=>document.querySelector('button').click());}

test('资料页无需阅读稿即可只读核对，重打开后从原件重算',async()=>environment(async(root,dom)=>{const value=await fixture(),calls=[];const requestJSON=async(path,options={})=>{calls.push({path,options});return path==='/me'?account:value;};const props={account,material,requestJSON};await act(async()=>root.render(React.createElement(Component,props)));await expand(dom);await click();await waitFor('原件保留特殊字符后的文字');assert.match(document.body.textContent,/‑20℃/);assert.match(document.body.textContent,/没有核验任何已保存的报告阅读稿/);assert.match(document.body.textContent,/尚不能用于本次分析/);assert.deepEqual(calls.map(x=>x.path.startsWith('/setup/')?'original':x.path),['/me','original','/me']);assert.ok(calls.every(x=>!x.options.method));await act(async()=>root.render(null));await act(async()=>root.render(React.createElement(Component,props)));await expand(dom);await click();await waitFor('原件保留特殊字符后的文字');assert.equal(calls.filter(x=>x.path.startsWith('/setup/')).length,2);}));

test('降权或会话变化时隐藏迟到对照，且不得转成发送或应用',async()=>environment(async(root,dom)=>{const value=await fixture();let finish,invalid=0,calls=[];const delayed=new Promise(resolve=>{finish=resolve});const requestJSON=async path=>{calls.push(path);return path==='/me'?account:delayed;};await act(async()=>root.render(React.createElement(Component,{account,material,requestJSON,onSessionInvalid:()=>invalid++})));await expand(dom);await click();await act(async()=>root.render(React.createElement(Component,{account:{...account,role:'viewer'},material,requestJSON,onSessionInvalid:()=>invalid++})));await act(async()=>finish(value));assert.doesNotMatch(document.body.textContent,/‑20℃/);assert.equal(invalid,0);assert.deepEqual(calls.map(x=>x.startsWith('/setup/')?'original':x),['/me','original']);}));

test('重新核对发现账号变化或权限失效，通知父页面而不显示原文',async()=>environment(async(root,dom)=>{const value=await fixture();for(const mode of ['changed','forbidden']){let identityCalls=0,invalid=0;const requestJSON=async path=>{if(path==='/me'){identityCalls++;if(mode==='changed'&&identityCalls===2)return {...account,user_id:id(33)};return account;}if(mode==='forbidden')throw Object.assign(Error('权限失效'),{status:403});return value;};await act(async()=>root.render(React.createElement(Component,{key:mode,account,material,requestJSON,onSessionInvalid:()=>invalid++})));await expand(dom);await click();await waitFor(mode==='forbidden'?'报告内容已隐藏':'暂时无法核对');assert.equal(invalid,1);assert.doesNotMatch(document.body.textContent,/‑20℃/);}}));
