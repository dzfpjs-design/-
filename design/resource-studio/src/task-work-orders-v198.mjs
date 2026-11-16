// Human notes and exact original commands, scoped to one account/workspace/Task.
// The server inventory establishes authority; this module never infers it from roles.
export const WORK_ORDERS_BASE='/workspace/work-orders-v198';
export const WORK_ORDER_OPERATIONS=['progress','submit','return','cancel'];
export const WORK_ORDER_STATUSES={assigned:'待办理',in_progress:'办理中',completion_reported:'办理结果已提交',returned:'待补充',cancelled:'已取消'};
export const WORK_ORDER_ACTIONS={progress:'记录进展',submit:'提交办理结果',return:'退回补充',cancel:'取消工作单'};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HASH=/^[a-f0-9]{64}$/,MAX_BYTES=128*1024;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const copy=v=>JSON.parse(JSON.stringify(v));
const sorted=v=>Array.isArray(v)?v.map(sorted):object(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])])):v;
const same=(a,b)=>JSON.stringify(sorted(a))===JSON.stringify(sorted(b));
const need=(ok,message='原工作单或原请求无法核对；输入与原记录仍保留。')=>{if(!ok)throw Error(message);};
const text=(v,max)=>typeof v==='string'&&Array.from(v).length<=max&&!v.includes('\0');
const version=v=>Number.isSafeInteger(v)&&v>=1&&v<=1000;
const time=v=>typeof v==='string'&&v.length<=80&&v.includes('T')&&Number.isFinite(Date.parse(v));
const flags=v=>v?.execution_completed===false&&v.independent_acceptance===false&&v.world_state_verified===false;
const slot=id=>'order:'+id;
export function workOrderScope(value){need(exact(value,['actor_id','workspace_id','task_id'])&&Object.values(value).every(v=>typeof v==='string'&&UUID.test(v)));return copy(value);}
export const workOrderScopeKey=scope=>{const value=workOrderScope(scope);return [value.actor_id,value.workspace_id,value.task_id].join(':');};
function note(value){need(text(value,12000),'说明超过12000字或包含无效字符；原输入未截断。');return value;}
function draftNote(value){need(typeof value==='string','本机说明格式无法核对，原记录保留。');return value;}
function permissions(value){need(exact(value,WORK_ORDER_OPERATIONS)&&WORK_ORDER_OPERATIONS.every(k=>typeof value[k]==='boolean'));return value;}
function definitiveRejection(value,pending){return exact(value,['code','not_applied','current_version','request_id','body_sha256','item_id','operation'])&&value.code==='workflow_work_order_version_conflict'&&value.not_applied===true&&version(value.current_version)&&value.request_id===pending.body.request_id&&value.body_sha256===pending.sha256&&value.item_id===pending.item_id&&value.operation===pending.operation;}
export function checkedWorkOrder(value,scope,{withPermissions=true}={}){
 const keys=['schema','id','batch_id','parent_task_id','title','description','expected_result','assignee_id','assignee_name','due_at','simulation_only','scope','status','version','note','updated_at','history','execution_completed','independent_acceptance','world_state_verified',...(withPermissions?['permissions']:[])];
 need(exact(value,keys)&&value.schema==='workflow-work-order-view-v198'&&UUID.test(value.id)&&UUID.test(value.batch_id)&&value.parent_task_id===scope.task_id&&UUID.test(value.assignee_id)&&value.scope==='human_work_orders'&&Object.hasOwn(WORK_ORDER_STATUSES,value.status)&&version(value.version)&&flags(value)&&typeof value.simulation_only==='boolean'&&time(value.due_at)&&time(value.updated_at));
 need(text(value.title,200)&&/\S/u.test(value.title)&&text(value.description,4000)&&/\S/u.test(value.description)&&text(value.expected_result,2000)&&/\S/u.test(value.expected_result)&&text(value.assignee_name,1000)&&value.assignee_name.length>0);note(value.note);
 need(Array.isArray(value.history)&&value.history.length===value.version);
 for(const [i,row]of value.history.entries()){
  need(exact(row,['version','status','operation','note','actor_id','actor_name','at','request_id'])&&row.version===i+1&&Object.hasOwn(WORK_ORDER_STATUSES,row.status)&&[...WORK_ORDER_OPERATIONS,'assigned'].includes(row.operation)&&UUID.test(row.actor_id)&&text(row.actor_name,1000)&&time(row.at)&&(row.request_id===null||UUID.test(row.request_id)));note(row.note);
 }
 const last=value.history.at(-1);need(last.status===value.status&&last.note===value.note&&last.at===value.updated_at);
 if(withPermissions)permissions(value.permissions);return copy(value);
}
export function checkedWorkOrderList(value,scope,mode){
 need(exact(value,['schema','scope','mode','items','execution_completed','independent_acceptance','world_state_verified'])&&value.schema==='workflow-work-order-list-v198'&&value.scope==='human_work_orders'&&value.mode===mode&&flags(value)&&Array.isArray(value.items));
 const ids=new Set();for(const item of value.items){checkedWorkOrder(item,scope);need(!ids.has(item.id));ids.add(item.id);}return copy(value);
}
function checkedResult(value,scope,pending){
 need(exact(value,['schema','request_id','item','execution_completed','independent_acceptance','world_state_verified'])&&value.schema==='workflow-work-order-result-v198'&&value.request_id===pending.body.request_id&&flags(value));
 const item=checkedWorkOrder(value.item,scope,{withPermissions:false}),last=item.history.at(-1);
 need(item.id===pending.item_id&&item.version===pending.body.expected_version+1&&item.note===pending.body.note&&last.request_id===pending.body.request_id&&last.actor_id===scope.actor_id&&last.operation===pending.operation&&item.status===({progress:'in_progress',submit:'completion_reported',return:'returned',cancel:'cancelled'})[pending.operation]);return copy(value);
}

export class TaskWorkOrdersController{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,crypto=globalThis.crypto,locks=globalThis.navigator?.locks,onChange=()=>{},onSaved=async()=>{}}){
  Object.assign(this,{api,scope:workOrderScope(scope),storage,session,crypto,locks,onChange,onSaved});
  this.prefix='ga-task-work-orders-v198:'+workOrderScopeKey(scope)+':';this.outboxKey=this.prefix+'outbox';
  this.state={schema:'task-work-order-draft-v198',scope:this.scope,mode:'assigned',selected:null,forms:{},rejections:[]};
  this.items=[];this.detail=null;this.pending=null;this.error='';this.notice='';this.loaded=false;this.localSaved=false;this.busy=false;this.active=false;this.generation=0;this.denied=false;this.verified=false;this.protected=false;
 }
 get readable(){return this.active&&this.verified&&!this.denied;}
 get form(){return this.state.selected?this.state.forms[slot(this.state.selected)]||null:null;}
 get canWriteNote(){return this.readable&&!this.busy&&!this.pending&&!!this.detail&&WORK_ORDER_OPERATIONS.some(k=>this.detail.permissions[k]===true);}
 notify(){this.onChange();}
 mount(){this.active=true;this.generation++;this.busy=false;this.verified=false;this.opening=null;}
 dispose(){this.active=false;this.generation++;this.opening=null;}
 conceal(message){this.denied=true;this.verified=false;this.error=message;this.notify();}
 check(token=this.generation){need(this.active&&token===this.generation&&!this.denied,'页面已关闭或账号已变化；原输入保留。');if(this.sessionPin){let values;try{values=this.sessionValues();}catch{this.conceal('当前会话不能核对，工作单与输入已隐藏。');throw Error(this.error);}if(!same(values,this.sessionPin)){this.conceal('账号、工作区或会话已变化，工作单与输入已隐藏。');throw Error(this.error);}}}
 sessionValues(){return Object.fromEntries(['user','workspace','csrf'].map(k=>[k,this.session.getItem('ga-setup-csrf'+(k==='csrf'?'':'-'+k))]));}
 async identity(token=this.generation){
  this.check(token);if(!this.sessionPin){const pin=this.sessionValues();need(pin.user===this.scope.actor_id&&pin.workspace===this.scope.workspace_id&&typeof pin.csrf==='string'&&pin.csrf.length>0,'请先重新核对当前登录账号。');this.sessionPin=pin;}
  try{const me=await this.api('/me',{headers:{'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.sessionPin.csrf}});this.check(token);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id){this.conceal('账号或工作区已变化，工作单与输入已隐藏。');throw Error(this.error);}this.verified=true;return me;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal('当前账号无权读取原工作单，内容已隐藏。');throw error;}
 }
 async request(path,options={},token=this.generation){await this.identity(token);try{const value=await this.api(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.sessionPin.csrf}});this.check(token);await this.identity(token);return value;}catch(error){if(this.active&&token===this.generation&&[401,403].includes(error.status))this.conceal('当前工作单权限已变化，原内容已隐藏。');throw error;}}
 write(key,value){const raw=JSON.stringify(value);this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'本机保存后不能读回；暂不提交服务器，请保留输入。');}
 initializeLocal(){
  try{
   let tab=this.session.getItem('ga-task-work-orders-v198-tab');if(!tab){tab=this.crypto.randomUUID();this.session.setItem('ga-task-work-orders-v198-tab',tab);need(this.session.getItem('ga-task-work-orders-v198-tab')===tab);}need(UUID.test(tab));this.draftKey=this.prefix+'draft:'+tab;
   const raw=this.storage.getItem(this.draftKey);
   if(raw!==null){const state=JSON.parse(raw);if(exact(state,['schema','scope','mode','selected','forms']))state.rejections=[];need(exact(state,['schema','scope','mode','selected','forms','rejections'])&&state.schema===this.state.schema&&same(state.scope,this.scope)&&['assigned','dispatched'].includes(state.mode)&&(state.selected===null||UUID.test(state.selected))&&object(state.forms)&&Array.isArray(state.rejections));for(const[key,f]of Object.entries(state.forms)){need(key.startsWith('order:')&&UUID.test(key.slice(6))&&exact(f,['version','baseline','note','dirty','conflict'])&&version(f.version)&&typeof f.dirty==='boolean'&&typeof f.conflict==='boolean');note(f.baseline);draftNote(f.note);need(f.dirty===(f.note!==f.baseline));}for(const row of state.rejections){need(exact(row,['request_id','item_id','operation','sha256','previous_version','current_version','note'])&&UUID.test(row.request_id)&&UUID.test(row.item_id)&&WORK_ORDER_OPERATIONS.includes(row.operation)&&HASH.test(row.sha256)&&version(row.previous_version)&&version(row.current_version));note(row.note);}this.state=copy(state);}
   this.reloadPending();this.protected=false;this.persist();
  }catch(error){this.localSaved=false;this.protected=true;this.error='本机恢复记录暂不能安全读写；服务器读取仍可重试，暂不提交。'+error.message;}
 }
 persist(){if(this.protected)return false;try{need(this.draftKey);this.write(this.draftKey,this.state);if(this.pending){this.checkedPending(this.pending);need(this.storage.getItem(this.outboxKey)===JSON.stringify(this.pending),'原请求落盘状态变化，暂不能离开。');}this.localSaved=true;return true;}catch(error){this.localSaved=false;this.error='本机保存失败，输入仍在本页；请重试保护或导出恢复记录。'+error.message;return false;}}
 safeToLeave(){if(this.busy)return false;return this.localSaved||this.persist();}
 checkedPending(value){
  need(object(value)&&value.schema==='task-work-order-outbox-v198'&&same(value.scope,this.scope)&&UUID.test(value.item_id)&&WORK_ORDER_OPERATIONS.includes(value.operation)&&exact(value.body,['request_id','expected_version','note'])&&UUID.test(value.body.request_id)&&version(value.body.expected_version)&&typeof value.raw==='string'&&HASH.test(value.sha256)&&same(JSON.parse(value.raw),value.body)&&new TextEncoder().encode(value.raw).byteLength<=MAX_BYTES);note(value.body.note);need(/\S/u.test(value.body.note));if(Object.hasOwn(value,'rejection'))need(definitiveRejection(value.rejection,value));return copy(value);
 }
 reloadPending(){const raw=this.storage.getItem(this.outboxKey);if(raw===null){need(!this.pending,'待核对原请求被另一窗口移除，本页仍保留原件。');return null;}const saved=this.checkedPending(JSON.parse(raw));if(this.pending)need(saved.raw===this.pending.raw&&saved.sha256===this.pending.sha256);this.pending=saved;return saved;}
 async hash(raw){return [...new Uint8Array(await this.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(v=>v.toString(16).padStart(2,'0')).join('');}
 async operation(run){need(!this.busy,'正在核对原请求，请稍候。');const token=this.generation;this.check(token);this.busy=true;this.error='';this.notify();try{return await run(token);}catch(error){if(this.active&&token===this.generation){this.error=error.message;this.notify();}throw error;}finally{if(this.active&&token===this.generation){this.busy=false;this.notify();}}}
 async open(){if(this.opening)return this.opening;const token=this.generation;const promise=this.operation(async()=>{await this.identity(token);this.initializeLocal();await this.load(token);if(this.pending)await this.recoverOriginal(token);});this.opening=promise;try{return await promise;}finally{if(token===this.generation)this.opening=null;}}
 ingestDetail(value){
  this.detail=checkedWorkOrder(value,this.scope);this.state.selected=value.id;
  const key=slot(value.id),f=this.state.forms[key];if(!f||!f.dirty)this.state.forms[key]={version:value.version,baseline:value.note,note:value.note,dirty:false,conflict:false};else f.conflict=f.version!==value.version;
  this.persist();this.notify();return this.detail;
 }
 async load(token=this.generation){
  const query=new URLSearchParams({mode:this.state.mode,parent_task_id:this.scope.task_id});const value=checkedWorkOrderList(await this.request(WORK_ORDERS_BASE+'?'+query,{},token),this.scope,this.state.mode);this.check(token);this.items=value.items;this.loaded=true;
  const selected=this.state.selected;if(selected&&this.items.some(row=>row.id===selected)){this.ingestDetail(await this.request(WORK_ORDERS_BASE+'/'+selected,{},token));}else{this.detail=null;this.state.selected=null;this.persist();this.notify();}return value;
 }
 refresh(){return this.operation(token=>this.load(token));}
 setMode(mode){need(['assigned','dispatched'].includes(mode));return this.operation(async token=>{need(this.safeToLeave()||this.localSaved,'原输入尚未保护，请先重试本机保存。');this.state.mode=mode;this.detail=null;this.state.selected=null;this.persist();await this.load(token);});}
 select(id){return this.operation(async token=>{need(UUID.test(id)&&this.items.some(row=>row.id===id));if(!this.localSaved)need(this.persist());return this.ingestDetail(await this.request(WORK_ORDERS_BASE+'/'+id,{},token));});}
 edit(value){this.check();need(this.canWriteNote&&this.form,'当前只读或原请求尚待核对，不能修改原命令。');draftNote(value);this.form.note=value;this.form.dirty=value!==this.form.baseline;this.persist();this.notify();}
 keepInputOnLatest(){this.check();need(this.canWriteNote&&this.form?.conflict&&!this.pending);this.write(this.prefix+'retained:'+this.crypto.randomUUID(),{scope:this.scope,item_id:this.detail.id,form:copy(this.form)});this.form.version=this.detail.version;this.form.baseline=this.detail.note;this.form.dirty=this.form.note!==this.form.baseline;this.form.conflict=false;this.persist();this.notify();}
 async locked(run){need(this.locks?.request,'当前浏览器不能保证多窗口原请求一致；输入保留，暂不能提交。');return this.locks.request(this.prefix+'command',{mode:'exclusive'},run);}
 submit(operation){return this.operation(token=>this.locked(async()=>{
  await this.identity(token);need(WORK_ORDER_OPERATIONS.includes(operation)&&!this.reloadPending()&&this.detail?.permissions?.[operation]===true&&this.form&&!this.form.conflict&&this.localSaved&&!this.protected,'请先核对当前权限、版本及本机保存。');note(this.form.note);need(/\S/u.test(this.form.note),'请填写本次进展、结果或原因。');
  const current=checkedWorkOrder(await this.request(WORK_ORDERS_BASE+'/'+this.detail.id,{},token),this.scope);need(current.permissions[operation]===true,'服务器未允许当前操作。');if(current.version!==this.form.version){this.form.conflict=true;this.detail=current;this.persist();throw Error('工作单已更新，原输入保留；请先对照最新版。');}
  const body={request_id:this.crypto.randomUUID(),expected_version:this.form.version,note:this.form.note},raw=JSON.stringify(body);need(new TextEncoder().encode(raw).byteLength<=MAX_BYTES,'完整请求超过128KiB，原输入未截断。');
  const pending={schema:'task-work-order-outbox-v198',scope:copy(this.scope),item_id:this.detail.id,operation,body,raw,sha256:await this.hash(raw)};this.check(token);this.checkedPending(pending);
  this.write(this.prefix+'original:'+body.request_id,pending);this.write(this.outboxKey,pending);this.pending=pending;need(this.persist(),this.error);need(await this.hash(pending.raw)===pending.sha256);
  try{const result=checkedResult(await this.request(WORK_ORDERS_BASE+'/'+pending.item_id+'/'+operation,{method:'POST',body:pending.raw},token),this.scope,pending);this.pending={...pending,direct_result:result};this.write(this.outboxKey,this.pending);need(this.persist());}
  catch(error){if(error.status===409&&definitiveRejection(error.detail,pending)){this.pending={...this.pending,rejection:copy(error.detail)};this.write(this.outboxKey,this.pending);this.persist();this.notice='服务器明确拒绝了这份旧版本提交。原说明和原请求保留，请主动核对最新版后再编辑；未重新提交。';}else this.notice='本次提交结果尚未核对；原编号、原文和说明已保留。请仅核对原请求，勿重复提交。';this.notify();throw error;}
  return this.recoverOriginal(token);
 }));}
 recover(){return this.operation(token=>this.locked(()=>this.recoverOriginal(token)));}
 useRejectedLatest(){return this.operation(token=>this.locked(async()=>{
  await this.identity(token);const pending=this.reloadPending();need(pending&&definitiveRejection(pending.rejection,pending),'这份原请求尚无明确的未保存证明，仅允许查询原号。');
  const original=await this.recoverOriginal(token);if(original.found)return original;
  const current=checkedWorkOrder(await this.request(WORK_ORDERS_BASE+'/'+pending.item_id,{},token),this.scope);need(current.version>=pending.rejection.current_version&&!current.history.some(row=>row.request_id===pending.body.request_id));
  const retained={original:pending,latest:current,definitively_not_applied:true};this.write(this.prefix+'rejected:'+pending.body.request_id,retained);
  const row={request_id:pending.body.request_id,item_id:pending.item_id,operation:pending.operation,sha256:pending.sha256,previous_version:pending.body.expected_version,current_version:current.version,note:pending.body.note};
  const prior=this.state.rejections.find(value=>value.request_id===row.request_id);if(prior)need(same(prior,row));else this.state.rejections.push(row);need(this.persist());
  need(this.storage.getItem(this.outboxKey)===JSON.stringify(this.pending));this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);this.pending=null;
  this.state.forms[slot(pending.item_id)]={version:current.version,baseline:current.note,note:pending.body.note,dirty:pending.body.note!==current.note,conflict:false};this.ingestDetail(current);need(this.persist());
  this.notice='被拒原请求和完整说明已留存；现在按最新版继续编辑。请查看差异后自行选择操作，尚未重新提交。';this.notify();return current;
 }));}
 async recoverOriginal(token=this.generation){
  const pending=this.reloadPending();need(pending,'没有待核对的原请求。');need(await this.hash(pending.raw)===pending.sha256);this.check(token);
  const value=await this.request(WORK_ORDERS_BASE+'/requests/'+pending.body.request_id+'?'+new URLSearchParams({expected_sha256:pending.sha256}),{},token);
  need(exact(value,['schema','request_id','found','body_sha256','result'])&&value.schema==='workflow-work-order-original-result-v198'&&value.request_id===pending.body.request_id&&typeof value.found==='boolean'&&value.body_sha256===pending.sha256);
  if(!value.found){need(value.result===null);this.notice='尚未查到原请求；这不能证明未提交。原编号和输入保留，仅继续查询，不另发请求。';this.notify();return value;}
  const result=checkedResult(value.result,this.scope,pending);if(pending.direct_result)need(same(result,pending.direct_result));
  const current=checkedWorkOrder(await this.request(WORK_ORDERS_BASE+'/'+pending.item_id,{},token),this.scope);need(current.version>=result.item.version&&same(current.history.slice(0,result.item.version),result.item.history),'原保存结果与工作单历史不一致，原请求保留。');
  for(const key of ['id','batch_id','parent_task_id','title','description','expected_result','assignee_id','assignee_name','due_at','simulation_only'])need(same(current[key],result.item[key]));
  this.write(this.prefix+'completed:'+pending.body.request_id,{original:pending,result});
  need(this.storage.getItem(this.outboxKey)===JSON.stringify(this.pending),'另一窗口改变了原请求，暂不清除。');this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);this.pending=null;
  this.state.forms[slot(pending.item_id)]={version:result.item.version,baseline:result.item.note,note:result.item.note,dirty:false,conflict:false};this.ingestDetail(current);need(this.persist());
  this.notice=pending.operation==='submit'?'办理结果已保存并核对；尚不表示独立验收通过或实际动作已完成。':'工作单记录已保存并核对。';this.notify();
  await this.load(token);this.check(token);try{await this.onSaved({kind:'work_order',item:copy(current),request_id:pending.body.request_id});this.check(token);}catch(error){this.notice+='父任务暂未刷新，可重新读取；原工作单保存结果保留。';this.notify();}return value;
 }
 retryLocal(){this.check();if(this.protected){this.error='原恢复记录尚不能安全读写；请先导出并保留，恢复本机存储后重新打开。';this.notify();return false;}const saved=this.persist();this.notify();return saved;}
 exportRecord(){return JSON.stringify({schema:'task-work-order-recovery-copy-v198',scope:this.scope,draft:this.state,pending:this.pending},null,2);}
}
export const createTaskWorkOrdersController=options=>new TaskWorkOrdersController(options);
