// Only in-flight workspace GET rounds are shared. Results and identity checks
// are never cached after a round settles; every subscriber keeps its own gate.
const rounds=new Map();
const fields=['actor_id','workspace_id','role','instance_id'];
export const WORKSPACE_READ_STALE_V142='WORKSPACE_READ_STALE_V142';
const stale=()=>Object.assign(Error('页面身份或业务范围已变化，原请求和输入保留，未应用旧读取。'),{code:WORKSPACE_READ_STALE_V142});
const need=value=>{if(!value)throw Error('业务读取范围或回调无法核对，尚未开始新的读取。');};

function scopeKey(scope){
 need(scope&&typeof scope==='object'&&!Array.isArray(scope)
  &&[Object.prototype,null].includes(Object.getPrototypeOf(scope))&&Reflect.ownKeys(scope).length===fields.length);
 const values=fields.map(key=>{
  const descriptor=Object.getOwnPropertyDescriptor(scope,key);
  need(descriptor&&Object.hasOwn(descriptor,'value')&&typeof descriptor.value==='string'
   &&descriptor.value.length>0&&descriptor.value.length<=512);return descriptor.value;
 });
 return JSON.stringify(values);
}

function reject(waiter,error){
 if(waiter.done)return;waiter.done=true;waiter.owner.waiters.delete(waiter);waiter.reject(error);
}
function current(waiter){
 if(waiter.done)return false;
 if(waiter.owner.disposed){reject(waiter,stale());return false;}
 try{if(waiter.isCurrent()===true)return true;}catch(error){reject(waiter,error);return false;}
 reject(waiter,stale());return false;
}
function deliver(batch,waiter,kind,value){
 if(batch.finished||!current(waiter))return;
 try{
  const result=waiter[kind](value);
  // One subscriber's callback failure cannot poison another subscriber or
  // silently turn this subscriber's failure into a successful empty read.
  waiter.callbacks.push(Promise.resolve(result).catch(error=>reject(waiter,error)));
 }catch(error){reject(waiter,error);}
}
function publish(batch,kind,value){
 if(batch.finished)return;
 need(!batch.buckets.has(kind));batch.buckets.set(kind,value);
 for(const waiter of batch.waiters)deliver(batch,waiter,kind,value);
}
function complete(slot,batch,successful,value){
 if(batch.finished)return;batch.finished=true;
 for(const waiter of batch.waiters){
  if(!successful){if(current(waiter))reject(waiter,value);continue;}
  Promise.all(waiter.callbacks).then(()=>{
   if(!current(waiter))return;
   waiter.done=true;waiter.owner.waiters.delete(waiter);waiter.resolve(value);
  });
 }
 if(slot.running!==batch)return;
 slot.running=null;
 if(slot.queued){const next=slot.queued;slot.queued=null;start(slot,next);}
 else if(rounds.get(slot.key)===slot)rounds.delete(slot.key);
}
function start(slot,batch){
 slot.running=batch;
 Promise.resolve().then(()=>{
  // Choose a still-current subscriber's captured read callback. In particular,
  // an old queued scope must never borrow the next page's identity or API.
  const owner=batch.waiters.find(waiter=>current(waiter));
  if(!owner){complete(slot,batch,false,stale());return;}
  batch.started=true;
  return Promise.resolve(owner.read(Object.freeze({
   onCurrent:value=>publish(batch,'onCurrent',value),
   onHistory:value=>publish(batch,'onHistory',value),
  }))).then(value=>complete(slot,batch,true,value),error=>complete(slot,batch,false,error));
 }).catch(error=>complete(slot,batch,false,error));
}
const batch=()=>({started:false,finished:false,buckets:new Map(),waiters:[]});

export function createWorkspaceReadCoalescerV142(){
 const owner={disposed:false,waiters:new Set()};
 return Object.freeze({
  async load(scope,read,{freshAfterCurrent=false,isCurrent,onCurrent=()=>{},onHistory=()=>{}}={}){
   const key=scopeKey(scope);
   need(typeof read==='function'&&typeof isCurrent==='function'&&typeof onCurrent==='function'
    &&typeof onHistory==='function'&&typeof freshAfterCurrent==='boolean');
   if(owner.disposed)throw stale();
   return new Promise((resolve,rejectPromise)=>{
    const waiter={owner,read,isCurrent,onCurrent,onHistory,resolve,reject:rejectPromise,done:false,callbacks:[]};
    owner.waiters.add(waiter);
    if(!current(waiter))return;
    let slot=rounds.get(key);
    if(!slot){slot={key,running:null,queued:null};rounds.set(key,slot);}
    let target=slot.queued||slot.running;
    if(!target)target=batch();
    else if(freshAfterCurrent&&target===slot.running&&target.started){
     target=batch();slot.queued=target;
    }
    target.waiters.push(waiter);
    // Only an in-flight round can replay an already finished bucket. A manual
    // or write-following subscriber joins the future round, never this data.
    for(const [kind,value] of target.buckets)deliver(target,waiter,kind,value);
    if(!slot.running)start(slot,target);
   });
  },
  dispose(){
   if(owner.disposed)return;owner.disposed=true;
   for(const waiter of [...owner.waiters])reject(waiter,stale());
   // No AbortController, POST, draft deletion or hidden automatic retry. An
   // actual shared read can finish for other subscribers; queued stale-only
   // rounds reject without starting any HTTP request.
  },
 });
}
