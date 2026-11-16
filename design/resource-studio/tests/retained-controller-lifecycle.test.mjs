import test from 'node:test';
import assert from 'node:assert/strict';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

function scene(){
 const queue=[];
 const lifecycle=createRetainedControllerLifecycle({defer:callback=>queue.push(callback)});
 const flush=()=>{while(queue.length)queue.shift()();};
 const controller={active:true,denied:false,suspended:false,disposals:0,
  check(){assert.ok(this.active&&!this.denied&&!this.cancelled&&!this.suspended,'controller is fenced');},
  dispose(){this.disposals++;this.active=false;}};
 return {lifecycle,controller,flush};
}

function barrier(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}

test('StrictMode replay retains the lifetime and allows one existing checked request to finish',async()=>{
 const {lifecycle,controller,flush}=scene(),gate=barrier();
 const first=lifecycle.setup(controller,'actor:workspace:handoff');
 let started=0,finished=0;
 const original=(async()=>{controller.check();started++;await gate.promise;controller.check();finished++;})();
 assert.equal(lifecycle.cleanup(first),true);assert.equal(controller.suspended,true);
 assert.equal(lifecycle.isCurrent(first.token),false);
 const second=lifecycle.setup(controller,'actor:workspace:handoff');
 assert.notEqual(second,first);assert.equal(second.token,first.token);
 assert.equal(controller.suspended,false);assert.equal(lifecycle.isCurrent(first.token),true);
 flush();assert.equal(controller.disposals,0);
 gate.resolve();await original;
 assert.equal(started,1);assert.equal(finished,1);assert.equal(controller.active,true);
});

test('true unmount immediately fences an awaited response before deferred disposal',async()=>{
 const {lifecycle,controller,flush}=scene(),gate=barrier();
 const lease=lifecycle.setup(controller,'scope');
 const response=(async()=>{controller.check();await gate.promise;controller.check();})();
 lifecycle.cleanup(lease);gate.resolve();await assert.rejects(response,/fenced/);
 assert.equal(controller.active,true);assert.equal(controller.suspended,true);
 flush();assert.equal(controller.active,false);assert.equal(controller.disposals,1);
 assert.equal(lifecycle.setup(controller,'scope'),null);flush();assert.equal(controller.disposals,1);
});

test('stale and repeated cleanup cannot suspend a synchronous replacement lease',()=>{
 const {lifecycle,controller,flush}=scene();
 const first=lifecycle.setup(controller,'scope');assert.equal(lifecycle.setup(controller,'scope'),first);
 lifecycle.cleanup(first);assert.equal(lifecycle.cleanup(first),false);
 const second=lifecycle.setup(controller,'scope');
 assert.equal(lifecycle.cleanup(first),false);flush();controller.check();
 assert.equal(lifecycle.isCurrent(second.token),true);
 lifecycle.cleanup(second);flush();assert.equal(controller.disposals,1);
});

test('changing scope permanently retires the old controller and cannot resume its request',async()=>{
 const {lifecycle,controller:old,flush}=scene(),gate=barrier();
 const first=lifecycle.setup(old,'actorA:workspace:handoffA');
 const response=(async()=>{old.check();await gate.promise;old.check();})();
 lifecycle.cleanup(first);
 const newer={active:true,denied:false,suspended:false,disposals:0,
  dispose(){this.disposals++;this.active=false;}};
 const second=lifecycle.setup(newer,'actorB:workspace:handoffB');
 assert.ok(second);assert.notEqual(second.token,first.token);
 assert.equal(old.active,false);assert.equal(old.suspended,true);
 gate.resolve();await assert.rejects(response,/fenced/);flush();
 assert.equal(lifecycle.isCurrent(second.token),true);assert.equal(old.disposals,1);
 assert.equal(lifecycle.cleanup(first),false);assert.equal(lifecycle.cancel(first.token),false);
});

test('the same controller cannot migrate to another scope or be manually resurrected',()=>{
 const {lifecycle,controller,flush}=scene();
 const first=lifecycle.setup(controller,'A');lifecycle.cleanup(first);
 assert.equal(lifecycle.setup(controller,'B'),null);assert.equal(controller.active,false);
 controller.active=true;controller.suspended=false;
 assert.equal(lifecycle.setup(controller,'A'),null);flush();
 assert.equal(controller.disposals,1);
});

for(const flag of ['denied','cancelled'])test(flag+' cannot be cleared by a StrictMode resume',()=>{
 const {lifecycle,controller,flush}=scene();
 const first=lifecycle.setup(controller,'scope');lifecycle.cleanup(first);controller[flag]=true;
 assert.equal(lifecycle.setup(controller,'scope'),null);assert.equal(controller.suspended,true);
 assert.equal(controller.active,false);assert.equal(controller.disposals,1);
 controller[flag]=false;controller.active=true;controller.suspended=false;
 assert.equal(lifecycle.setup(controller,'scope'),null);flush();assert.equal(controller.disposals,1);
});

test('explicit cancellation retires the lifetime and queued cleanup cannot dispose twice',()=>{
 const {lifecycle,controller,flush}=scene();
 const lease=lifecycle.setup(controller,'scope');lifecycle.cleanup(lease);
 assert.equal(lifecycle.cancel(lease.token),true);assert.equal(lifecycle.cancel(lease.token),false);
 assert.equal(lifecycle.setup(controller,'scope'),null);flush();
 assert.equal(controller.disposals,1);assert.equal(lifecycle.isCurrent(lease.token),false);
});

test('a disposed controller or a suspension owned elsewhere is never revived',()=>{
 const {lifecycle,controller}=scene();controller.suspended=true;
 assert.equal(lifecycle.setup(controller,'scope'),null);assert.equal(controller.suspended,true);
 controller.suspended=false;controller.dispose();
 assert.equal(lifecycle.setup(controller,'scope'),null);assert.equal(controller.disposals,1);
});

test('an external dispose before the microtask is respected without a second dispose',()=>{
 const {lifecycle,controller,flush}=scene();
 const lease=lifecycle.setup(controller,'scope');lifecycle.cleanup(lease);controller.dispose();
 flush();assert.equal(controller.disposals,1);assert.equal(controller.suspended,true);
 assert.equal(lifecycle.setup(controller,'scope'),null);
});

test('failed cleanup scheduling still leaves the controller fenced and retired',()=>{
 const {controller}=scene();
 const lifecycle=createRetainedControllerLifecycle({defer:()=>{throw Error('scheduler unavailable');}});
 const lease=lifecycle.setup(controller,'scope');
 assert.throws(()=>lifecycle.cleanup(lease),/scheduler unavailable/);
 assert.equal(controller.suspended,true);assert.equal(controller.active,false);
 assert.equal(lifecycle.setup(controller,'scope'),null);
});
