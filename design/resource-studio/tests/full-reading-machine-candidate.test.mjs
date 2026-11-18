import test from 'node:test';
import assert from 'node:assert/strict';
import React, {act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {stableStringify} from '../src/content-signature.js';

let Component, createRoot, tmp;
const previewHash='a'.repeat(64),rawHash='b'.repeat(64),account={user_id:'user-1',workspace_id:'workspace-1',role:'operator'};
const citation={source_ref:'w:p1',quote:'合成原文依据'};
const row={id:'draft-1',actor_id:account.user_id,workspace_id:account.workspace_id,revision:5,
  extractions:[{batch_id:'extract-1',status:'failed'}],
  preview:{preview_hash:previewHash,batches:[{batch_id:'extract-1',source_refs:['w:p1'],
    payload:{blocks:[['w:p1','paragraph',[4],'此处是合成原文依据的完整来源段落。']]}}]}};
const Btn=({children,...props})=>React.createElement('button',props,children);
const relation={name:'合成关系',description:'合成关系说明',source_type:'对象甲',target_type:'对象乙',citations:[citation]};
const sign=value=>{delete value.view_hash;value.view_hash=createHash('sha256').update(stableStringify(value)).digest('hex');return value;};
const reply=()=>sign({schema:'report-full-failed-candidate-preview-v1',scope:{workspace_id:account.workspace_id,actor_id:account.user_id,
  draft_id:row.id,revision:row.revision,stage:'extract-1',preview_hash:previewHash,raw_hash:rawHash},
  candidate:{schema:'report-business-candidate-v3',domain:'合成领域',objects:[{name:'合成对象',fields:[{label:'测量字段',kind:'数值',unit:null,citations:[citation]}]}],
    businesses:[{name:'合成业务',trigger:'人工发起',output:'待核结果',steps:[{name:'合成步骤'}]}],relationships:[],questions:['请核对合成条件。']},
  quarantine:[{kind:'unit',path:'/objects/0/fields/0/unit',reason:'unit_evidence_not_established',original_value:'合成单位',citations:[citation]},
    {kind:'relationship',path:'/relationships/0',reason:'relationship_evidence_not_established',original_value:relation}],status:'needs_review',confirmed:false,can_apply:false,can_publish:false});
const props=(extra={})=>({account,row,stage:'extract-1',rawHash,Btn,...extra});
const flush=async()=>{await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};

test.before(async()=>{
  tmp=await mkdtemp(join(tmpdir(),'full-candidate-ui-'));
  const entry=fileURLToPath(new URL('../src/FullReadingMachineCandidate.jsx',import.meta.url));
  const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',
    build:{ssr:entry,write:false,minify:false,rollupOptions:{external:['react'],
      output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?new URL('../node_modules/react/index.js',import.meta.url).href:id}}}});
  const file=join(tmp,'component.mjs');await writeFile(file,out.output.find(x=>x.isEntry).code);
  Component=(await import(pathToFileURL(file))).default;({createRoot}=await import('react-dom/client'));
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function withDOM(run){
 const dom=new JSDOM('<div id="app"></div>',{url:'http://localhost/'}),names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const before=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const key of names.slice(0,5))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value:dom.window[key]});
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;const root=createRoot(document.getElementById('app'));
 const render=async p=>{await act(async()=>{root.render(React.createElement(Component,p));await flush();});};
 const click=async label=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(label));assert.ok(b);await act(async()=>{b.click();await flush();});};
 try{await run({render,click,root});}finally{await act(async()=>root.unmount());dom.window.close();for(const k of names){if(before[k])Object.defineProperty(globalThis,k,before[k]);else delete globalThis[k];}}
}

test('按需读取并展示待核单位、关系、原文与问题，不写存储、不发布',async()=>withDOM(async({render,click})=>{
 const calls=[];const request=async(path,options)=>{calls.push({path,options});return reply();};
 await render(props({request}));assert.equal(calls.length,0);await click('生成机器待核候选');
 assert.equal(calls.length,1);assert.match(calls[0].path,/failed-candidate\?revision=5$/);assert.equal(calls[0].options.cache,'no-store');
 assert.equal(calls[0].options.headers['X-Workspace-ID'],account.workspace_id);
 for(const value of ['合成单位','合成关系','合成原文依据','完整来源段落','请核对合成条件','未人工确认','不会确认、应用或发布'])assert.match(document.body.textContent,new RegExp(value));
 assert.match(document.body.textContent,/仅为第 1 批抽取的候选，并非整份报告结果/);assert.match(document.body.textContent,/仅为展示清单，先后与条件需对照原文核实/);
 assert.equal(document.querySelectorAll('ol').length,0);
 assert.equal([...document.querySelectorAll('button')].filter(b=>/应用|发布|确认/.test(b.textContent)).length,0);
 assert.equal(localStorage.length,0);assert.equal(sessionStorage.length,0);
}));

test('后端允许的 120 字步骤名称完整展示，不隐式缩短',async()=>withDOM(async({render,click})=>{
 const v=reply(), name='步'.repeat(120);v.candidate.businesses[0].steps[0].name=name;sign(v);
 await render(props({request:async()=>v}));await click('生成机器待核候选');assert.ok(document.body.textContent.includes(name));
}));

test('不在本批的引用不得冒充原文匹配，模型文字只作文本',async()=>withDOM(async({render,click})=>{
 const v=reply();v.quarantine[0].citations=[{source_ref:'w:p2',quote:'<img src=x onerror=alert(1)>'}];sign(v);
 await render(props({request:async()=>v}));await click('生成机器待核候选');
 assert.match(document.body.textContent,/该引用不在本批冻结来源中/);assert.match(document.body.textContent,/<img src=x/);
 assert.equal(document.querySelectorAll('img').length,0);
}));

test('读取错误支持重试且不展示内部错误信息',async()=>withDOM(async({render,click})=>{
 let count=0;const request=async()=>{if(++count===1){const e=Error('synthetic internal detail');e.status=403;throw e;}return reply();};
 await render(props({request}));await click('生成机器待核候选');assert.match(document.body.textContent,/当前账号无法读取/);assert.doesNotMatch(document.body.textContent,/synthetic internal detail/);
 await click('重试生成机器待核候选');assert.equal(count,2);assert.match(document.body.textContent,/合成单位/);
}));

test('候选处理预算不足时保留明确失败，重试不伪造已生成结果',async()=>withDOM(async({render,click})=>{
 const calls=[];const request=async(path,options)=>{calls.push({path,options});const error=Error('synthetic private detail');error.status=409;error.detail={code:'full_candidate_preview_budget'};throw error;};
 await render(props({request}));await click('生成机器待核候选');
 assert.match(document.querySelector('[role="alert"]').textContent,/来源过多或文字重复，暂无法安全生成预览/);
 assert.match(document.body.textContent,/原回答和费用记录保留，可先查看原回答/);
 assert.doesNotMatch(document.body.textContent,/synthetic private detail|候选领域：|合成单位|合成关系/);
 assert.equal(document.querySelectorAll('[role="status"]').length,0);
 await click('重试生成机器待核候选');assert.equal(calls.length,2);
 assert.match(document.querySelector('[role="alert"]').textContent,/暂无法安全生成预览/);
 assert.equal(document.querySelectorAll('[role="status"]').length,0);
 assert.ok(calls.every(({options})=>!options.method || options.method==='GET'));
 assert.equal(localStorage.length,0);assert.equal(sessionStorage.length,0);
}));

test('不同原答或可应用标记会被拒绝，不能显示候选',async()=>withDOM(async({render,click})=>{
 let v=reply();v.scope.raw_hash='c'.repeat(64);const request=async()=>v;
 await render(props({request}));await click('生成机器待核候选');assert.match(document.body.textContent,/暂不能核对/);assert.doesNotMatch(document.body.textContent,/合成单位/);
 v=reply();v.can_apply=true;await click('重试生成机器待核候选');assert.match(document.body.textContent,/暂不能核对/);assert.doesNotMatch(document.body.textContent,/合成关系/);
 v=reply();v.candidate.domain='篡改后的领域';await click('重试生成机器待核候选');assert.match(document.body.textContent,/暂不能核对/);assert.doesNotMatch(document.body.textContent,/篡改后的领域/);
}));

test('切换阶段、角色和账号后立即隐藏候选，旧请求迟到不能复现',async()=>withDOM(async({render,click})=>{
 const held=deferred();let n=0;const request=async()=>++n===1?reply():held.promise;
 await render(props({request}));await click('生成机器待核候选');assert.match(document.body.textContent,/合成单位/);
 await render(props({request,account:{...account,role:'viewer'}}));assert.doesNotMatch(document.body.textContent,/合成单位/);
 await click('生成机器待核候选');await render(props({request,account:{...account,user_id:'other'}}));
 await act(async()=>{held.resolve(reply());await flush();});assert.doesNotMatch(document.body.textContent,/合成单位|合成关系/);
 await render(props({request}));assert.doesNotMatch(document.body.textContent,/合成单位|合成关系/);
}));
