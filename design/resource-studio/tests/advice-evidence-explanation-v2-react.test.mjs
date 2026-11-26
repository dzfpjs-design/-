import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

const capability={kind:'capability_declaration',source_scope:'provided_excerpt',observed_at:null,freshness:'not_applicable',source_types:['capability','packet','PRIVATE-TYPE'],source_id:'PRIVATE-ID',source_text:'PRIVATE-TEXT'};
const observation={kind:'observation',source_scope:'provided_source_text',observed_at:'2026-11-25T00:00:00Z',freshness:'current',source_types:['observation']};
const evidence=(overrides={})=>({capability_count:1,observation_count:0,other_count:0,observation_state:'not_cited',partial_source_count:1,items:[capability],...overrides});

test('evidence display stays truthful across disabled, uncertainty, paging and account changes',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');
  const {default:Panel}=await server.ssrLoadModule('/src/AdviceEvidenceExplanationV2.jsx');
  root=createRoot(document.getElementById('root'));
  let enabled=false,items=[],cursor=null,fail=false,failPage=false,calls=[];
  const request=async(path)=>{
   calls.push(path);if(fail)throw Error('PRIVATE-ERROR');
   if(path.includes('?cursor=')){
    if(failPage)throw Error('PRIVATE-PAGE-ERROR');
    return {enabled:true,items:[{state:'ready',evidence:evidence({partial_source_count:0})}],next_cursor:null};
   }
   return {enabled,items,next_cursor:cursor};
  };
  const account={workspace_id:'work',user_id:'user',role:'operator'};
  const render=(id='task',who=account)=>React.createElement(Panel,{account:who,task:{id},requestJSON:request});
  await act(async()=>root.render(render()));
  assert.equal(document.body.textContent,'');assert.match(calls[0],/\/workspace\/tasks\/task\/business-advice\/evidence-explanation$/);
  enabled=true;items=[{state:'ready',evidence:evidence()},{state:'needs_review',evidence:{capability_count:999,source_text:'PRIVATE-TEXT'}}];
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/已登记能力1 条/);assert.match(document.body.textContent,/现场观测0 条/);
  assert.match(document.body.textContent,/未引用现场观测，设备状态待核/);
  assert.match(document.body.textContent,/包含资料摘录，尚不能代表全文/);
  assert.match(document.body.textContent,/来源：能力资料、资料包、来源待核/);
  assert.match(document.body.textContent,/依据待核实/);
  assert.doesNotMatch(document.body.textContent,/PRIVATE-ID|PRIVATE-TEXT|PRIVATE-TYPE|999/);
  assert.doesNotMatch(document.body.textContent,/校验|内部|实验|候选|Schema|账本/);
  items=[{state:'ready',evidence:evidence({capability_count:0,observation_count:1,observation_state:'current',partial_source_count:0,items:[observation]})}];
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/已引用现场观测，设备当前状态仍需确认/);
  assert.doesNotMatch(document.body.textContent,/设备在线|现场已确认/);
  assert.match(document.body.textContent,/北京时间/);
  assert.match(document.body.textContent,/来源：观测记录/);
  items=[{state:'ready',evidence:evidence({capability_count:0,observation_count:1,observation_state:'unknown',partial_source_count:0,
   items:[{...observation,observed_at:null,observed_start:'2026-11-24T00:00:00Z',observed_end:'2026-11-25T00:00:00Z',freshness:'unknown'}]})}];
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/观测时效待核/);assert.match(document.body.textContent,/至/);
  assert.doesNotMatch(document.body.textContent,/已过期/);
  items=[{state:'ready',evidence:evidence({capability_count:0,observation_count:1,observation_state:'stale',partial_source_count:0,
   items:[{...observation,freshness:'stale'}]})}];
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/已过期/);
  items=[{state:'ready',evidence:evidence({capability_count:0,observation_count:1,observation_state:'current',partial_source_count:0,
   items:[{...observation,observed_at:'2026-02-30T00:00:00Z'}]})}];
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/依据待核实/);assert.doesNotMatch(document.body.textContent,/有效期内/);
  items=[{state:'ready',evidence:evidence({capability_count:99})}];
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/依据待核实/);assert.doesNotMatch(document.body.textContent,/99 条/);
  items=[{state:'ready',evidence:evidence()}];cursor='PRIVATE-CURSOR';
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  failPage=true;
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='加载更多建议').click());
  assert.match(document.body.textContent,/加载更多依据失败/);assert.equal(document.querySelectorAll('article').length,1);
  assert.doesNotMatch(document.body.textContent,/PRIVATE-PAGE-ERROR/);failPage=false;
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='加载更多建议').click());
  assert.equal(document.querySelectorAll('article').length,2);assert.doesNotMatch(document.body.textContent,/PRIVATE-CURSOR/);
  fail=true;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/暂时无法读取建议依据/);assert.doesNotMatch(document.body.textContent,/PRIVATE-ERROR|暂无建议依据/);
  fail=false;cursor=null;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/未引用现场观测/);
  enabled=false;await act(async()=>root.render(render('other-task',{...account,workspace_id:'another'})));
  assert.equal(document.body.textContent,'');
  let resolveOld;const delayed=path=>path.includes('/old-task/')?new Promise(resolve=>{resolveOld=resolve;}):Promise.resolve({enabled:false,items:[]});
  const late=(id,who=account)=>React.createElement(Panel,{account:who,task:{id},requestJSON:delayed});
  await act(async()=>root.render(late('old-task')));
  await act(async()=>root.render(late('new-task')));
  await act(async()=>resolveOld({enabled:true,items:[{state:'ready',evidence:evidence()}]}));
  assert.equal(document.body.textContent,'');
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
