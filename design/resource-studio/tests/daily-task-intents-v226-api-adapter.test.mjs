import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {dailyFixture} from './daily-task-intents-v226-fixture.mjs';

test('actual runtime json adapter sends immutable enable and Unicode pause bytes with real CSRF and workspace headers',{concurrency:false},async()=>{
 const f=dailyFixture(),before=new Map(['sessionStorage','fetch'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)])),actual=[],NativeResponse=globalThis.Response;let rejectNext=false,c;
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,writable:true,value:f.session});
 Object.defineProperty(globalThis,'fetch',{configurable:true,writable:true,value:async(url,options={})=>{
  assert.ok(typeof url==='string'&&url.startsWith('/api/'));assert.equal(options.credentials,'same-origin');const req=new Request('https://synthetic-api-adapter.invalid'+url,options),bytes=new Uint8Array(await req.arrayBuffer()),raw=new TextDecoder().decode(bytes);assert.equal(req.headers.get('X-Workspace-ID'),f.account.workspace_id);assert.equal(req.headers.get('X-CSRF-Token'),'daily-test-csrf');
  if(options.method==='POST'&&!url.endsWith('/preview')){const nonce=JSON.parse(raw).request_id,p=JSON.parse(f.storage.getItem(c.pendingKey));assert.equal(raw,p.raw);assert.equal(createHash('sha256').update(bytes).digest('hex'),p.sha256);assert.ok(f.storage.getItem(c.prefix+'original:'+nonce));assert.equal(req.headers.get('Content-Type'),'application/json');actual.push({url,raw,sha:p.sha256});if(rejectNext)return new NativeResponse(JSON.stringify({detail:'当前登录已经失效'}),{status:401,headers:{'Content-Type':'application/json'}});}
  try{const result=await f.api(url.slice(4),{...options,...(bytes.length?{body:raw}:{})});return new NativeResponse(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});}catch(error){return new NativeResponse(JSON.stringify({detail:error.message}),{status:error.status||503,headers:{'Content-Type':'application/json'}});}
 }});
 try{const runtime=await import('../src/runtime-api.js?daily_v226_adapter');runtime.setSession('daily-test-csrf',f.account.workspace_id,f.account.user_id,f.account.username);c=f.make({api:runtime.json});await c.start();f.fill(c);await c.prepare();await c.enable(true);const intent=c.intents[0];await c.pause(intent.id,'只暂停后续发起；当前人工说明与原任务继续保留。');assert.equal(actual.length,2);assert.equal(JSON.parse(actual[0].raw).confirm_scope,true);assert.equal(JSON.parse(actual[1].raw).reason,'只暂停后续发起；当前人工说明与原任务继续保留。');assert.equal(c.pending,null);await c.prepare();rejectNext=true;await assert.rejects(c.enable(true),/登录已经失效/);assert.equal(c.denied,true);assert.ok(c.pending,'401 never erases the original posted nonce');assert.equal(f.state.intents.length,1,'401 did not manufacture an enabled range');assert.equal(c.intents.length,0,'old scope details hidden after actual adapter 401');}
 finally{c?.dispose();for(const[k,d]of before){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
