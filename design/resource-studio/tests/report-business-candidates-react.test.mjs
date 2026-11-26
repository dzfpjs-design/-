import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useState} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

const material={id:'private-id',hash:'a'.repeat(64),version:2,engine:'native-docx-v1',status:'needs_input'};
const source={source_text:'这段更长的内部原句不会直接显示在页面',quote:'内部原句'};
const business={key:'k1',name:'森林巡护',trigger:'发现异常',output:'核查记录',inference_reason:'建议人工核查',sources:[],steps:[{key:'s1',name:'现场核查',purpose:'核实线索',agent:'人工确认',role:'巡护员',output:'核查结论',condition:'',next_key:'end',else_key:'end',inference_reason:'建议到现场',sources:[source]}]};
const bundle={schema:'setup-business-candidates-v1',origin:'local_summary',material:{...material,status:'parsed'},candidates:[business]};
const unresolved={...source,material_id:material.id,reason:'not_found'};
const response={material,candidates:[{...business,citations:[],unresolved_sources:[],steps:[{...business.steps[0],citations:[],unresolved_sources:[unresolved]}]}]};

async function environment(run){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'}),keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{const {createRoot}=await import('react-dom/client'),{default:Component}=await server.ssrLoadModule('/src/ReportBusinessCandidates.jsx');root=createRoot(document.getElementById('root'));await run({Component,root,dom});}
 finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
const Btn=({children,primary,...props})=>React.createElement('button',props,children);
const button=label=>[...document.querySelectorAll('button')].find(item=>item.textContent===label);

test('one candidate can be reviewed and saved without overwriting or confirming it',()=>environment(async({Component,root})=>{
 let calls=0,saved,selected;const initial={version:1,businesses:[],flows:{},businessCandidates:bundle,materials:[material],resources:[],connections:[]};
 const request=async(path,options)=>{calls++;assert.equal(path,'/setup/candidates/preview');assert.equal(JSON.parse(options.body).material.hash,material.hash);return response;};
 function Harness(){const [workspace,setWorkspace]=useState(initial);return React.createElement(Component,{bundle:workspace.businessCandidates,workspace,liveWorkspace:()=>workspace,materials:[material],materialReadStatus:'ready',canManage:true,Btn,request,onCommit:value=>{saved=value;setWorkspace(value);return true;},onSelectBusiness:value=>{selected=value;}});}
 await act(async()=>root.render(React.createElement(Harness)));assert.equal(calls,0);
 await act(async()=>button('查看候选').click());assert.equal(calls,1);assert.match(document.body.textContent,/资料仍有内容待核/);
 assert.doesNotMatch(document.body.textContent,/private-id|这段更长的内部原句|aaaaaaa/);
 await act(async()=>button('加入业务清单').click());assert.equal(selected,'森林巡护');assert.equal(saved.flows['森林巡护'].confirmedSignature,null);
 assert.deepEqual(saved.flows['森林巡护'].steps[0].candidateUnresolvedSources,[unresolved]);assert.equal(button('已在业务清单').disabled,true);
}));

test('stale material cannot be used and changed server preview cannot be added',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle};let calls=0,saves=0;
 const props={bundle,workspace:initial,materials:[{...material,version:3}],materialReadStatus:'ready',canManage:true,Btn,onCommit:()=>{saves++;return true;},request:async()=>{calls++;return response;}};
 await act(async()=>root.render(React.createElement(Component,props)));assert.equal(button('查看候选').disabled,true);assert.equal(calls,0);
 await act(async()=>root.render(React.createElement(Component,{...props,materials:[material],request:async()=>({...response,candidates:[{...response.candidates[0],name:'不一致的业务'}]})})));
 await act(async()=>button('查看候选').click());assert.match(document.body.textContent,/重新核对/);assert.equal(saves,0);assert.equal(button('加入业务清单'),undefined);
}));

test('explicit source recheck adopts server state and keeps remaining uncertainty visible',()=>environment(async({Component,root})=>{
 const account={user_id:'person',workspace_id:'workspace',role:'admin'},initial={businesses:[],flows:{},businessCandidates:bundle};let adoption,refreshes=0,requestCount=0;
 const saved={revision:5,configuration:{...initial,flows:{'森林巡护':{candidateKey:'k1',candidateUnresolvedSources:[unresolved],steps:[]}}}};
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,account,Btn,
  prepareDraft:async()=>4,onAdopt:async(...args)=>{adoption=args;},onRefreshMaterials:async()=>{refreshes++;},onCommit:()=>true,
  request:async(path,options)=>{requestCount++;assert.equal(path,'/setup/candidates/revalidate');assert.deepEqual(JSON.parse(options.body),{revision:4});return {draft:saved,preview:response};}};
 await act(async()=>root.render(React.createElement(Component,props)));
 await act(async()=>button('重新核对依据').click());assert.equal(requestCount,1);assert.equal(adoption[0],saved);assert.deepEqual(adoption[1],{actor_id:'person',workspace_id:'workspace'});assert.equal(refreshes,1);
 assert.match(document.body.textContent,/部分依据仍待核/);
}));

test('failed source recheck preserves existing work and does not claim success',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle};let saves=0;
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,account:{user_id:'u',workspace_id:'w'},Btn,
  prepareDraft:async()=>4,onAdopt:async()=>{saves++;},onCommit:()=>{saves++;return true;},request:async()=>{throw Object.assign(Error('conflict'),{status:409});}};
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('重新核对依据').click());
 assert.match(document.body.textContent,/配置已变化/);assert.equal(saves,0);assert.deepEqual(initial.businesses,[]);
}));

test('material version changes require explicit recheck and saved-but-unadopted results are not repeated',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},saved={revision:6,configuration:initial};let requests=0;
 const props={bundle,workspace:initial,materials:[{...material,version:3}],materialReadStatus:'ready',canManage:true,account:{user_id:'u',workspace_id:'w'},Btn,
  prepareDraft:async()=>5,onAdopt:async()=>{throw Error('new edits');},onCommit:()=>true,
  request:async(path)=>{requests++;assert.equal(path,'/setup/candidates/revalidate');return {draft:saved,preview:response};}};
 await act(async()=>root.render(React.createElement(Component,props)));assert.equal(button('查看候选').disabled,true);assert.equal(button('重新核对依据').disabled,false);
 await act(async()=>button('重新核对依据').click());assert.equal(requests,1);assert.match(document.body.textContent,/结果已保存，请刷新后查看/);
}));
