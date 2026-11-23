import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('展示固定的失败原因，旧记录或未知代码安全回退', async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
 const built=await build({entryPoints:['src/NativeSensorAnalysis.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
 const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const root=createRoot(document.getElementById('root')),view=module.exports.FailureReason;
 try {
  await act(async()=>root.render(React.createElement(view,{analysis:{state:'response_invalid',failure_reason:{code:'response_truncated',message:'SECRET'}}})));
  assert.match(document.body.textContent,/输出长度限制而未完成/);assert.match(document.body.textContent,/不会自动重发/);assert.doesNotMatch(document.body.textContent,/SECRET/);
  await act(async()=>root.render(React.createElement(view,{analysis:{state:'response_invalid'}})));
  assert.match(document.body.textContent,/暂时无法确定失败原因/);
  await act(async()=>root.render(React.createElement(view,{analysis:{state:'response_invalid',failure_reason:{code:'SECRET'}}})));
  assert.match(document.body.textContent,/暂时无法确定失败原因/);assert.doesNotMatch(document.body.textContent,/SECRET/);
  await act(async()=>root.render(React.createElement(view,{analysis:{state:'completed'}})));
  assert.equal(document.body.textContent,'');
 } finally {await act(async()=>root.unmount());dom.window.close()}
});

test('用户在分析历史和详情都能看到原因且无需再次提交',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
 const built=await build({entryPoints:['src/NativeSensorAnalysis.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const record={id:'analysis',request_id:'request',state:'response_invalid',created_at:'2026-11-14T08:00:00Z',failure_reason:{code:'content_not_json',message:'SECRET'}};
 let postCount=0;const request=async(path,options)=>{if(options?.method==='POST')postCount++;
  if(path.endsWith('/context'))return {cloud_analysis_supported:true,read_only:false,models:[],analyses:[]};
  if(path.includes('/packets?page='))return {packets:[],page:1,has_more:false};
  if(path.includes('/analyses/history?page='))return {analyses:[record],page:1,has_more:false};
  if(path.endsWith('/analyses/analysis'))return {analysis:record};
  throw Error('unexpected request');
 };
 const root=createRoot(document.getElementById('root')),Btn=({children,...p})=>React.createElement('button',p,children),wait=()=>new Promise(r=>setTimeout(r,0));
 try{
  await act(async()=>{root.render(React.createElement(module.exports.default,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'task'},Btn,request}));await wait()});
  await act(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent==='查看设备数据分析').click();await wait()});
  assert.match(document.body.textContent,/模型回答内容不是可解析的 JSON/);
  await act(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent==='查看原分析').click();await wait()});
  assert.equal((document.body.textContent.match(/模型回答内容不是可解析的 JSON/g)||[]).length,2);
  assert.doesNotMatch(document.body.textContent,/SECRET/);assert.equal(postCount,0);
 }finally{await act(async()=>root.unmount());dom.window.close()}
});
