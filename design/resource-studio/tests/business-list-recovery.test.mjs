import {evidencePath} from './evidence-path.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {readFile,mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';import {pathToFileURL} from 'node:url';import {resolve} from 'node:path';
import React from 'react';import {renderToString} from 'react-dom/server';import {build} from 'vite';import react from '@vitejs/plugin-react';
import {businessNames,changeBusinesses} from '../src/business-schema.js';import {flowFor} from '../src/setup-schema.js';import {normalize} from '../src/automation-schema.js';
import {businessListKey,openBusinessListEditor,storeBusinessListRows,applyBusinessListEditor,businessListMessage} from '../src/business-list-contract.mjs';
const snapshot=JSON.parse(await readFile(evidencePath('safe-automation-v22/browser/before-usable-ui.json'),'utf8'));const stored=snapshot.records.setup_drafts[0].data;const B=typeof stored==='string'?JSON.parse(stored):stored;
const storage=()=>{const values=new Map();return {values,get length(){return values.size;},key:index=>[...values.keys()][index]||null,getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
const account={workspace_id:'w',username:'human',role:'operator'},key=businessListKey(account,'business',true),rows=()=>[...businessNames(B).map(name=>({original:name,name})),{name:'资料补核'}];
const oldNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator');const tails=new Map(),locks={request(key,_options,operation){const result=(tails.get(key)||Promise.resolve()).then(operation);tails.set(key,result.catch(()=>{}));return result;}};Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks}});
test.after(()=>{if(oldNavigator)Object.defineProperty(globalThis,'navigator',oldNavigator);else delete globalThis.navigator;});
test('actual saved B configuration adds business without optional gaps or resources and retains all flow/policy data',()=>{
 assert.equal(Object.hasOwn(B,'gaps'),false);const before=JSON.stringify(B),next=changeBusinesses(B,rows());assert.equal(next.businesses.at(-1),'资料补核');assert.deepEqual(next.gaps,[]);assert.deepEqual(next.flows,B.flows);for(const [name,policy]of Object.entries(B.automation.policies))assert.deepEqual(next.automation.policies[name],policy);assert.equal(Object.keys(next.automation.policies['资料补核'].rules).length,6);assert.equal(JSON.stringify(B),before);
 const optional={...B};delete optional.resources;const missing=changeBusinesses(optional,rows());assert.deepEqual(missing.resources,[]);assert.deepEqual(missing.gaps,[]);
 const safe=normalize(missing.automation,businessNames(missing),missing.businessAliases,true).policies['资料补核'];assert.deepEqual(safe.triggers,['人工发起']);assert.ok(Object.values(safe.rules).every(rule=>rule.mode==='仅给出建议'));
});
test('actual B rename/delete keeps references, external associations, archived flows and permissions',()=>{
 const name=businessNames(B)[0],resource={id:'real-resource',custom:{evidence:'keep'},businesses:[name,'外部绑定']},gap={id:'gap',business:name,evidence:'keep'},workspace={...B,resources:[resource,{id:'unbound',custom:2}],gaps:[gap]};
 const renamed=changeBusinesses(workspace,[{original:name,name:'资料复核'}]);assert.deepEqual(renamed.resources[0].businesses,['资料复核','外部绑定']);assert.deepEqual(renamed.resources[1].businesses,[]);assert.equal(renamed.gaps[0].business,'资料复核');assert.deepEqual(flowFor(renamed,'资料复核'),flowFor(workspace,name));assert.deepEqual(renamed.automation,workspace.automation);
 const removed=changeBusinesses(workspace,[]);assert.deepEqual(removed.resources[0].businesses,['外部绑定']);assert.equal(removed.gaps[0].business,name);assert.deepEqual(removed.flows,workspace.flows);assert.deepEqual(removed.automation,workspace.automation);assert.deepEqual(resource.businesses,[name,'外部绑定']);
});
test('present corrupt collections or bindings block without mutating actual saved input',()=>{
 for(const field of ['resources','gaps'])for(const bad of [null,'broken',{},[null]]){const workspace={...B,[field]:bad},before=JSON.stringify(workspace);assert.throws(()=>changeBusinesses(workspace,rows()),error=>error.code==='BUSINESS_CONFIG_INVALID'&&!/reading|undefined|map/.test(error.message));assert.equal(JSON.stringify(workspace),before);}
 for(const bad of [null,'broken',{},[5],[null]]){const workspace={...B,resources:[{id:'preserved',businesses:bad}]},before=JSON.stringify(workspace);assert.throws(()=>changeBusinesses(workspace,rows()),/资源关联业务/);assert.equal(JSON.stringify(workspace),before);}
});
test('actual handler onCommit false and throw preserve typed rows across refresh with no applied/close callbacks',async()=>{
 for(const failure of ['false','throw']){const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);let applied=0,closed=0;
  const callbacks={onCommit(){if(failure==='throw')throw new TypeError("Cannot read properties of undefined (reading 'map')");return false;},onApplied(){applied++;},onClose(){closed++;}};
  if(failure==='throw'){await assert.rejects(applyBusinessListEditor(editor,B,rows(),callbacks),TypeError);assert.doesNotMatch(businessListMessage(new TypeError('reading map')),/reading|map|undefined/);}else assert.equal((await applyBusinessListEditor(editor,B,rows(),callbacks)).applied,false);
  const refreshed=openBusinessListEditor(store,tab,key,B);assert.deepEqual(refreshed.form.rows,rows());assert.equal(refreshed.restored,true);assert.equal(applied,0);assert.equal(closed,0);
 }
});
test('stale journal cannot remove a business added later, including an old explicitly empty edit',async()=>{
 for(const edited of [rows(),[]]){const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);storeBusinessListRows(editor,edited);const refreshed=openBusinessListEditor(store,tab,key,{...B,businesses:[...B.businesses,'另一页业务']});let commits=0;
  await assert.rejects(applyBusinessListEditor(refreshed,{...B,businesses:[...B.businesses,'另一页业务']},edited,{onCommit(){commits++;return true;}}),/不会覆盖其他页面/);assert.equal(commits,0);assert.deepEqual(openBusinessListEditor(store,tab,key,B).form.rows,edited);
 }
});
test('editor account/page scopes and cloned sessions keep independent writers',()=>{
 const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);storeBusinessListRows(editor,rows());const cloneTab=storage();for(const [name,value]of tab.values)cloneTab.setItem(name,value);const clone=openBusinessListEditor(store,cloneTab,key,B);assert.notEqual(clone.owner,editor.owner);storeBusinessListRows(clone,[{name:'另页输入'}]);assert.deepEqual(openBusinessListEditor(store,tab,key,B).form.rows,rows());assert.deepEqual(openBusinessListEditor(store,cloneTab,key,B).form.rows,[{name:'另页输入'}]);
 assert.equal(openBusinessListEditor(store,storage(),businessListKey({...account,username:'other'},'business',true),B).restored,false);assert.equal(openBusinessListEditor(store,storage(),businessListKey(account,'automation',true),B).restored,false);
});
test('successful handler clears only exact owned input after actual save readback, mismatch or unknown keeps draft',async()=>{
 for(const outcome of ['unknown','changed','saved']){const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);let callbacks=0;const options={onCommit:()=>true,onSave:async()=>{if(outcome==='unknown')throw Error('服务器暂不可连接');return {configuration:outcome==='saved'?changeBusinesses(B,rows()):B};},onApplied:()=>callbacks++,onClose:()=>callbacks++};
  if(outcome==='saved'){await applyBusinessListEditor(editor,B,rows(),options);assert.equal(callbacks,2);assert.equal(openBusinessListEditor(store,tab,key,changeBusinesses(B,rows())).restored,false);}else {await assert.rejects(applyBusinessListEditor(editor,B,rows(),options));assert.equal(callbacks,0);assert.deepEqual(openBusinessListEditor(store,tab,key,B).form.rows,rows());}
 }
});
test('real BusinessListDialog SSR restores actual failed B rows and shows task language/review boundary',async()=>{
 await mkdir(resolve('.m62-validation'),{recursive:true});const dir=await mkdtemp(resolve('.m62-validation/.business-list-render-')),store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);await applyBusinessListEditor(editor,B,rows(),{onCommit:()=>false});
 const priorLocation=globalThis.location,priorSession=globalThis.sessionStorage,priorStorage=globalThis.localStorage;globalThis.location={search:'?view=business'};
 try{const output=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/BusinessListDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});const path=resolve(dir,'dialog.mjs');await writeFile(path,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);const DialogComponent=(await import(pathToFileURL(path).href)).default;
 const Btn=({children,disabled})=>React.createElement('button',{disabled},children),Field=({children,label})=>React.createElement('label',{},label,children),Dialog=({children,title})=>React.createElement('section',{},title,children),props={runtime:true,account,workspace:B,storage:store,tabStorage:tab,Btn,Field,Dialog,onClose(){},onCommit:()=>false};
 const html=renderToString(React.createElement(DialogComponent,props));assert.match(html,/value="资料补核"/);assert.match(html,/已恢复上次业务清单编辑稿/);assert.match(html,/第4步重新核对/);assert.doesNotMatch(html,/reading.*map|Cannot read/);
 const viewer=renderToString(React.createElement(DialogComponent,{...props,account:{...account,role:'viewer'}}));assert.match(viewer,/当前账号只读/);assert.match(viewer,/<fieldset[^>]+disabled=""/);
 assert.match(html,/保存核对入口暂不可用/);
 globalThis.sessionStorage=tab;globalThis.localStorage=store;globalThis.location={search:'?view=business&business_list_edit=1'};
 const pageOutput=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/SetupStudio.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});const pagePath=resolve(dir,'page.mjs');await writeFile(pagePath,pageOutput.output.find(item=>item.type==='chunk'&&item.isEntry).code);const Setup=(await import(pathToFileURL(pagePath).href)).default;
 const page=renderToString(React.createElement(Setup,{runtime:true,account,workspace:B,step:1,Icon:()=>null,Btn,Field,Dialog,onCommit:()=>false,onSave:async()=>({configuration:B}),onGo(){},onRegisterGuard(){}}));assert.match(page,/修改业务清单/);assert.match(page,/value="资料补核"/);assert.match(page,/已恢复上次业务清单编辑稿/);

 }finally{await rm(dir,{recursive:true,force:true});if(priorLocation===undefined)delete globalThis.location;else globalThis.location=priorLocation;if(priorSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorSession;if(priorStorage===undefined)delete globalThis.localStorage;else globalThis.localStorage=priorStorage;}
});

test('runtime same-name readback with missing or changed rename alias keeps exact human draft',async()=>{
 const old=businessNames(B)[0],edited=[{original:old,name:'改名复核'}];for(const aliases of [{},{'改名复核':'different'}]){const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);let closed=0;
 await assert.rejects(applyBusinessListEditor(editor,B,edited,{onCommit:()=>true,onSave:async()=>({configuration:{...changeBusinesses(B,edited),businessAliases:aliases}}),onClose:()=>closed++}),/服务器当前业务清单/);
 assert.equal(closed,0);assert.deepEqual(openBusinessListEditor(store,tab,key,B).form.rows,edited);}
});
test('late save readback cannot clear newer editor generation entered while awaiting',async()=>{
 const store=storage(),tab=storage(),editor=openBusinessListEditor(store,tab,key,B);let release,started;const began=new Promise(resolve=>started=resolve),wait=new Promise(resolve=>release=resolve);let closed=0;
 const saving=applyBusinessListEditor(editor,B,rows(),{onCommit:()=>true,onSave:async()=>{started();await wait;return {configuration:changeBusinesses(B,rows())};},onClose:()=>closed++});await began;
 const later=[...rows(),{name:'稍后人稿'}];storeBusinessListRows(editor,later);release();await assert.rejects(saving,/已被另一页面更新/);assert.equal(closed,0);assert.deepEqual(openBusinessListEditor(store,tab,key,B).form.rows,later);
});
