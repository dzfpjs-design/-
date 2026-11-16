import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('server imports appear on the domain page without overwriting annotated draft or committing a version',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/?view=domain'}),keys=['window','document','navigator','location','history','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,location:dom.window.location,history:dom.window.history,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/SetupStudio.jsx')).default,{createRoot}=await import('react-dom/client');
  const Btn=({children,primary,ghost,icon,...p})=>React.createElement('button',p,children),Field=({children,label})=>React.createElement('label',null,label,children),Icon=()=>null,Dialog=({children})=>React.createElement('div',null,children);
  const prior={id:'old',name:'旧资料.docx',size:1024,version:1,kind:'需求文档',note:'人工保留的说明',archived:false},archived={id:'archive',name:'归档资料.docx',size:1024,version:1,archived:true};
  const added={id:'new',name:'导入报告.docx',size:2048,version:1,archived:false,status:'parsed'},workspace={instanceProfile:{name:'原实例',domain:'林业',goal:'未保存的说明'},materials:[prior,archived],businesses:[],flows:{}};
  let commits=0;const props={runtime:true,step:0,account:{user_id:'u',workspace_id:'w',role:'admin'},workspace,canonicalMaterials:[prior,archived],onCommit:()=>{commits++;return true},onGo:()=>{},onRegisterGuard:()=>{},Icon,Btn,Field,Dialog};
  root=createRoot(document.getElementById('root'));
  await act(async()=>root.render(React.createElement(Component,props)));
  await act(async()=>root.render(React.createElement(Component,{...props,canonicalMaterials:[{...prior,kind:'其他资料',note:'服务器旧值'},archived,added]})));
  assert.match(document.body.textContent,/导入报告.docx/);assert.match(document.body.textContent,/旧资料.docx/);
  assert.equal([...document.querySelectorAll('input')].find(x=>x.value==='人工保留的说明')?.value,'人工保留的说明');
  assert.equal([...document.querySelectorAll('select')].find(x=>x.value==='需求文档')?.value,'需求文档');
  assert.equal(document.querySelector('textarea')?.value,'未保存的说明');assert.equal(commits,0);
  const archivedBtn=[...document.querySelectorAll('button')].find(x=>x.textContent==='查看已归档');await act(async()=>archivedBtn.click());assert.match(document.body.textContent,/归档资料.docx/);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));
  await act(async()=>root.render(React.createElement(Component,{...props,canonicalMaterials:[prior,archived,added]})));assert.match(document.body.textContent,/导入报告.docx/);assert.equal(commits,0);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a parsed imported original can be selected for an understanding preview without invoking a model',async()=>{
 const {understandingMaterialChoice,assertUnderstandingMaterialSelection}=await import('../src/understanding-materials.mjs');
 const {newUnderstandingRequest}=await import('../src/setup-understanding-contract.mjs');
 const material={id:'33333333-3333-4333-8333-333333333333',name:'导入报告.docx',status:'parsed',archived:false,version:1,hash:'a'.repeat(64),ocr:{coverage_status:'complete'}};
 const selected=[material.id];assert.equal(understandingMaterialChoice(material,[],{canEdit:true}).disabled,false);
 assert.deepEqual(assertUnderstandingMaterialSelection([material],selected),selected);
 assert.deepEqual(newUnderstandingRequest({revision:4,model_id:'qwen',material_ids:selected,instruction:'核对',mode:'understand'},'request').material_ids,selected);
 assert.equal(understandingMaterialChoice({...material,status:'queued'},[],{canEdit:true}).disabled,true);
 assert.equal(understandingMaterialChoice({...material,archived:true},[],{canEdit:true}).disabled,true);
});

test('material read identity distinguishes reused usernames, roles and returning sessions',async()=>{
 const {materialReadScope}=await import('../src/material-list-merge.mjs');
 const user={workspace_id:'w',user_id:'u1',username:'same',role:'admin'};
 assert.notEqual(materialReadScope(user,1),materialReadScope({...user,user_id:'u2'},1));
 assert.notEqual(materialReadScope(user,1),materialReadScope({...user,role:'viewer'},1));
 assert.notEqual(materialReadScope(user,1),materialReadScope(user,2));
});

test('a refused old editing guard hides old identity data and does not merge new materials',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/?view=business'}),keys=['window','document','navigator','location','history','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,location:dom.window.location,history:dom.window.history,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const server=await createServer({configFile:false,plugins:[{name:'guard-test',enforce:'pre',resolveId(id){if(id==='./FullSemanticHandoff')return '\0test-handoff';},load(id){if(id==='\0test-handoff')return `import React,{useEffect} from 'react';export function FullSemanticConfigurationView({onRegisterGuard}){useEffect(()=>{onRegisterGuard(()=>false);return()=>onRegisterGuard(null)},[]);return null} export function FullSemanticHandoffRecoveries(){return null}`;}}],server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{const Component=(await server.ssrLoadModule('/src/SetupStudio.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,primary,ghost,icon,...p})=>React.createElement('button',p,children),Field=({children})=>React.createElement('label',null,children),Icon=()=>null,Dialog=()=>null;
 const first={instanceProfile:{name:'原账号秘密',domain:'林业'},materials:[{id:'old',name:'原账号资料',size:1}],businesses:[],flows:{}},second={instanceProfile:{name:'新账号内容',domain:'水利'},materials:[],businesses:[],flows:{}};
 const props={runtime:true,step:1,account:{user_id:'old',workspace_id:'w',role:'admin'},workspace:first,canonicalMaterials:first.materials,onCommit:()=>true,onGo:()=>{},onRegisterGuard:()=>{},Icon,Btn,Field,Dialog};root=createRoot(document.getElementById('root'));
 await act(async()=>root.render(React.createElement(Component,props)));await act(async()=>root.render(React.createElement(Component,{...props,account:{user_id:'new',workspace_id:'w',role:'admin'},workspace:second,canonicalMaterials:[{id:'new',name:'新账号资料',size:1}]})));
 assert.match(document.body.textContent,/返回原账号核对/);assert.doesNotMatch(document.body.textContent,/原账号秘密|原账号资料|新账号资料/);await act(async()=>root.render(React.createElement(Component,props)));assert.doesNotMatch(document.body.textContent,/返回原账号核对/);assert.doesNotMatch(document.body.textContent,/新账号资料/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
