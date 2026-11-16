// Storage only. These records preserve work; only an actual server read settles
// an operation. Native and processing source families never share a slot.
export const NATIVE_SOURCE_MAX_RECORD_V136=256*1024;
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const need=(value,message='本机原稿或原请求无法核对；原记录保留，暂未提交。')=>{if(!value)throw Error(message);};
const copy=value=>JSON.parse(JSON.stringify(value));
const same=(left,right)=>left===right||(Array.isArray(left)&&Array.isArray(right)?left.length===right.length&&left.every((value,index)=>same(value,right[index])):object(left)&&object(right)&&Object.keys(left).length===Object.keys(right).length&&Object.keys(left).every(key=>Object.hasOwn(right,key)&&same(left[key],right[key])));
export function nativeSourceScopeV136(value){
 need(exact(value,['actor_id','workspace_id','instance_id'])&&Object.values(value).every(item=>typeof item==='string'&&UUID.test(item)));
 return Object.freeze({...value});
}
export const nativeSourcePrefixV136=scope=>'ga-native-graph-sources-v136:'+scope.workspace_id+':'+scope.actor_id+':'+scope.instance_id+':';
export class NativeGraphSourceJournalV136{
 constructor({scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,checkForm,checkRequest}){
  Object.assign(this,{scope:nativeSourceScopeV136(scope),storage,session,locks,crypto,checkForm,checkRequest});
  this.prefix=nativeSourcePrefixV136(this.scope);this.owner=crypto.randomUUID();this.generation=0;this.windowID=null;
  this.opened=false;this.tabReady=false;this.error='';this.entry=null;
 }
 write(store,key,raw){
  need(typeof raw==='string'&&raw.length<=NATIVE_SOURCE_MAX_RECORD_V136,'本页输入过长，原稿保留；请缩短说明后重试保存。');
  store.setItem(key,raw);need(store.getItem(key)===raw,'本机写入后未能完整读回，原输入保留，暂不发送。');
 }
 once(key,value){const raw=JSON.stringify(value),prior=this.storage.getItem(key);need(prior===null||prior===raw,'原保存槽已有不同内容，原记录未被覆盖。');this.write(this.storage,key,raw);return raw;}
 canSend(){return this.opened&&this.tabReady&&typeof this.locks?.request==='function';}
 lock(callback){
  need(typeof this.locks?.request==='function','此浏览器缺少安全的多窗口协调能力；可查看原记录，暂不能发送或结清操作。');
  return this.locks.request(this.prefix+'operation-lock',{mode:'exclusive'},callback);
 }
 requestKey(nonce){need(UUID.test(nonce));return this.prefix+'request:'+nonce;}
 retirementKey(nonce){need(UUID.test(nonce));return this.prefix+'retirement:'+nonce;}
 readRequest(key){
  need(typeof key==='string'&&key.startsWith(this.prefix));
  const raw=this.storage.getItem(key);need(typeof raw==='string'&&raw.length<=NATIVE_SOURCE_MAX_RECORD_V136);
  const value=JSON.parse(raw);this.checkRequest(value,this.scope);
  need(key===(value.kind==='retirement'?this.retirementKey(value.body.request_id):this.requestKey(value.body.request_id)));
  return {key,raw,value};
 }
 head(name){
  const raw=this.storage.getItem(this.prefix+name);if(raw===null)return null;
  const value=JSON.parse(raw);need(exact(value,['key','request_id'])&&UUID.test(value.request_id));
  const record=this.readRequest(value.key);need(record.value.body.request_id===value.request_id);
  return {...record,headRaw:raw};
 }
 open(blank){
  // The controller invokes this only after fresh /me. Reading a broken pointer
  // never writes an empty replacement over the shared original request.
  let form=this.checkForm(blank);
  try{
   this.windowID=this.session.getItem(this.prefix+'window');
   if(this.windowID===null){this.windowID=this.crypto.randomUUID();this.write(this.session,this.prefix+'window',this.windowID);}
   need(UUID.test(this.windowID));
   const raw=this.session.getItem(this.prefix+'form-head');
   if(raw!==null){
    const head=JSON.parse(raw);need(exact(head,['schema','scope','window_id','owner','generation','form'])
     &&head.schema==='native-graph-source-draft-v136'&&same(head.scope,this.scope)&&head.window_id===this.windowID
     &&UUID.test(head.owner)&&Number.isSafeInteger(head.generation)&&head.generation>=0);
    form=this.checkForm(head.form);
   }
   // A fresh owner keeps a duplicated tab from reusing the old tab's slots.
   this.write(this.session,this.prefix+'tab-owner',this.owner);this.tabReady=true;
  }catch{this.tabReady=false;this.error='本窗口的恢复指针未能保存或读取。共享原请求仍保留可查，暂不能发送。';}
  this.entry=copy(form);this.opened=true;
  return {form,pending:this.head('active'),retirement:this.head('retiring')};
 }
 save(form){
  form=this.checkForm(form);need(this.opened,'本窗口尚未核对恢复记录。');
  if(!this.tabReady){
   const original=this.session.getItem(this.prefix+'window');need(original===null||UUID.test(original));
   this.windowID=original||this.windowID||this.crypto.randomUUID();this.write(this.session,this.prefix+'window',this.windowID);
   this.write(this.session,this.prefix+'tab-owner',this.owner);this.tabReady=true;
  }
  need(this.session.getItem(this.prefix+'tab-owner')===this.owner,'窗口保存归属已变化，当前人稿保留，暂未覆盖。');
  const next=this.generation+1,row={schema:'native-graph-source-draft-v136',scope:this.scope,
   window_id:this.windowID,owner:this.owner,generation:next,form:copy(form)};
  this.once(this.prefix+'form:entry:'+this.owner,{...row,generation:0,form:this.entry});
  const key=this.prefix+'form:slot:'+this.owner+':'+(next%2),prior=this.storage.getItem(key);
  if(prior!==null){const old=JSON.parse(prior);need(old.schema===row.schema&&same(old.scope,this.scope)&&old.owner===this.owner
   &&old.window_id===this.windowID&&Number.isSafeInteger(old.generation)&&old.generation<next);this.checkForm(old.form);}
  need(this.storage.getItem(key)===prior,'保存槽已被其它窗口更新，原人稿保留。');
  this.write(this.storage,key,JSON.stringify(row));this.generation=next;
  this.write(this.session,this.prefix+'form-head',JSON.stringify(row));return copy(form);
 }
 async install(value,authorize){
  this.checkRequest(value,this.scope);need(this.canSend(),'恢复指针或多窗口协调尚不可用，原稿保留，暂不能提交。');
  return this.lock(async()=>{
   await authorize();const pending=this.head('active'),retiring=this.head('retiring');
   if(value.kind==='retirement'){
    need(pending&&pending.value.body.request_id===value.body.original.request_id&&!retiring,'原操作已变化或已有待核的停止请求。');
   }else need(!retiring&&(!pending||same(pending.value,value)),'另一窗口已有原操作待核对，当前人稿保留；请先找回原请求。');
   const key=value.kind==='retirement'?this.retirementKey(value.body.request_id):this.requestKey(value.body.request_id);
   const raw=this.once(key,value),head={key,request_id:value.body.request_id};
   this.write(this.storage,this.prefix+(value.kind==='retirement'?'retiring':'active'),JSON.stringify(head));
   need(this.readRequest(key).raw===raw);return {key,raw,value:copy(value)};
  });
 }
 async withOriginal(record,authorize,callback){
  need(this.canSend(),'本窗口的恢复指针或多窗口协调不可用；原请求可查，暂不重发。');
  return this.lock(async()=>{
   await authorize();const current=this.readRequest(record.key);need(current.raw===record.raw,'选中的原保存槽已变化，未发送。');
   const head=this.head(record.value.kind==='retirement'?'retiring':'active');
   need(head?.key===record.key&&head.raw===record.raw,'原操作已被另一窗口核对或保存，先读取原结果。');
   if(record.value.kind!=='retirement')need(!this.head('retiring'),'原操作正在核对结束结果，不能同时重发。');
   return callback(current.value);
  });
 }
 async settle(record,hint,authorize){
  // hint has already been checked against the actual HTTP ledger response. It
  // is only a local completion hint; a later mount must check the server again.
  return this.lock(async()=>{
   await authorize();need(this.readRequest(record.key).raw===record.raw,'原保存槽已变化，未清除。');
   this.once(this.prefix+'completed:'+record.value.body.request_id,{schema:'native-graph-source-completed-v136',scope:this.scope,
    request_id:record.value.body.request_id,original_key:record.key,outcome:hint.outcome,server_id:hint.server_id});
   const active=this.head('active');
   if(active?.key===record.key&&active.raw===record.raw){
    need(this.storage.getItem(this.prefix+'active')===active.headRaw);this.storage.removeItem(this.prefix+'active');need(this.storage.getItem(this.prefix+'active')===null);
   }
   if(record.value.kind==='retirement'){
    const original=this.head('active');
    if(original?.value.body.request_id===record.value.body.original.request_id){
     need(JSON.stringify(original.value.body)===JSON.stringify(record.value.body.original));
     this.once(this.prefix+'completed:'+original.value.body.request_id,{schema:'native-graph-source-completed-v136',scope:this.scope,
      request_id:original.value.body.request_id,original_key:original.key,outcome:hint.outcome,server_id:hint.server_id});
     need(this.storage.getItem(this.prefix+'active')===original.headRaw);this.storage.removeItem(this.prefix+'active');need(this.storage.getItem(this.prefix+'active')===null);
    }
    const retiring=this.head('retiring');if(retiring?.key===record.key&&retiring.raw===record.raw){
     need(this.storage.getItem(this.prefix+'retiring')===retiring.headRaw);this.storage.removeItem(this.prefix+'retiring');need(this.storage.getItem(this.prefix+'retiring')===null);
    }
   }
  });
 }
 restore(record){
  need(this.readRequest(record.key).raw===record.raw,'选中记录已被替换，请重新读取本机记录。');
  return this.head('active');
 }
 async adopt(record,authorize){
  need(this.canSend(),'恢复指针或多窗口协调不可用，原记录保留，暂不能接回发送。');
  return this.lock(async()=>{
   await authorize();const current=this.readRequest(record.key);need(current.raw===record.raw,'选中记录已被替换，请重新读取。');
   const retiring=this.head('retiring'),active=this.head('active');
   if(current.value.kind==='retirement'){
    need(!retiring||retiring.raw===current.raw);need(!active||same(active.value.body,current.value.body.original));
    this.write(this.storage,this.prefix+'retiring',JSON.stringify({key:record.key,request_id:current.value.body.request_id}));
   }else{
    need(!retiring&&(!active||active.raw===current.raw),'另一原操作仍待核对，未覆盖其恢复入口。');
    this.write(this.storage,this.prefix+'active',JSON.stringify({key:record.key,request_id:current.value.body.request_id}));
   }
   return current;
  });
 }
 async originalWon(original,retirement,hint,authorize){
  // A verified saved original makes this retirement impossible on the server.
  // Preserve its raw body as history, but never call it a saved retirement.
  return this.lock(async()=>{
   await authorize();need(this.readRequest(original.key).raw===original.raw&&this.readRequest(retirement.key).raw===retirement.raw);
   need(same(original.value.body,retirement.value.body.original));
   this.once(this.prefix+'completed:'+original.value.body.request_id,{schema:'native-graph-source-completed-v136',scope:this.scope,
    request_id:original.value.body.request_id,original_key:original.key,outcome:hint.outcome,server_id:hint.server_id});
   for(const [name,record] of [['active',original],['retiring',retirement]]){
    const current=this.head(name);if(current?.key===record.key&&current.raw===record.raw){need(this.storage.getItem(this.prefix+name)===current.headRaw);this.storage.removeItem(this.prefix+name);need(this.storage.getItem(this.prefix+name)===null);}
   }
  });
 }
}
