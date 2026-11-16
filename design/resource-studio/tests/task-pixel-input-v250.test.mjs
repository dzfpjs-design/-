import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {webcrypto} from 'node:crypto';
import {pixelFixture,initializePixelControllers,id,copy,file} from './pixel-change-v249-fixture.mjs';
import {taskPixelContext,blankTaskPixelPair,changeTaskPixelSource,changeTaskPixelConsent,adoptTaskPixelPair,taskPixelPairFields,checkedTaskPixelOptions,checkedTaskPixelPairSnapshot,taskPixelBusinessCandidate} from '../src/task-pixel-pair-v250.mjs';
import {pixelSHA,pixelCanonical} from '../src/pixel-change-v249-protocol.mjs';
import {persistTaskCreation,readTaskCreation,taskCreationJSON} from '../src/graph-task-inputs.mjs';
let Controller;
async function harness(run){
 const f=pixelFixture(),dom=new JSDOM('<!doctype html>',{url:'https://task-pixel-unit.invalid'}),globals={window:dom.window,localStorage:f.storage,sessionStorage:f.session,crypto:webcrypto},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 try{await initializePixelControllers();Controller=(await import('../src/task-pixel-input-v250-controller.mjs')).TaskPixelInputControllerV250;
  const context=taskPixelContext(f.instanceId,f.releaseID,'business_pixel250'),state={required:true,legacy:false,chooseSaved:true,chosen:[],optionsGate:null},options=()=>({schema:'workflow-pixel-task-options-v250',...context,pixel_pair_required:state.required,provider:'task_pixel_pair_refs',single_entity:true,execution_mode:'confirmed_automatic_workflow',capabilities:state.required?[{id:id(900),hash:'d'.repeat(64),threshold_text:'0.5',model_trace:f.trace(),runtime_hash:'f'.repeat(64)}]:[],legacy_images_allowed:state.legacy,legacy_image_provider:state.legacy?'task_image_ids':null});
  const request=async(path,init={})=>{if(path.startsWith(f.base+'/task-options?')){const url=new URL(path,'https://task-pixel.invalid');assert.equal(url.searchParams.get('release_id'),context.release_id);assert.equal(url.searchParams.get('business_id'),context.business_id);if(state.optionsGate)await state.optionsGate;return options();}return f.api(path,init);},make=extra=>new Controller({account:f.account,context,request,readBinary:f.binary,storage:f.storage,session:f.session,locks:f.locks,onChoose:async(side,image)=>{state.chosen.push({side,image:copy(image)});return state.chooseSaved;},...extra});await run({...f,context,input:{state,options,request,make}});
 }finally{dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
function pair(f){let value=blankTaskPixelPair(f.context);value=changeTaskPixelSource(value,f.context,'before',f.before);value=changeTaskPixelSource(value,f.context,'after',f.after);value=changeTaskPixelConsent(value,f.context,'pixel_correspondence_confirmed',true);return changeTaskPixelConsent(value,f.context,'full_image_present_confirmed',true);}
test('V250 server task-options supplies the pixel provider for real graph or linear capability pins',()=>harness(async f=>{
 const o=checkedTaskPixelOptions(f.input.options(),f.context);assert.equal(o.pixel_pair_required,true);assert.equal(o.provider,'task_pixel_pair_refs');assert.equal(taskPixelBusinessCandidate({execution_mode:'confirmed_graph_workflow',flow:{steps:[{agent:'人工确认'}]}}),true);assert.equal(taskPixelBusinessCandidate({flow:{steps:[{execution:{adapter:'local_pixel_change'}}]}}),true);assert.equal(taskPixelBusinessCandidate({flow:{steps:[{agent:'人工确认'}]}}),false);const bad=copy(o);bad.business_id='same_label_other_id';assert.throws(()=>checkedTaskPixelOptions(bad,f.context));bad.business_id=f.context.business_id;bad.capabilities=[];assert.throws(()=>checkedTaskPixelOptions(bad,f.context));
}));
test('V250 independent pair fields preserve A/B order and never add empty optional fields to a legacy Task body',()=>harness(async f=>{
 const old={release_id:f.releaseID,business_id:'business_old250',entity_ids:[id(80)],title:'原业务任务',request_id:id(81)},raw=JSON.stringify(old),ignored=taskPixelPairFields(false,null,null,[],[]);assert.deepEqual(ignored,{});assert.equal(JSON.stringify({...old,...ignored}),raw);const value=pair(f),fields=taskPixelPairFields(true,value,f.context,[id(80)]);assert.deepEqual(fields,{pixel_pair:{before_image_id:f.before.id,after_image_id:f.after.id,pixel_correspondence_confirmed:true,full_image_present_confirmed:true}});assert.equal(Object.hasOwn(fields,'image_ids'),false);
}));
test('V250 pairing rejects multiple objects and unsupported ordinary-image conflict while leaving every selection intact',()=>harness(async f=>{
 const value=pair(f),before=JSON.stringify(value),ids=[id(81),id(82)];assert.throws(()=>taskPixelPairFields(true,value,f.context,ids),/一次只关联一个对象/);assert.deepEqual(ids,[id(81),id(82)]);assert.throws(()=>taskPixelPairFields(true,value,f.context,[id(81)],[f.before.id]),/取消普通任务图片选择/);assert.equal(JSON.stringify(value),before);
}));
test('V250 mixed providers keep explicit legacy images and independent ordered pair without substituting either purpose',()=>harness(async f=>{
 f.input.state.legacy=true;const options=checkedTaskPixelOptions(f.input.options(),f.context),value=pair(f),legacy=[f.after.id],fields=taskPixelPairFields(true,value,f.context,[id(80)],legacy,options.legacy_images_allowed),payload={image_ids:[...legacy],...fields};assert.deepEqual(payload.image_ids,[f.after.id]);assert.equal(payload.pixel_pair.before_image_id,f.before.id);assert.equal(payload.pixel_pair.after_image_id,f.after.id);assert.equal(Object.hasOwn(fields,'image_ids'),false);assert.deepEqual(legacy,[f.after.id]);const bad=copy(options);bad.legacy_image_provider=null;assert.throws(()=>checkedTaskPixelOptions(bad,f.context));
}));
test('V250 changing source or explicitly adopting a new business cancels both previous human confirmations',()=>harness(async f=>{
 const original=pair(f),changed=changeTaskPixelSource(original,f.context,'after',f.before);assert.equal(changed.pixel_correspondence_confirmed,false);assert.equal(changed.full_image_present_confirmed,false);const next={...f.context,business_id:'business_second250'};assert.throws(()=>changeTaskPixelConsent(original,next,'pixel_correspondence_confirmed',true));const adopted=adoptTaskPixelPair(original,next);assert.equal(adopted.before.id,original.before.id);assert.equal(adopted.context.business_id,next.business_id);assert.equal(adopted.full_image_present_confirmed,false);assert.equal(original.full_image_present_confirmed,true);
}));
test('V250 original Task outbox signs the new pair and cold recovery preserves exact raw bytes without affecting legacy bodies',()=>harness(async f=>{
 const body={release_id:f.releaseID,business_id:f.context.business_id,entity_ids:[id(80)],title:'当次两图重新运行',...taskPixelPairFields(true,pair(f),f.context,[id(80)]),request_id:id(81)},key='actual-task-outbox-v250';const raw=persistTaskCreation(f.storage,key,{business:body.business_id,pixelPairDraft:pair(f)},body,{typedNumbers:true}),pending=readTaskCreation(f.storage,key);assert.equal(taskCreationJSON(pending.payload),raw);assert.equal(JSON.parse(raw).pixel_pair.before_image_id,f.before.id);assert.equal(JSON.parse(raw).pixel_pair.after_image_id,f.after.id);const changed=copy(pending.payload);changed.pixel_pair.after_image_id=id(88);assert.notEqual(taskCreationJSON(changed),raw);assert.equal(f.state.posts.length,0);
}));
test('V250 Task snapshot decoder verifies the complete original pair and rejects a changed order or source',()=>harness(async f=>{
 const unsigned={schema:'workflow-pixel-pair-v250',before:copy(f.before),after:copy(f.after),input_order:'before_after',pixel_correspondence_confirmed:true,full_image_present_confirmed:true,coordinate_space:'pixel',georeference_available:false},snapshot={...unsigned,pair_hash:await pixelSHA(pixelCanonical(unsigned))};await checkedTaskPixelPairSnapshot(snapshot);const bad=copy(snapshot);[bad.before,bad.after]=[bad.after,bad.before];await assert.rejects(checkedTaskPixelPairSnapshot(bad));
}));
test('V250 normal task upload preserves bytes and waits for the parent human draft to confirm its save',()=>harness(async f=>{
 const c=f.input.make();await c.start();const saved=await c.upload('before',file('本次A.png',66),true);assert.equal(c.pending,null);assert.equal(f.input.state.chosen.length,1);assert.equal(f.input.state.chosen[0].side,'before');assert.equal(f.input.state.chosen[0].image.sha256,saved.sha256);assert.equal(f.state.posts.length,1);assert.equal(f.state.posts[0].path,f.imageBase);assert.equal(c.guard(),true);c.dispose();
}));
test('V250 uploaded original with parent save failure keeps pending rather than declaring source choice complete',()=>harness(async f=>{
 const c=f.input.make();await c.start();f.input.state.chooseSaved=false;await assert.rejects(c.upload('after',file('本次B.png',67),true),/尚未完整暂存/);const p=copy(c.pending);assert.equal(f.state.posts.length,1);assert.equal(c.pending.side,'after');f.input.state.chooseSaved=true;await c.reconcile();assert.equal(c.pending,null);assert.equal(f.state.posts.length,1);assert.equal(f.input.state.chosen.at(-1).image.sha256,p.sha256);c.dispose();
}));
test('V250 unknown task upload cold entry checks originals only and does not silently reupload or replace the original file',()=>harness(async f=>{
 const c=f.input.make();await c.start();f.state.uploadMode='unknown';await assert.rejects(c.upload('before',file('未知原件.png',68),true));const p=copy(c.pending);c.dispose();const next=f.input.make();await next.start();assert.equal(next.pending.local_id,p.local_id);assert.equal(f.state.posts.length,1);assert.equal(next.guard(),true);await assert.rejects(next.upload('before',file('别的原件.png',69),true),/同一原PNG/);assert.equal(f.state.posts.length,1);next.dispose();
}));
test('V250 lost task upload reply is positively reconciled against the exact stored PNG without another POST',()=>harness(async f=>{
 const c=f.input.make();await c.start();f.state.uploadMode='lost';const image=await c.upload('after',file('已存原件.png',70),true);assert.equal(c.pending,null);assert.equal(f.state.posts.length,1);assert.equal(f.input.state.chosen.at(-1).image.sha256,image.sha256);c.dispose();
}));
test('V250 original upload metadata storage failure is repairable on the same page with no upload or new identifier',()=>harness(async f=>{
 const c=f.input.make();await c.start();f.storage.failSet=key=>key===c.prefix+'pending';await assert.rejects(c.upload('before',file('原信息.png',71),true));const p=copy(c.pending);assert.equal(c.guard(),false);assert.equal(f.state.posts.length,0);f.storage.failSet=null;await c.repair();assert.equal(c.guard(),true);assert.equal(c.pending.local_id,p.local_id);assert.equal(f.state.posts.length,0);assert.equal(f.storage.getItem(c.prefix+'pending'),JSON.stringify(p));c.dispose();
}));
test('V250 pause prevents new upload while source directory and previously saved originals remain readable',()=>harness(async f=>{
 const c=f.input.make();await c.start();await assert.rejects(c.upload('before',file('原图.png',71),false),/不能新上传/);assert.equal(f.state.posts.length,0);assert.ok((await c.original(f.before)).byteLength>0);c.dispose();
}));
test('V250 account switch during readonly input-options read hides source data without creating a new upload',()=>harness(async f=>{
 let release;f.input.state.optionsGate=new Promise(resolve=>release=resolve);const c=f.input.make(),start=c.start();for(let turn=0;turn<100&&!c.authenticated;turn++)await new Promise(resolve=>setTimeout(resolve,0));f.session.setItem('ga-setup-csrf-user',id(999));release();await start;assert.equal(c.denied,true);assert.equal(c.options,null);assert.equal(f.state.posts.length,0);c.dispose();
}));
test('V250 ordinary graph business false options do not demand a pair or read/upload unused image data',()=>harness(async f=>{
 f.input.state.required=false;const c=f.input.make();await c.start();assert.equal(c.options.pixel_pair_required,false);assert.deepEqual(c.images,[]);assert.equal(f.state.readCalls.some(row=>row.path===f.imageBase),false);assert.equal(f.state.posts.length,0);c.dispose();
}));
