import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';

// Compile the actual JSX and invoke its rendered handlers/effects. Storage is an
// injected synthetic adapter; no browser, listener, API, or real files are used.
const account={user_id:'91000000-0000-4000-8000-000000000001',workspace_id:'91000000-0000-4000-8000-000000000002',username:'synthetic-guard',role:'operator'};
const clone=value=>structuredClone(value),storage=()=>({getItem:()=>null,setItem(){},removeItem(){}});
const nodes=(value,out=[])=>{if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value?.props){out.push(value);nodes(value.props.children,out);}return out;};
const text=value=>Array.isArray(value)?value.map(text).join(''):value?.props?text(value.props.children):['string','number'].includes(typeof value)?String(value):'';
const workspace=()=>({instanceProfile:{name:'合成实例',domain:'合成领域',goal:'初始人工目标'},businesses:[],flows:{},resources:[],materials:[{id:'synthetic-pdf',name:'合成报告.pdf',version:3,hash:'a'.repeat(64),archived:false,ocr:{run_status:'running',confirmed:2}}]});
const goal=ui=>{const field=nodes(ui.render()).find(node=>node.props.label==='主要目标与业务说明');assert.ok(field);const input=nodes(field).find(node=>node.type==='textarea');assert.ok(input);return input;};
let Studio,configureMaterialBackend,tmp;
test.before(async()=>{
 tmp=await mkdtemp(join(tmpdir(),'setup-readonly-guard-'));await mkdir(join(tmp,'empty-env'));
 const frontend=fileURLToPath(new URL('../',import.meta.url)),entry=join(tmp,'entry.jsx');
 await writeFile(entry,`export {default} from ${JSON.stringify(join(frontend,'src/SetupStudio.jsx'))};\nexport {configureMaterialBackend} from ${JSON.stringify(join(frontend,'src/material-store.js'))};\n`);
 const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
 try{
  const result=await build({root:frontend,configFile:false,envDir:join(tmp,'empty-env'),cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:['react'],output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?new URL('../node_modules/react/index.js',import.meta.url).href:id}}}});
  const modulePath=join(tmp,'component.mjs');await writeFile(modulePath,result.output.find(item=>item.isEntry).code);
  const module=await import(pathToFileURL(modulePath));Studio=module.default;configureMaterialBackend=module.configureMaterialBackend;
 }finally{globalThis.sessionStorage=previous;}
});
test.after(()=>rm(tmp,{recursive:true,force:true}));
async function fixture(run,{role='operator'}={}){
 const names=['sessionStorage','localStorage','location','window','fetch'],previous=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value]of Object.entries({sessionStorage:storage(),localStorage:storage(),location:{search:''},window:{addEventListener(){},removeEventListener(){}},fetch:async()=>{throw Error('this guard must not make API requests');}}))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 const calls=[];let guard,answer=true;
 const props={runtime:true,account:{...account,role},step:0,workspace:workspace(),onCommit(value){calls.push(clone(value));if(answer instanceof Error)throw answer;return answer;},onRegisterGuard(value){guard=value;},onGo(){},Icon:'span',Btn:'button',Field:'label',Dialog:'section'};
 const ui=mount(Studio,props);
 try{await ui.flush();await run({ui,props,calls,guard:()=>guard,answer:value=>{answer=value;},async edit(value){goal(ui).props.onChange({target:{value}});await ui.flush();}});}
 finally{ui.unmount();configureMaterialBackend(null);for(const name of names){if(previous[name])Object.defineProperty(globalThis,name,previous[name]);else delete globalThis[name];}}
}

test('actual untouched boot and derived OCR progress updates leave the guard read-only',async()=>fixture(async({ui,props,calls,guard})=>{
 assert.equal(guard()(),true);assert.equal(calls.length,0);
 props.workspace={...clone(props.workspace),materials:[{...clone(props.workspace.materials[0]),ocr:{run_status:'review',confirmed:2}}]};await ui.flush();
 assert.equal(guard()(),true);assert.equal(guard()(),true);assert.equal(calls.length,0,'opening OCR must not save a derived material projection');assert.equal(goal(ui).props.value,'初始人工目标');
}));

test('actual human input commits immediately once; the guard does not repeat the queued save',async()=>fixture(async({ui,props,calls,guard,edit})=>{
 await edit('人工输入最后一字甲');assert.equal(calls.length,1);assert.equal(calls[0].instanceProfile.goal,'人工输入最后一字甲');
 assert.equal(guard()(),true);assert.equal(calls.length,1);
 props.workspace={...clone(calls[0]),materials:[{...clone(props.workspace.materials[0]),ocr:{run_status:'review',confirmed:2}}]};await ui.flush();
 assert.equal(guard()(),true);assert.equal(calls.length,1);assert.equal(goal(ui).props.value,'人工输入最后一字甲');
}));

test('actual failed input survives external props and retries the exact snapshot through the newest callback',async()=>fixture(async({ui,props,calls,guard,answer,edit})=>{
 answer(false);await edit('尚未提交的人工最后一字乙');const failed=clone(calls[0]);assert.equal(calls.length,1);
 props.workspace={...workspace(),instanceProfile:{...workspace().instanceProfile,goal:'外部只读配置目标'},materials:[{...workspace().materials[0],ocr:{run_status:'review',confirmed:2}}]};await ui.flush();
 assert.equal(goal(ui).props.value,'尚未提交的人工最后一字乙');const oldGuard=guard(),latest=[];
 props.onCommit=value=>{latest.push(clone(value));return false;};ui.render();
 assert.equal(oldGuard(),false);assert.deepEqual(latest,[failed]);assert.equal(calls.length,1);assert.equal(goal(ui).props.value,failed.instanceProfile.goal);
 props.onCommit=value=>{latest.push(clone(value));return true;};ui.render();assert.equal(guard()(),true);await ui.flush();
 assert.deepEqual(latest,[failed,failed]);assert.equal(guard()(),true);assert.equal(latest.length,2);assert.equal(goal(ui).props.value,failed.instanceProfile.goal);
}));

test('actual thrown commit retains the last human edit and a failing guard remains closed',async()=>fixture(async({ui,calls,guard,answer,edit})=>{
 answer(Error('synthetic local write failure'));await edit('写入异常后仍保留丙');assert.equal(guard()(),false);await ui.flush();assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);assert.equal(goal(ui).props.value,'写入异常后仍保留丙');assert.match(text(ui.render()),/填写内容仍保留/);
}));

test('actual upload remains busy-blocked without committing any untouched draft',async()=>fixture(async({ui,calls,guard})=>{
 let fail;const pending=new Promise((resolve,reject)=>{fail=reject;});configureMaterialBackend({put:()=>pending});
 const input=nodes(ui.render()).find(node=>node.type==='input'&&node.props.type==='file');assert.ok(input);input.props.onChange({target:{files:[{name:'synthetic.txt',size:1}],value:'synthetic.txt'}});
 assert.equal(guard()(),false);assert.equal(calls.length,0);fail(Error('synthetic upload cancelled'));await ui.flush();assert.equal(guard()(),true);assert.equal(calls.length,0);
}));

test('actual readonly without pending input can browse but downgraded failed input cannot be discarded or written',async()=>{
 await fixture(async({guard,calls})=>{assert.equal(guard()(),true);assert.equal(calls.length,0);},{role:'viewer'});
 await fixture(async({ui,props,calls,guard,answer,edit})=>{
  answer(false);await edit('权限变更也保留末字丁');const staleHandler=goal(ui).props.onChange,oldGuard=guard();props.account={...props.account,role:'viewer'};ui.render();
  assert.equal(oldGuard(),false);assert.equal(guard()(),false);staleHandler({target:{value:'旧可写处理器不得提交'}});await ui.flush();assert.equal(calls.length,1);assert.equal(goal(ui).props.value,'权限变更也保留末字丁');
  props.account={...props.account,role:'operator'};answer(true);ui.render();assert.equal(guard()(),true);assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);
 });
});

test('actual stale actor handlers cannot commit through the new owner; returning retains the original failed snapshot',async()=>fixture(async({ui,props,calls,guard,answer,edit})=>{
 answer(false);await edit('账号甲未提交末字戊');const original=clone(calls[0]),guardA=guard(),handlerA=goal(ui).props.onChange,accountA=props.account;
 props.account={...account,user_id:'91000000-0000-4000-8000-000000000009',workspace_id:'91000000-0000-4000-8000-000000000010',username:'synthetic-guard-B'};props.workspace=workspace();const newCalls=[];props.onCommit=value=>{newCalls.push(clone(value));return true;};await ui.flush();
 assert.equal(guardA(),false);handlerA({target:{value:'迟到甲不得写乙'}});await ui.flush();assert.equal(guard()(),true);assert.equal(newCalls.length,0);assert.equal(calls.length,1);assert.equal(goal(ui).props.value,'初始人工目标');
 props.account=accountA;props.workspace=workspace();await ui.flush();assert.equal(goal(ui).props.value,original.instanceProfile.goal);assert.equal(guard()(),true);assert.deepEqual(newCalls,[original]);assert.equal(calls.length,1);
}));
