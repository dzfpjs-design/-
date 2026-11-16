// React may synchronously replay an effect without replacing its controller.
// Suspension fences every await immediately. Only that same lifetime may resume;
// disposal, denial and scope changes never create permission to revive it.
export function createRetainedControllerLifecycle({defer=callback=>globalThis.queueMicrotask(callback)}={}){
 if(typeof defer!=='function')throw new TypeError('A microtask scheduler is required.');
 const retired=new WeakSet();let current=null;
 const usable=c=>c!==null&&typeof c==='object'&&c.active===true
  &&c.denied!==true&&c.cancelled!==true&&typeof c.dispose==='function'&&!retired.has(c);

 function retire(entry){
  if(!entry||entry.retired)return;
  entry.retired=true;entry.pending=false;entry.controller.suspended=true;
  retired.add(entry.controller);if(current===entry)current=null;
  // The caller may already have disposed this controller on a scope change.
  // Keep it suspended even when its dispose callback throws.
  try{if(entry.controller.active!==false)entry.controller.dispose();}
  finally{entry.controller.suspended=true;}
 }

 function setup(controller,scopeKey){
  if(typeof scopeKey!=='string'||!scopeKey)throw new TypeError('An exact nonempty scope key is required.');
  const entry=current;
  if(entry&&(entry.controller!==controller||entry.scope!==scopeKey))retire(entry);
  if(!usable(controller)){
   if(current?.controller===controller)retire(current);
   return null;
  }
  if(current){
   // Repeating setup while mounted is idempotent. Replaying after cleanup
   // gets a new lease, so stale cleanup cannot release the new mount.
   if(!current.pending)return current.lease;
   current.pending=false;current.generation++;controller.suspended=false;
   current.lease=Object.freeze({token:current.token});
   return current.lease;
  }
  // An unknown suspension belongs to another lifetime/owner, never this one.
  if(controller.suspended===true)return null;
  const token=Object.freeze({scope:scopeKey});
  current={controller,scope:scopeKey,token,lease:Object.freeze({token}),
   generation:0,pending:false,retired:false};
  return current.lease;
 }

 function cleanup(lease){
  const entry=current;
  if(!entry||entry.retired||entry.lease!==lease||entry.pending)return false;
  entry.pending=true;entry.controller.suspended=true;
  const generation=++entry.generation;
  try{
   defer(()=>{
    if(current===entry&&!entry.retired&&entry.pending&&entry.generation===generation)retire(entry);
   });
  }catch(error){retire(entry);throw error;}
  return true;
 }

 function cancel(token=current?.token){
  if(!current||current.token!==token)return false;
  retire(current);return true;
 }

 function isCurrent(token){
  return !!current&&current.token===token&&!current.retired&&!current.pending
   &&usable(current.controller)&&current.controller.suspended!==true;
 }

 return Object.freeze({setup,cleanup,cancel,isCurrent});
}
