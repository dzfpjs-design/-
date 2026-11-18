import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {id,account as fixtureAccount,text} from './report-evidence-v3-fixture.mjs';
import {digest} from '../src/report-full-reading-contract.mjs';
import {verifyScopedActivationStatus} from '../src/report-scoped-activation.mjs';

const account={...fixtureAccount,role:'admin'},plan={preview_id:id(70),original_revision:2,command_hash:'a'.repeat(64),verified:true};
let Component,tmp;
test.before(async()=>{tmp=await mkdtemp(join(tmpdir(),'scope-activation-'));const built=await build({configFile:false,envDir:tmp,logLevel:'silent',cacheDir:join(tmp,'cache'),build:{ssr:resolve('src/ScopedReportActivation.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}}),file=join(tmp,'component.mjs');await writeFile(file,built.output.find(o=>o.isEntry).code);Component=(await import(pathToFileURL(file))).default;});
test.after(()=>rm(tmp,{recursive:true,force:true}));
async function env(fn){const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator'),tails=new Map(),locks={request(name,opts,callback){const p=tails.get(name)||Promise.resolve(),next=p.then(callback);tails.set(name,next.catch(()=>{}));return next;}};Object.defineProperty(globalThis,'navigator',{configurable:true,writable:true,value:{locks}});
 const state={status:'not_found',plan_status:'saved',calls:[],issueTimeout:false,statusFail:false,hold:null};
 const value=async()=>{const v={schema:'report-scoped-activation-status-v1',workspace_id:account.workspace_id,actor_id:account.user_id,preview_id:plan.preview_id,activation_id:state.status==='not_found'?null:id(71),status:state.status,plan_status:state.plan_status,expires_at:state.status==='not_found'?null:'2026-11-19T00:00:00Z',execution_history:'unknown',this_action_calls_model:false,user_confirmed_business:false,in_flight_may_continue:true};v.status_hash=await digest(v);return v;};
 const request=async(path,options)=>{state.calls.push({path,body:JSON.parse(options.body)});if(path.endsWith('/status')){if(state.statusFail)throw Error('offline');if(state.hold)await new Promise(resolve=>{state.release=resolve;});return value();}if(path.endsWith('/activation')){assert.equal(JSON.parse(options.body).consent,true);state.status='active';if(state.issueTimeout){state.issueTimeout=false;throw Error('lost');}return {ok:true};}if(path.endsWith('/revoke')){state.status='revoked';return {ok:true};}throw Error('unexpected request');};
 const props={account,plan,request,onChanged:async()=>{}},ui=mount(Component,props);try{await fn({state,props,ui,request,value});}finally{ui.unmount();if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;}}

test('status, explicit activation and revocation use server state, no model call',async()=>env(async({state,ui})=>{await ui.click('核对定向启用状态');assert.match(text(ui.render()),/不能证明历史没有调用或费用/);await ui.click('确认启用这一计划');assert.match(text(ui.render()),/服务器确认此计划当前已启用/);assert.match(text(ui.render()),/已经进入发送流程的请求仍可能继续/);await ui.click('撤销这一计划的启用');assert.match(text(ui.render()),/服务器确认此计划的启用已撤销/);assert.equal(state.calls.filter(c=>c.path.endsWith('/activation')).length,1);assert.equal(state.calls.filter(c=>c.path.endsWith('/revoke')).length,1);assert.ok(state.calls.every(c=>!c.path.includes('/start')));}));

test('lost activation response is resolved by server query and survives remount',async()=>env(async({state,ui,props})=>{await ui.click('核对定向启用状态');state.issueTimeout=true;await ui.click('确认启用这一计划');assert.match(text(ui.render()),/服务器确认此计划当前已启用/);assert.equal(state.calls.filter(c=>c.path.endsWith('/activation')).length,1);const other=mount(Component,props);try{await other.click('核对定向启用状态');assert.match(text(other.render()),/服务器确认此计划当前已启用/);assert.ok(other.button('撤销这一计划的启用'));}finally{other.unmount();}}));

test('status failure disables writes until server is rechecked',async()=>env(async({state,ui})=>{await ui.click('核对定向启用状态');state.statusFail=true;await ui.click('确认启用这一计划');assert.match(text(ui.render()),/启用状态暂时无法确认/);assert.doesNotMatch(text(ui.render()),/确认启用这一计划/);state.statusFail=false;await ui.click('核对定向启用状态');assert.match(text(ui.render()),/服务器未找到可核对的启用记录/);assert.equal(state.calls.filter(c=>c.path.endsWith('/activation')).length,0);}));

test('two tabs serialize activation and the second sees the new server state',async()=>env(async({state,ui,props})=>{const second=mount(Component,props);try{await ui.click('核对定向启用状态');await second.click('核对定向启用状态');await Promise.all([ui.button('确认启用这一计划').props.onClick(),second.button('确认启用这一计划').props.onClick()]);await ui.flush();await second.flush();assert.equal(state.calls.filter(c=>c.path.endsWith('/activation')).length,1);assert.match(text(second.render()),/状态暂时无法确认/);await second.click('核对定向启用状态');assert.match(text(second.render()),/服务器确认此计划当前已启用/);}finally{second.unmount();}}));

test('scope change hides a delayed response; malformed server status is rejected',async()=>env(async({state,ui,props,value})=>{state.hold=true;const p=ui.button('核对定向启用状态').props.onClick();for(let i=0;i<20&&!state.release;i++)await ui.flush();assert.ok(state.release);props.account={...account,user_id:id(99)};ui.render();state.release();await p;await ui.flush();assert.doesNotMatch(text(ui.render()),/服务器未找到可核对的启用记录/);const bad=await value();bad.workspace_id=id(98);await assert.rejects(verifyScopedActivationStatus(bad,account,plan));}));

test('started plan can retrieve and revoke its original activation',async()=>env(async({state,ui})=>{state.plan_status='started';state.status='active';await ui.click('核对定向启用状态');await ui.click('撤销这一计划的启用');assert.match(text(ui.render()),/启用已撤销/);assert.equal(state.calls.filter(c=>c.path.endsWith('/revoke')).length,1);}));
