// Independent transport/recovery counterexamples. Synthetic DTOs establish no
// model quality, PostgreSQL durability, actual browser or production acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {
  pixelCanonical, blankPixelDraft, makePixelOriginal, checkedPixelOriginal,
  checkedPixelCreate, checkedPixelRun, checkedPixelRecovery, checkedPixelCommandResult,
} from '../src/pixel-change-v249-protocol.mjs';
import {pixelFixture, initializePixelControllers, id, copy, sha} from './pixel-change-v249-fixture.mjs';

function body(f, requestID='pixel/原号?保留-249-012345') {
  return {
    schema:'pixel-change-create-v249',request_id:requestID,
    instance_version:f.state.instanceVersion,release_id:f.releaseID,
    before_image_id:f.before.id,before_sha256:f.before.sha256,
    after_image_id:f.after.id,after_sha256:f.after.sha256,
    model_installation_id:f.installationID,model_generation:f.state.modelGeneration,
    model_recipe_hash:f.state.recipeHash,pixel_correspondence_confirmed:true,
    full_image_present_confirmed:true,input_order:'before_after',threshold_text:'0.5',
  };
}

// The public backend contract always includes the current attempt. Explicitly
// construct that record here rather than rely on a changing fixture shortcut.
function queued(f, originalBody=body(f)) {
  const run=f.makeRun(originalBody);
  run.attempts=[{generation:1,job_id:run.job_id,status:'queued',error:null,result_hash:null}];
  return run;
}

function completed(f, originalBody=body(f)) {
  const run=queued(f,originalBody);f.state.runs.set(run.id,run);f.finish(run.id);
  run.attempts=[{generation:1,job_id:run.job_id,status:'succeeded',error:null,result_hash:run.result.result_hash}];
  return run;
}

async function original(f, originalBody=body(f)) {
  return makePixelOriginal(f.scope,'create',null,originalBody,webcrypto);
}

function recovery(run, request) {
  return {schema:'pixel-change-recovery-v249',found:true,request_id:request.body.request_id,
    expected_body_hash:request.body_hash,state:'found',run:copy(run)};
}

async function harness(action) {
  const f=pixelFixture(),dom=new JSDOM('<!doctype html>',{url:'https://pixel-peer.invalid'});
  const globals={window:dom.window,localStorage:f.storage,sessionStorage:f.session};
  const previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
  try {await initializePixelControllers();await action(f);}
  finally {
    dom.window.close();
    for(const [key,value] of previous)if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];
  }
}

async function seedPending(f) {
  const request=await original(f),draft=blankPixelDraft(f.scope),b=request.body;
  draft.pins={instance_version:b.instance_version,release_id:b.release_id,
    model_installation_id:b.model_installation_id,model_generation:b.model_generation,model_recipe_hash:b.model_recipe_hash};
  draft.form={before_image_id:b.before_image_id,after_image_id:b.after_image_id,
    threshold_text:b.threshold_text,pixel_correspondence_confirmed:true,full_image_present_confirmed:true};
  draft.images={before:copy(f.before),after:copy(f.after)};
  const raw=JSON.stringify(request),formRaw=JSON.stringify(draft);
  f.storage.setItem(f.prefix+'original:'+b.request_id,raw);
  f.storage.setItem(f.prefix+'pending',raw);
  f.storage.setItem(f.prefix+'form',formRaw);
  return {request,raw,formRaw};
}

test('V249 peer: legal Chinese and /? original IDs round-trip without retagging or rewriting the body',async()=>{
  const f=pixelFixture(),b=body(f),request=await original(f,b),run=queued(f,b);
  assert.equal(checkedPixelCreate(copy(b)).request_id,b.request_id);
  assert.deepEqual(await checkedPixelOriginal(request,f.scope,webcrypto),request);
  assert.deepEqual(await checkedPixelRun(run,f.scope,webcrypto),run);
  assert.deepEqual((await checkedPixelRecovery(recovery(run,request),f.scope,request,webcrypto)).run,run);
  assert.equal(request.raw,JSON.stringify(b));
  assert.equal(request.raw_sha256,sha(request.raw));
  assert.equal(request.body_hash,sha(pixelCanonical(b)));
});

const wrongOriginals={
  'another actor':value=>{value.scope.actor_id=id(88);},
  'another workspace':value=>{value.scope.workspace_id=id(88);},
  'another instance':value=>{value.scope.instance_id=id(88);},
  'changed raw bytes':value=>{value.raw+=' ';},
  'wrong raw digest':value=>{value.raw_sha256='e'.repeat(64);},
  'wrong semantic digest':value=>{value.body_hash='e'.repeat(64);},
};
for(const [name,damage] of Object.entries(wrongOriginals))test('V249 peer: immutable original rejects '+name,async()=>{
  const f=pixelFixture(),request=await original(f),broken=copy(request);damage(broken);
  await assert.rejects(checkedPixelOriginal(broken,f.scope,webcrypto));
  assert.equal(request.raw,JSON.stringify(request.body));
});

const wrongRecoveries={
  'wrong original owner':value=>{value.run.owner_id=id(89);},
  'wrong instance':value=>{value.run.instance_id=id(89);},
  'wrong expected body hash':value=>{value.expected_body_hash='d'.repeat(64);},
  'wrong run body hash':value=>{value.run.body_hash='d'.repeat(64);},
  'wrong before Evidence ID':value=>{value.run.sources.before.id=id(89);},
  'wrong after source SHA':value=>{value.run.sources.after.sha256='d'.repeat(64);},
  'wrong release':value=>{value.run.release_id=id(89);},
  'wrong model generation':value=>{value.run.model_trace.generation++;},
  'self-consistent different threshold':value=>{
    value.run.request.threshold_text='0.6';value.run.body_hash=sha(pixelCanonical(value.run.request));
  },
};
for(const [name,damage] of Object.entries(wrongRecoveries))test('V249 peer: recovery rejects '+name,async()=>{
  const f=pixelFixture(),request=await original(f),reply=recovery(queued(f,request.body),request);
  damage(reply);
  await assert.rejects(checkedPixelRecovery(reply,f.scope,request,webcrypto));
  assert.equal(request.body.threshold_text,'0.5');
});

test('V249 peer: public workspace history can belong to another owner, while original command recovery cannot',async()=>{
  const f=pixelFixture(),run=queued(f),command={schema:'pixel-change-command-v249',request_id:'停止/原操作?249-012345',version:1};
  run.owner_id=id(89);await checkedPixelRun(run,f.scope,webcrypto);
  const request=await makePixelOriginal(f.scope,'cancel',run.id,command,webcrypto);
  const result={schema:'pixel-change-command-result-v249',request_id:command.request_id,operation:'cancel',
    body_hash:request.body_hash,run};
  await assert.rejects(checkedPixelCommandResult(result,f.scope,request,webcrypto));
});

const wrongAttempts={
  'missing current attempt':run=>{run.attempts=[];},
  'outer generation exceeds saved attempts':run=>{run.generation=2;},
  'attempt generation out of order':run=>{run.attempts[0].generation=2;},
  'different outer Job':run=>{run.job_id=id(90);},
  'different current attempt Job':run=>{run.attempts[0].job_id=id(90);},
  'outer status differs from current attempt':run=>{run.status='running';},
  'attempt status differs from outer record':run=>{run.attempts[0].status='cancelled';},
};
for(const [name,damage] of Object.entries(wrongAttempts))test('V249 peer: run decoder rejects '+name,async()=>{
  const f=pixelFixture(),run=queued(f);damage(run);
  await assert.rejects(checkedPixelRun(run,f.scope,webcrypto));
});

test('V249 peer: succeeded output must match the current attempt result hash',async()=>{
  const f=pixelFixture(),run=completed(f);
  await checkedPixelRun(run,f.scope,webcrypto);
  const wrong=copy(run);wrong.attempts[0].result_hash='e'.repeat(64);
  await assert.rejects(checkedPixelRun(wrong,f.scope,webcrypto));
  const absent=copy(run);absent.result=null;
  await assert.rejects(checkedPixelRun(absent,f.scope,webcrypto));
  const impossible=copy(run);impossible.status='queued';impossible.attempts[0].status='queued';
  await assert.rejects(checkedPixelRun(impossible,f.scope,webcrypto));
});

for(const elapsed of ['1e-05','9.125E+2','0.0'])test('V249 peer: valid finite nonnegative elapsed token '+elapsed+' is accepted',async()=>{
  const f=pixelFixture(),run=completed(f);run.result.inference_seconds_text=elapsed;
  assert.equal((await checkedPixelRun(run,f.scope,webcrypto)).result.inference_seconds_text,elapsed);
});

test('V249 peer: unknown POST followed by found=false and pause retains exact original and draft with no automatic POST',()=>harness(async f=>{
  const saved=await seedPending(f);f.state.createMode='unknown';
  const first=f.make();await first.start();
  assert.equal(f.state.posts.length,0);
  await assert.rejects(first.resend(),/未知/);
  assert.equal(f.state.posts.length,1);
  assert.equal(f.state.posts[0].raw,saved.request.raw);
  first.dispose();f.state.status='paused';
  const cold=f.make();await cold.start();
  assert.equal(f.state.posts.length,1);
  assert.equal(cold.pendingMissing,true);
  assert.equal(cold.pending.body.request_id,saved.request.body.request_id);
  assert.equal(f.storage.getItem(f.prefix+'pending'),saved.raw);
  assert.equal(f.storage.getItem(f.prefix+'original:'+saved.request.body.request_id),saved.raw);
  assert.equal(f.storage.getItem(f.prefix+'form'),saved.formRaw);
  assert.equal(cold.options.can_create,false);
  await cold.recover();assert.equal(f.state.posts.length,1);
  const lookup=f.state.readCalls.find(row=>row.path.startsWith(f.base+'/requests?'));
  const query=new URL(lookup.path,'https://peer.invalid').searchParams;
  assert.equal(query.get('request_id'),saved.request.body.request_id);
  assert.equal(query.get('expected_body_hash'),saved.request.body_hash);
  cold.dispose();
}));

for(const status of [404,409])test('V249 peer: recovery '+status+' preserves unknown POST and its exact draft without resending',()=>harness(async f=>{
  const saved=await seedPending(f);f.state.createMode='unknown';
  const first=f.make();await first.start();await assert.rejects(first.resend());first.dispose();
  const count=f.state.posts.length;
  const api=async(path,request)=>{
    if(path.startsWith(f.base+'/requests?'))throw Object.assign(Error('测试恢复无法核对，原申请保留'),{status});
    return f.api(path,request);
  };
  const cold=f.make({api});await cold.start();
  assert.equal(f.state.posts.length,count);
  assert.equal(f.storage.getItem(f.prefix+'pending'),saved.raw);
  assert.equal(f.storage.getItem(f.prefix+'original:'+saved.request.body.request_id),saved.raw);
  assert.equal(f.storage.getItem(f.prefix+'form'),saved.formRaw);
  assert.equal(cold.pending.raw_sha256,saved.request.raw_sha256);
  assert.match(cold.error,/恢复无法核对/);
  await assert.rejects(cold.recover());
  assert.equal(f.state.posts.length,count);
  cold.dispose();
}));
