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
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'}),keys=['window','document','navigator','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.sessionStorage=dom.window.sessionStorage;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{const {createRoot}=await import('react-dom/client'),{default:Actual}=await server.ssrLoadModule('/src/ReportBusinessCandidates.jsx');
  const Dialog=({title,onClose,children})=>React.createElement('div',{role:'dialog','aria-label':title},React.createElement('button',{onClick:onClose},'关闭'),children);
  const Component=props=>React.createElement(Actual,{Dialog,...props});root=createRoot(document.getElementById('root'));await run({Component,root,dom});}
 finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
const Btn=({children,primary,...props})=>React.createElement('button',props,children);
const button=label=>[...document.querySelectorAll('button')].find(item=>item.textContent===label);

test('one candidate can be reviewed and saved without overwriting or confirming it',()=>environment(async({Component,root})=>{
 let calls=0,saved,selected;const initial={version:1,businesses:[],flows:{},businessCandidates:bundle,materials:[material],resources:[],connections:[]};
 const request=async(path,options)=>{calls++;assert.equal(path,'/setup/candidates/preview');assert.equal(JSON.parse(options.body).material.hash,material.hash);return response;};
 function Harness(){const [workspace,setWorkspace]=useState(initial);return React.createElement(Component,{bundle:workspace.businessCandidates,workspace,liveWorkspace:()=>workspace,materials:[material],materialReadStatus:'ready',canManage:true,Btn,request,onCommit:value=>{saved=value;setWorkspace(value);return true;},onSelectBusiness:value=>{selected=value;}});}
 await act(async()=>root.render(React.createElement(Harness)));assert.equal(calls,0);
 await act(async()=>button('查看候选').click());assert.equal(calls,1);assert.ok(document.querySelector('[role=dialog]'));
 assert.doesNotMatch(document.body.textContent,/private-id|这段更长的内部原句|aaaaaaa/);
 await act(async()=>button('加入当前业务').click());assert.equal(selected,'森林巡护');assert.equal(saved.flows['森林巡护'].confirmedSignature,null);
 assert.deepEqual(saved.flows['森林巡护'].steps[0].candidateUnresolvedSources,[unresolved]);assert.equal(document.querySelector('[role=dialog]'),null);assert.match(document.body.textContent,/已加入 1 项/);
}));

test('stale material cannot be used and changed server preview cannot be added',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle};let calls=0,saves=0;
 const props={bundle,workspace:initial,materials:[{...material,version:3}],materialReadStatus:'ready',canManage:true,Btn,onCommit:()=>{saves++;return true;},request:async()=>{calls++;return response;}};
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('查看候选').click());assert.match(document.body.textContent,/资料状态待核对/);assert.equal(calls,0);
 await act(async()=>root.render(React.createElement(Component,{...props,materials:[material],request:async()=>({...response,candidates:[{...response.candidates[0],name:'不一致的业务'}]})})));
 await act(async()=>button('读取候选').click());assert.match(document.body.textContent,/重新核对/);assert.equal(saves,0);assert.equal(button('加入当前业务'),undefined);
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

test('edited content skipped by the server is not presented as fully verified',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},saved={revision:5,configuration:initial};
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,account:{user_id:'u',workspace_id:'w'},Btn,
  prepareDraft:async()=>4,onAdopt:async()=>{},onCommit:()=>true,request:async()=>({draft:saved,preview:{...response,material:{...material,status:'parsed'}},recheck:{skipped:1}})};
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('重新核对依据').click());
 assert.match(document.body.textContent,/部分已修改内容未自动核验/);assert.doesNotMatch(document.body.textContent,/依据已全部核对/);
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
 await act(async()=>root.render(React.createElement(Component,props)));assert.equal(button('查看候选').disabled,false);assert.equal(button('重新核对依据').disabled,false);
 await act(async()=>button('重新核对依据').click());assert.equal(requests,1);assert.match(document.body.textContent,/结果已保存，请刷新后查看/);
}));

test('switching workspace hides a prior preview even when candidate contents are identical',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle};let saves=0;
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,onCommit:()=>{saves++;return true;},request:async()=>response};
 await act(async()=>root.render(React.createElement(Component,{...props,account:{user_id:'u',workspace_id:'one',role:'admin'}})));
 await act(async()=>button('查看候选').click());assert.match(document.body.textContent,/森林巡护/);
 await act(async()=>root.render(React.createElement(Component,{...props,account:{user_id:'u',workspace_id:'two',role:'admin'}})));
 assert.doesNotMatch(document.body.textContent,/森林巡护|内部原句/);assert.equal(button('加入当前业务'),undefined);assert.equal(saves,0);
}));

test('late preview and recheck responses cannot appear or be adopted after account switch',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},old={user_id:'a',workspace_id:'one',role:'admin'},other={user_id:'b',workspace_id:'one',role:'admin'};
 let releasePreview,releaseRecheck,adopted=0;const slowPreview=new Promise(resolve=>{releasePreview=resolve;}),slowRecheck=new Promise(resolve=>{releaseRecheck=resolve;});
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,prepareDraft:async()=>4,onAdopt:async()=>{adopted++;},onCommit:()=>true,
  request:(path)=>path.endsWith('/preview')?slowPreview:slowRecheck};
 await act(async()=>root.render(React.createElement(Component,{...props,account:old})));
 await act(async()=>button('查看候选').click());await act(async()=>root.render(React.createElement(Component,{...props,account:other})));
 await act(async()=>{releasePreview(response);await Promise.resolve();});assert.doesNotMatch(document.body.textContent,/森林巡护|内部原句/);
 await act(async()=>button('重新核对依据').click());await act(async()=>root.render(React.createElement(Component,{...props,account:old})));
 await act(async()=>{releaseRecheck({draft:{revision:5,configuration:initial},preview:response});await Promise.resolve();});assert.equal(adopted,0);
}));

test('a new session for the same account invalidates the old preview and late response',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},account={user_id:'u',username:'name',workspace_id:'w',role:'admin'};let release,started=false;
 const pending=new Promise(resolve=>{release=resolve;}),props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,onCommit:()=>true,account,request:async()=>{started=true;return pending;}};
 sessionStorage.setItem('ga-setup-csrf','first-session');await act(async()=>root.render(React.createElement(Component,props)));
 await act(async()=>button('查看候选').click());assert.equal(started,true);sessionStorage.setItem('ga-setup-csrf','second-session');
 await act(async()=>root.render(React.createElement(Component,{...props})));await act(async()=>{release(response);await Promise.resolve();});
 assert.doesNotMatch(document.body.textContent,/森林巡护|内部原句/);assert.equal(button('加入当前业务'),undefined);
}));

test('a changed session cannot apply an old preview even before rerender',()=>environment(async({Component,root,dom})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},account={user_id:'u',username:'name',workspace_id:'w',role:'admin'};let saves=0;
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,onCommit:()=>{saves++;return true;},account,request:async()=>response};
 sessionStorage.setItem('ga-setup-csrf','first-session');await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('查看候选').click());
 sessionStorage.setItem('ga-setup-csrf','second-session');await act(async()=>button('加入当前业务').click());assert.equal(saves,0);
 await act(async()=>dom.window.dispatchEvent(new dom.window.Event('focus')));assert.doesNotMatch(document.body.textContent,/森林巡护|内部原句/);
}));

test('source recheck refreshes material status after adopting a changed candidate revision',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},updatedBundle={...bundle,material:{...material,version:3}};
 const saved={revision:5,configuration:{...initial,businessCandidates:updatedBundle}},account={user_id:'u',username:'name',workspace_id:'w',role:'admin'};
 let release,started,refreshes=0;const pending=new Promise(resolve=>{release=resolve;}),adopting=new Promise(resolve=>{started=resolve;});
 function Harness(){const [workspace,setWorkspace]=useState(initial),[rows,setRows]=useState([material]);
  return React.createElement(Component,{bundle:workspace.businessCandidates,workspace,materials:rows,materialReadStatus:'ready',canManage:true,account,Btn,
   prepareDraft:async()=>4,onAdopt:async()=>{setWorkspace(saved.configuration);started();await pending;},onRefreshMaterials:async()=>{refreshes++;setRows([{...material,version:3}]);},
   onCommit:()=>true,request:async()=>({draft:saved,preview:{...response,material:{...material,version:3}},recheck:{skipped:1}})});}
 await act(async()=>root.render(React.createElement(Harness)));await act(async()=>button('重新核对依据').click());await adopting;
 assert.equal(button('查看候选').disabled,false);await act(async()=>{release();await Promise.resolve();});
 assert.equal(refreshes,1);assert.equal(button('查看候选').disabled,false);assert.match(document.body.textContent,/部分已修改内容未自动核验/);
}));

test('an adopted recheck never refreshes another session even after an account ABA switch',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},saved={revision:5,configuration:initial};
 const one={user_id:'u1',username:'one',workspace_id:'w',role:'admin'},two={user_id:'u2',username:'two',workspace_id:'w',role:'admin'};
 let release,started,refreshes=0;const pending=new Promise(resolve=>{release=resolve;}),adopting=new Promise(resolve=>{started=resolve;});
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,prepareDraft:async()=>4,
  onAdopt:async()=>{started();await pending;},onRefreshMaterials:async()=>{refreshes++;},onCommit:()=>true,request:async()=>({draft:saved,preview:response})};
 await act(async()=>root.render(React.createElement(Component,{...props,account:one})));await act(async()=>button('重新核对依据').click());await adopting;
 await act(async()=>root.render(React.createElement(Component,{...props,account:two})));await act(async()=>root.render(React.createElement(Component,{...props,account:one})));
 await act(async()=>{release();await Promise.resolve();});assert.equal(refreshes,0);
}));

test('an unmounted source recheck does not refresh its former workspace',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle},saved={revision:5,configuration:initial},account={user_id:'u',workspace_id:'w',role:'admin'};
 let release,started,refreshes=0;const pending=new Promise(resolve=>{release=resolve;}),adopting=new Promise(resolve=>{started=resolve;});
 const props={bundle,workspace:initial,materials:[material],materialReadStatus:'ready',canManage:true,account,Btn,prepareDraft:async()=>4,
  onAdopt:async()=>{started();await pending;},onRefreshMaterials:async()=>{refreshes++;},onCommit:()=>true,request:async()=>({draft:saved,preview:response})};
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('重新核对依据').click());await adopting;
 await act(async()=>root.render(null));await act(async()=>{release();await Promise.resolve();});assert.equal(refreshes,0);
}));

test('bulk add skips an existing business and saves all remaining candidates in one commit',()=>environment(async({Component,root})=>{
 const second={...business,key:'k2',name:'防火核查'},nextBundle={...bundle,candidates:[business,second]};
 const secondPreview={...response.candidates[0],key:'k2',name:'防火核查'},nextPreview={...response,candidates:[response.candidates[0],secondPreview]};
 const originalFlow={trigger:'已有流程',output:'既有记录',steps:[]},initial={businesses:['森林巡护'],flows:{'森林巡护':originalFlow},businessCandidates:nextBundle};let saves=0,saved,selected;
 function Harness(){const [workspace,setWorkspace]=useState(initial);return React.createElement(Component,{bundle:workspace.businessCandidates,workspace,liveWorkspace:()=>workspace,materials:[material],materialReadStatus:'ready',canManage:true,Btn,request:async()=>nextPreview,onCommit:value=>{saves++;saved=value;setWorkspace(value);return true;},onSelectBusiness:value=>{selected=value;}});}
 await act(async()=>root.render(React.createElement(Harness)));await act(async()=>button('查看候选').click());assert.match(document.body.textContent,/1 项已在业务清单，将跳过/);
 const choice=[...document.querySelectorAll('nav button')].find(item=>item.textContent.includes('防火核查'));await act(async()=>choice.click());assert.equal(choice.getAttribute('aria-pressed'),'true');
 await act(async()=>button('全部加入业务清单').click());assert.equal(saves,1);assert.equal(selected,'防火核查');assert.equal(saved.flows['森林巡护'].trigger,'已有流程');
 assert.equal(saved.flows['防火核查'].confirmedSignature,null);assert.deepEqual(saved.flows['防火核查'].steps[0].candidateUnresolvedSources,[unresolved]);assert.equal(document.querySelector('[role=dialog]'),null);
 assert.match(document.body.textContent,/已加入 1 项，跳过 1 项已有业务/);
}));

test('bulk save refusal leaves the dialog and original draft intact',()=>environment(async({Component,root})=>{
 const initial={businesses:[],flows:{},businessCandidates:bundle};let attempts=0;
 const props={bundle,workspace:initial,liveWorkspace:()=>initial,materials:[material],materialReadStatus:'ready',canManage:true,Btn,request:async()=>response,onCommit:()=>{attempts++;return false;}};
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>button('查看候选').click());await act(async()=>button('全部加入业务清单').click());
 assert.equal(attempts,1);assert.deepEqual(initial.businesses,[]);assert.ok(document.querySelector('[role=dialog]'));assert.match(document.body.textContent,/保存未完成/);
}));
