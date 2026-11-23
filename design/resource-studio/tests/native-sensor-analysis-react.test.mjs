import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {randomUUID} from 'node:crypto';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('设备数据分析需核对全文与费用，响应丢失后只核对原请求',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_key,_opts,fn)=>fn({})}});
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),built=await build({entryPoints:['src/NativeSensorAnalysis.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const Component=module.exports.default,Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'w',user_id:'u',role:'admin'},task={id:'task'};
 let original=null,posted=0,analysis=null,contextUnavailable=false,uncertain=false;const packet={id:'packet',created_at:'2026-11-14T08:00:00Z',records:[{kind:'data',id:'r'}]},model={id:'model',name:'DeepSeek',provider:'deepseek',model:'deepseek-v4-pro',version:1,price_available:false,max_output_tokens:1024,price:null};
 const request=async(path,opts={})=>{
  if(path.endsWith('/context')){if(contextUnavailable){const e=Error('当前账号仅能查看历史');e.status=403;throw e}return {cloud_analysis_supported:true,read_only:false,models:[model],analyses:analysis?[{id:'a',request_id:original.request_id,state:'completed',packet_id:'packet',model_id:'model',created_at:packet.created_at}]:[]}}
  if(path.includes('/analyses/history?page='))return {page:1,has_more:false,analyses:analysis?[{id:'a',request_id:original.request_id,state:'completed',packet_id:'packet',model_id:'model',created_at:packet.created_at}]:[]};
  if(path.includes('/packets?page='))return {packets:[packet],page:1,has_more:false};
  if(path.endsWith('/preview')){assert.equal(Object.hasOwn(JSON.parse(opts.body),'max_cost_fen'),false);return {preview_id:'preview',preview_hash:'a'.repeat(64),model,messages:[{role:'user',content:'设备读数：25；来源未核实。'}],options:{model:'deepseek-v4-pro',max_tokens:512},output_schema:{type:'object'},quote:{schema:'sensor-native-unlimited-quote-v2',currency:'CNY',maximum_cost_fen:null,user_limit_fen:null,price_expires_at:null,provider_price_locked:false,output_token_bound:512,input_rate_fen_per_million:null,output_rate_fen_per_million:null},requires_uncertainty_ack:uncertain,uncertain_request_ids:uncertain?['old']:[],source_policy_notice:'来源云策略未设置，本次须单独确认。',source_policies:[{ref:'R1',status:'未设置'}]}}
  if(path.endsWith('/send')){posted++;original=JSON.parse(opts.body);analysis={id:'a',request_id:original.request_id,state:'completed',packet_id:'packet',model_id:'model',result:{summary:'请人工核查',observations:[],suggested_checks:['检查设备']},cost_status:{currency:'CNY',maximum_cost_fen:1,actual_cost_fen:null,explanation:'实际费用待核对'}};throw Error('响应丢失')}
  if(path.includes('/requests/'))return {state:'completed',analysis};if(path.endsWith('/a'))return {analysis};throw Error(path);
 };
 let root=createRoot(document.getElementById('root'));const wait=()=>new Promise(r=>setTimeout(r,0));
 const render=()=>act(async()=>{root.render(React.createElement(Component,{account,task,Btn,request}));await wait()});
 const button=label=>[...document.querySelectorAll('button')].find(b=>b.textContent===label);
 const click=async label=>{assert.ok(button(label),label);await act(async()=>{button(label).click();await wait()})};
 const choose=async(label,value)=>{const select=[...document.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('select');await act(async()=>{select.value=value;select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await wait()})};
 try{
  await render();await click('查看设备数据分析');await choose('已保存设备证据包','packet');await choose('DeepSeek 模型','model');await click('核对发送内容和费用');
  assert.match(document.body.textContent,/设备读数：25；来源未核实/);assert.match(document.body.textContent,/费用暂无法估算/);assert.equal(button('确认并开始一次分析').disabled,true);
  await act(async()=>{[...document.querySelectorAll('label')].find(x=>x.textContent.includes('我已核对完整发送内容')).querySelector('input').click();await wait()});await click('确认并开始一次分析');assert.equal(posted,1);assert.match(document.body.textContent,/尚待核对/);
  contextUnavailable=true;account.role='viewer';await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备数据分析');await click('核对原分析');
  assert.equal(posted,1);assert.match(document.body.textContent,/请人工核查/);assert.match(document.body.textContent,/实际费用：待核对/);assert.match(document.body.textContent,/仅可查看已保存分析/);assert.equal(localStorage.getItem('sensor-analysis:w:u:task:pending'),null);
  contextUnavailable=false;account.role='admin';uncertain=true;await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备数据分析');await click('核对发送内容和费用');
  const risk=[...document.querySelectorAll('label')].find(x=>x.textContent.includes('理解本次可能重复发送')).querySelector('input'),consent=[...document.querySelectorAll('label')].find(x=>x.textContent.includes('我已核对完整发送内容')).querySelector('input');await act(async()=>{consent.click();await wait()});assert.equal(button('确认并开始一次分析').disabled,true);
  await act(async()=>{risk.click();await wait()});await click('确认并开始一次分析');assert.equal(posted,2);assert.equal(original.acknowledge_prior_uncertain,true);
 }finally{await act(async()=>root.unmount());dom.window.close()}
});

test('价格登记待核时仍能分析并按原编号恢复登记',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_key,_opts,fn)=>fn({})}});
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),built=await build({entryPoints:['src/NativeSensorAnalysis.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const Component=module.exports.default,Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'w',user_id:'u',role:'admin'},task={id:'task'};
 const model={id:'model',name:'DeepSeek',provider:'deepseek',model:'deepseek-v4-pro',version:1,price_available:false,max_output_tokens:1024};let calls=[],analyses=0;
 const request=async(path,opts={})=>{
  if(path.endsWith('/context'))return {cloud_analysis_supported:true,read_only:false,models:[model],analyses:[]};
  if(path.includes('/analyses/history?page='))return {analyses:[],page:1,has_more:false};
  if(path.includes('/packets?page='))return {packets:[{id:'packet',created_at:'2026-11-14T08:00:00Z',records:[{kind:'data',id:'r'}]}],page:1,has_more:false};
  if(path.endsWith('/preview'))return {preview_id:'preview',preview_hash:'a'.repeat(64),model,messages:[{role:'user',content:'设备读数'}],options:{model:'deepseek-v4-pro',max_tokens:512},output_schema:{type:'object'},quote:{schema:'sensor-native-unlimited-quote-v2',currency:'CNY',maximum_cost_fen:null,user_limit_fen:null,price_expires_at:null,provider_price_locked:false,output_token_bound:512,input_rate_fen_per_million:null,output_rate_fen_per_million:null},requires_uncertainty_ack:false,uncertain_request_ids:[],source_policy_notice:'来源待核对',source_policies:[{ref:'R1',status:'未设置'}]};
  if(path.endsWith('/send')){analyses++;const v=JSON.parse(opts.body);return {state:'completed',analysis:{id:'a',request_id:v.request_id,packet_id:'packet',model_id:'model',state:'completed',result:null}}}
  if(path.endsWith('/price')){calls.push(JSON.parse(opts.body));if(calls.length===1)throw Error('响应丢失');return {model_id:'model',source:'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',checked_at:'2026-11-14T08:00:00Z',expires_at:'2099-01-01T00:00:00Z',currency:'CNY',input_rate_fen_per_million:200,output_rate_fen_per_million:400,provider_price_locked:false}}
  throw Error('意外模型调用 '+path);
 };
 let root=createRoot(document.getElementById('root'));const wait=()=>new Promise(r=>setTimeout(r,0));const render=()=>act(async()=>{root.render(React.createElement(Component,{account,task,Btn,request}));await wait()});
 const button=label=>[...document.querySelectorAll('button')].find(b=>b.textContent===label);const click=async label=>{assert.ok(button(label),label);await act(async()=>{button(label).click();await wait()})};
 try{
  await render();await click('查看设备数据分析');const labels=[...document.querySelectorAll('label')],select=labels.find(l=>l.textContent.startsWith('核定价格的模型')).querySelector('select'),reason=labels.find(l=>l.textContent.startsWith('核定说明')).querySelector('input');
  await act(async()=>{select.value='model';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(reason,'核对当前官方价格');reason.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await wait()});
  await act(async()=>{[...document.querySelectorAll('label')].find(l=>l.textContent.includes('确认使用所选模型')).querySelector('input').click();await wait()});await click('核定并登记官方价格');assert.equal(calls.length,1);
  const priceRecord=localStorage.getItem('sensor-analysis:w:u:task:price-pending');assert.ok(priceRecord);const choose=async(label,value)=>{const el=[...document.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('select');await act(async()=>{el.value=value;el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await wait()})};await choose('已保存设备证据包','packet');await choose('DeepSeek 模型','model');await click('核对发送内容和费用');assert.match(document.body.textContent,/费用暂无法估算/);await act(async()=>{[...document.querySelectorAll('label')].find(l=>l.textContent.includes('我已核对完整发送内容')).querySelector('input').click();await wait()});await click('确认并开始一次分析');assert.equal(analyses,1);assert.equal(localStorage.getItem('sensor-analysis:w:u:task:price-pending'),priceRecord);await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备数据分析');await click('按原请求核对价格');assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);assert.match(document.body.textContent,/已登记 DeepSeek 的官方价格/);assert.equal(localStorage.getItem('sensor-analysis:w:u:task:price-pending'),null);
 }finally{await act(async()=>root.unmount());dom.window.close()}
});
