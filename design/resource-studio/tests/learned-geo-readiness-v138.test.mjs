// Actual paired-workbench component/ReactDOM/StrictMode and runtime-api.
// Only catalogue/list HTTP data are synthetic. No real model, installation,
// inference, backend authority, browser acceptance or durable DB is claimed.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'learned-geo-readiness-v138-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/LearnedGeoPairs.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(value=>value.type==='chunk'&&value.isEntry);assert.ok(output,'build actual paired-workbench component');const file=join(temporary,'component.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const account={user_id:'13800000-0000-4000-8000-000000000001',workspace_id:'13800000-0000-4000-8000-000000000002',username:'synthetic-model-admin',role:'admin'};
const hash='a'.repeat(64),pipeline='b'.repeat(64),copy=value=>JSON.parse(JSON.stringify(value));
function catalog({prepared=true,installation=null}={}){return {pack_id:'changer-r18-binary-v1',model_recipe_hash:hash,pipeline_hash:pipeline,title:'本地影像变化候选识别',backend:'cpu',default_deployment:'local',cloud_fallback:false,domain_quality_verified:false,business_registration_enabled:true,prepared_available:prepared,runtime_available:true,installations:installation?[installation]:[]};}
function installed(smoke){return {id:'13800000-0000-4000-8000-000000000003',version:3,status:'ready',generation:1,source:'prepared_local',backend:'cpu',deployment:'local',recipe_hash:hash,pipeline_hash:pipeline,stage:'smoke_check',domain_evaluation:'pending',attempts:[],error:null,progress:{},job_id:'13800000-0000-4000-8000-000000000004',artifact_hashes:{'changer-r18.safetensors':'c'.repeat(64)},paired_workflow_registration:'pending',smoke};}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in this synthetic DOM test');}}
async function harness(value,run){
 const errors=[],unexpected=[],calls=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://learned-geo-readiness.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole});
 const session=dom.window.sessionStorage;session.setItem('ga-setup-csrf','synthetic-readiness-token');session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const fetch=async(raw,options={})=>{
  const path=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:path.pathname});
  try{
   assert.equal(path.origin,dom.window.location.origin);assert.equal(path.search,'');assert.equal(method,'GET','readiness display never sends an installation or inference request');
   const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-readiness-token');
   if(path.pathname==='/api/geo/learned-pairs/catalog')return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
   if(path.pathname==='/api/geo/learned-pairs')return new Response('[]',{headers:{'Content-Type':'application/json'}});
   throw Error('Unexpected actual component HTTP route '+path.pathname);
  }catch(error){unexpected.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:session,fetch,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const [key,item]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value:item});dom.window.fetch=fetch;
 let root;
 try{
  // Load the installed renderer before Vite.build can supply a default
  // NODE_ENV; never mix React development act with a production renderer.
  const renderer=await import('react-dom/client'),actual=await component();assert.equal(actual.BundledReact,React);assert.equal(renderer.version,React.version);
  actual.setSession('synthetic-readiness-token',account.workspace_id,account.user_id,account.username);
  const container=dom.window.document.getElementById('root');root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Panel,{account,scenes:[],Btn:Button}))));
  for(let turn=0;turn<20;turn++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});
  assert.ok(calls.some(call=>call.path==='/api/geo/learned-pairs/catalog'),'actual catalogue was read');assert.ok(calls.some(call=>call.path==='/api/geo/learned-pairs'),'actual saved-analysis list was read');
  const button=label=>[...container.querySelectorAll('button')].find(item=>item.textContent===label);
  await run({container,calls,button});
  assert.equal(calls.some(call=>call.method!=='GET'),false);assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
}

test('actual paired page describes a present prepared file as awaiting install verification',()=>harness(catalog(),async h=>{
 assert.ok(h.container.textContent.includes('已发现本地准备文件'));assert.ok(h.container.textContent.includes('文件摘要、转换回执及模型加载将在安装时核验'));assert.ok(h.container.textContent.includes('文件存在不代表核验通过'));assert.ok(h.container.textContent.includes('尚未验证您的业务领域'));
 assert.equal(h.button('安装并检查本地模型').disabled,false);assert.equal(h.container.textContent.includes('已通过本地工程加载检查'),false);assert.equal(h.container.textContent.includes('生产使用许可待核验'),false,'no nonexistent smoke flag is invented');
}));
test('actual paired page keeps installation blocked when no prepared file is present',()=>harness(catalog({prepared:false}),async h=>{
 assert.ok(h.container.textContent.includes('尚未发现本地准备文件'));assert.ok(h.container.textContent.includes('不会下载或转为云调用'));assert.equal(h.button('安装并检查本地模型').disabled,true);assert.equal(h.container.textContent.includes('文件存在不代表核验通过'),false);
}));
test('actual ready engineering receipt explicitly preserves pending production licence and domain validation',()=>harness(catalog({installation:installed({backend:'cpu',inference_performed:true,cloud_calls:0,probe_kind:'synthetic_identical_RGB_engineering_only',production_license_verified:false,domain_quality_verified:false})}),async h=>{
 assert.ok(h.container.textContent.includes('已通过本地工程加载检查，可生成候选'));assert.ok(h.container.textContent.includes('生产使用许可待核验'));assert.ok(h.container.textContent.includes('本机工程检查不代表生产使用许可通过'));assert.ok(h.container.textContent.includes('尚未验证您的业务领域'));assert.equal(h.button('安装并检查本地模型'),undefined);assert.equal(h.container.textContent.includes('文件存在不代表核验通过'),false);
}));
for(const flag of [undefined,'false'])test('missing or nonboolean original licence flag is never fabricated as verified=false: '+String(flag),()=>{
 const smoke={backend:'cpu',inference_performed:true,...(flag===undefined?{}:{production_license_verified:flag})};
 return harness(catalog({installation:installed(copy(smoke))}),async h=>{assert.ok(h.container.textContent.includes('已通过本地工程加载检查'));assert.equal(h.container.textContent.includes('生产使用许可待核验'),false);assert.doesNotMatch(h.container.textContent,/生产(?:使用)?许可(?:已确认|已核验通过|已通过)/);assert.ok(h.container.textContent.includes('尚未验证您的业务领域'));});
});
