import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {stableStringify} from '../src/content-signature.js';

let Component,createRoot,tmp;
const h=x=>createHash('sha256').update(stableStringify(x)).digest('hex');
const account={user_id:'00000000-0000-4000-8000-000000000001',workspace_id:'00000000-0000-4000-8000-000000000002',role:'operator'};
const id='00000000-0000-4000-8000-000000000003',savedId='00000000-0000-4000-8000-000000000004';
const stage='extract-1',source='a'.repeat(64),raw='b'.repeat(64),previewHash='c'.repeat(64),algorithm='report-failed-candidate-quarantine-v1';
const row={id,workspace_id:account.workspace_id,actor_id:account.user_id,revision:5,extractions:[{batch_id:stage}],preview:{source_hash:source,preview_hash:previewHash}};
const candidate={schema:'report-business-candidate-v3',objects:[{name:'合成对象',fields:[{label:'合成字段',unit:null}]}],businesses:[],relationships:[{name:'保留的合成关系',source_type:'合成来源',target_type:'合成目标',description:'合成关系说明'}],questions:['合成待核问题']};
const quarantine=[{kind:'unit',original_value:'合成单位'}];
const preview=()=>{const value={schema:'report-full-failed-candidate-preview-v1',scope:{workspace_id:account.workspace_id,actor_id:account.user_id,
  draft_id:id,revision:5,stage,preview_hash:previewHash,raw_hash:raw},algorithm_version:algorithm,
  candidate,quarantine,status:'needs_review',confirmed:false,can_apply:false,can_publish:false};return {...value,view_hash:h(value)};};
const Btn=({children,...props})=>React.createElement('button',props,children);
const flush=async()=>{await Promise.resolve();await new Promise(r=>setTimeout(r,0));};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const absent=()=>Object.assign(Error('not found'),{status:404});
const locks=()=>{const tails=new Map();return {request(name,options,fn){const prev=tails.get(name)||Promise.resolve(),next=prev.then(fn);tails.set(name,next.catch(()=>{}));return next;}};};
const props=(extra={})=>({account,row,stage,preview:preview(),Btn,...extra});

function saved(body){
 const scope={workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:id,stage};
 const signed={schema:'report-full-provisional-receipt-v1',...scope,request_id:body.request_id,draft_revision:body.revision,
  source_hash:body.source_hash,raw_hash:body.raw_hash,preview_hash:previewHash,view_hash:body.view_hash,algorithm_version:body.algorithm_version,
  candidate_hash:h(candidate),quarantine_hash:h(quarantine),command_hash:h({kind:'report_full_provisional_save',...scope,body})};
 return {schema:'report-full-provisional-v1',id:savedId,candidate:structuredClone(candidate),quarantine:structuredClone(quarantine),
  receipt:{...signed,receipt_hash:h(signed)},status:'needs_review',confirmed:false,can_apply:false,can_publish:false};
}
function fakeServer({lose=false,gate=null}={}){
 let record=null,posts=[];return {posts,get record(){return record;},async request(path,options={}){
  if(options.method==='POST'){
   const body=JSON.parse(options.body);posts.push(body);const made=saved(body);
   if(record&&record.receipt.request_id!==body.request_id)throw Object.assign(Error('duplicate'),{status:409,detail:{code:'full_provisional_already_saved'}});
   if(gate)await (Array.isArray(gate)?gate[posts.length-1]:gate).promise;record=record||made;if(lose){lose=false;throw Error('response lost');}return structuredClone(record);
  }
  if(path.includes('/provisional-receipts/')){const requested=path.split('/').pop();if(!record||record.receipt.request_id!==requested)throw absent();return structuredClone(record);}
  if(path.endsWith('/provisional'))return {schema:'report-full-provisional-list-v1',workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:id,stage,
    items:record?[{id:record.id,receipt:structuredClone(record.receipt),status:'needs_review',confirmed:false}]:[]};
  throw Error('unexpected');
 }};
}

test.before(async()=>{
 tmp=await mkdtemp(join(tmpdir(),'full-provisional-ui-'));const entry=fileURLToPath(new URL('../src/FullReadingProvisional.jsx',import.meta.url));
 const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:entry,write:false,minify:false,
  rollupOptions:{external:['react'],output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?new URL('../node_modules/react/index.js',import.meta.url).href:x}}}});
 const path=join(tmp,'component.mjs');await writeFile(path,out.output.find(x=>x.isEntry).code);Component=(await import(pathToFileURL(path))).default;
 ({createRoot}=await import('react-dom/client'));
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function withDOM(fn){
 const dom=new JSDOM('<div id="a"></div><div id="b"></div>',{url:'http://localhost/'}),names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const prior=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const n of names.slice(0,5))Object.defineProperty(globalThis,n,{configurable:true,writable:true,value:dom.window[n]});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const roots=[createRoot(document.getElementById('a')),createRoot(document.getElementById('b'))],lock=locks();
 const render=async(p,index=0)=>{await act(async()=>{roots[index].render(React.createElement(Component,{...p,locks:lock}));await flush();});};
 const click=async(label,index=0)=>{const container=document.getElementById(index?'b':'a'),button=[...container.querySelectorAll('button')].find(b=>b.textContent.includes(label));assert.ok(button,'missing '+label);assert.equal(button.disabled,false);await act(async()=>{button.click();await flush();});};
 const waitFor=async(predicate)=>{for(let i=0;i<100;i++){if(predicate())return;await act(async()=>{await flush();});}assert.ok(predicate(),'异步状态未按预期出现');};
 try{await fn({render,click,waitFor,roots,lock,dom});}finally{await act(async()=>{roots.forEach(r=>r.unmount());});dom.window.close();for(const n of names){if(prior[n])Object.defineProperty(globalThis,n,prior[n]);else delete globalThis[n];}}
}

test('原编号保存，服务器回执校验后展示机器候选，无人工确认控件',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer();await render(props({request:s.request}));assert.equal(s.posts.length,0);await click('保存此机器候选');
 await waitFor(()=>document.body.textContent.includes('机器候选已在服务器保存'));
 assert.equal(s.posts.length,1);assert.match(document.body.textContent,/服务器保存此机器候选|机器候选已在服务器保存/);
 assert.match(document.body.textContent,/尚未人工确认/);assert.match(document.body.textContent,/合成对象/);
 assert.match(document.body.textContent,/候选关系（仍需核对）/);assert.match(document.body.textContent,/保留的合成关系/);assert.match(document.body.textContent,/合成来源 → 合成目标/);
 assert.equal([...document.querySelectorAll('button')].filter(b=>/确认候选|应用|发布/.test(b.textContent)).length,0);
 assert.doesNotMatch(JSON.stringify(localStorage),/合成对象|合成单位|合成待核问题/);assert.equal(localStorage.length,0);
}));

test('保存成功但丢响应后刷新，原编号只读找回且不重复提交',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer({lose:true});await render(props({request:s.request}));await click('保存此机器候选');
 await waitFor(()=>s.posts.length===1&&document.body.textContent.includes('结果暂未确认'));
 assert.equal(s.posts.length,1);assert.match(document.body.textContent,/原保存编号尚待核对/);
 assert.doesNotMatch(JSON.stringify(localStorage),/合成对象|合成单位|合成待核问题/);
 await render({...props({request:s.request}),row:{...row,revision:6}});
 assert.match(document.body.textContent,/原保存编号尚待核对/);
 await click('只核对原保存回执');await waitFor(()=>document.body.textContent.includes('机器候选已在服务器保存'));assert.equal(s.posts.length,1);assert.match(document.body.textContent,/机器候选已在服务器保存/);assert.equal(localStorage.length,0);
}));

test('没有本地记录的新浏览器可按批发现并读取已保存候选',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer();const body={revision:5,request_id:'00000000-0000-5000-8000-000000000005',source_hash:source,raw_hash:raw,view_hash:preview().view_hash,algorithm_version:algorithm};
 await s.request('x',{method:'POST',body:JSON.stringify(body)});s.posts.length=0;
 await render(props({request:s.request,preview:null}));await click('查找已保存机器候选');assert.match(document.body.textContent,/已找到服务器保存的机器候选/);
 await click('查看已保存候选');await waitFor(()=>document.body.textContent.includes('合成对象'));assert.match(document.body.textContent,/合成对象/);assert.equal(s.posts.length,0);
}));

test('原任务推进到成功和待合并后，新浏览器仍可发现旧版待核保存稿',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer();const body={revision:5,request_id:'00000000-0000-5000-8000-000000000005',source_hash:source,raw_hash:raw,view_hash:preview().view_hash,algorithm_version:algorithm};
 await s.request('x',{method:'POST',body:JSON.stringify(body)});s.posts.length=0;
 const advanced={...row,revision:6,status:'awaiting_merge',extractions:[{batch_id:stage,status:'succeeded'}]};
 await render(props({request:s.request,row:advanced,preview:null}));await click('查找已保存机器候选');
 await waitFor(()=>document.body.textContent.includes('原草案第 5 版'));
 await click('查看已保存候选');await waitFor(()=>document.body.textContent.includes('合成对象'));
 assert.match(document.body.textContent,/尚未人工确认/);assert.equal(s.posts.length,0);
}));

test('不同浏览器相同保存意图使用同一编号，乱序回执不会重复保存',async()=>withDOM(async({render,click,waitFor})=>{
 const gates=[deferred(),deferred()],s=fakeServer({gate:gates});
 const makeStorage=()=>{const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)};};
 const first=makeStorage(),second=makeStorage();
 await render(props({request:s.request,storage:first}),0);await render(props({request:s.request,storage:second}),1);
 await click('保存此机器候选',0);await click('保存此机器候选',1);await waitFor(()=>s.posts.length===2);assert.equal(s.posts.length,2);
 assert.equal(s.posts[0].request_id,s.posts[1].request_id);
 await act(async()=>{gates[1].resolve();await flush();});await waitFor(()=>document.getElementById('b').textContent.includes('已在服务器保存'));
 assert.doesNotMatch(document.getElementById('a').textContent,/已在服务器保存/);
 await act(async()=>{gates[0].resolve();await flush();});await waitFor(()=>document.getElementById('a').textContent.includes('已在服务器保存'));
 assert.match(document.getElementById('a').textContent,/已在服务器保存/);assert.match(document.getElementById('b').textContent,/已在服务器保存/);
 assert.equal(localStorage.length,0);
}));

test('读取原号未发现不能换号，明确重试仅使用相同编号',async()=>withDOM(async({render,click,waitFor})=>{
 let offline=true;const s=fakeServer(),request=async(path,o={})=>{if(o.method==='POST'&&offline)throw Error('transport lost');return s.request(path,o);};
 await render(props({request}));await click('保存此机器候选');await waitFor(()=>document.body.textContent.includes('原保存编号尚待核对'));assert.match(document.body.textContent,/原保存编号尚待核对/);
 const stored=JSON.parse(localStorage.getItem(localStorage.key(0)));await click('只核对原保存回执');await waitFor(()=>document.body.textContent.includes('不证明原请求不会稍后提交'));assert.match(document.body.textContent,/不证明原请求不会稍后提交/);
 offline=false;await click('按原编号重试保存');await waitFor(()=>s.posts.length===1);assert.equal(s.posts.length,1);assert.equal(s.posts[0].request_id,stored.body.request_id);
}));

test('本机存储不可用时不提交',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer(),broken={getItem(){return null;},setItem(){throw Error('disk');}};
 await render(props({request:s.request,storage:broken}));await click('保存此机器候选');await waitFor(()=>document.body.textContent.includes('未发送保存请求'));assert.equal(s.posts.length,0);assert.match(document.body.textContent,/未发送保存请求/);
}));

test('伪造服务器回执不显示结果，原编号保留',async()=>withDOM(async({render,click,waitFor})=>{
 const s=fakeServer();await render(props({request:async(path,o)=>{const value=await s.request(path,o);if(o?.method==='POST')value.candidate.objects[0].name='伪造内容';return value;}}));
 await click('保存此机器候选');await waitFor(()=>document.body.textContent.includes('结果暂未确认'));assert.doesNotMatch(document.body.textContent,/伪造内容/);assert.match(document.body.textContent,/结果暂未确认/);
}));

test('账号切换或权限失效时隐藏私人内容且迟到原请求不会发 POST',async()=>withDOM(async({render,click})=>{
 const wait=deferred();let posts=0,invalid=0;const request=async(path,o={})=>{if(o.method==='POST'){posts++;return saved(JSON.parse(o.body));}await wait.promise;throw absent();};
 await render(props({request,onSessionInvalid:()=>invalid++}));await click('保存此机器候选');
 await render(props({request,account:{...account,role:'viewer'},onSessionInvalid:()=>invalid++}));
 await act(async()=>{wait.resolve();await flush();});assert.equal(posts,0);assert.equal(invalid,0);assert.doesNotMatch(document.body.textContent,/合成对象/);
}));

test('旧账号的迟到权限错误不能注销当前账号',async()=>withDOM(async({render,click})=>{
 const wait=deferred();let invalid=0;const request=async()=>{await wait.promise;throw Object.assign(Error('revoked'),{status:403});};
 await render(props({request,onSessionInvalid:()=>invalid++}));await click('查找已保存机器候选');
 await render(props({request,account:{...account,role:'viewer'},onSessionInvalid:()=>invalid++}));
 await act(async()=>{wait.resolve();await flush();});assert.equal(invalid,0);assert.doesNotMatch(document.body.textContent,/合成对象/);
}));

test('原保存范围变更时保留待核记录，不能静默生成新编号',async()=>withDOM(async({render,click,waitFor})=>{
 const request=async()=>{throw Error('offline');};await render(props({request}));await click('保存此机器候选');
 await waitFor(()=>document.body.textContent.includes('结果暂未确认'));assert.equal(localStorage.length,1);
 await render(props({request,row:{...row,revision:6,preview:{...row.preview,source_hash:'d'.repeat(64)}}}));
 await waitFor(()=>document.body.textContent.includes('原保存编号暂不能核对'));
 const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('保存此机器候选'));assert.equal(b.disabled,true);assert.equal(localStorage.length,1);
}));
