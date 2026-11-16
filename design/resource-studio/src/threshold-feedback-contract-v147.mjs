import {hashBytes} from './runtime-api';

export const THRESHOLD_ROOT_V147='/local-rules/evolution';
export const thresholdLabelsV147={should_alert:'本应提示',should_not_alert:'本不应提示',uncertain:'尚不能判断'};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,sha=/^[0-9a-f]{64}$/;
const own=(x,k)=>Object.prototype.hasOwnProperty.call(x,k),object=x=>!!x&&typeof x==='object'&&!Array.isArray(x),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),integer=x=>Number.isSafeInteger(x)&&x>=1;
const error=()=>Error('原判断、保存请求或本机稿无法核对；原内容保留，未替换或重新发送。');
function need(ok){if(!ok)throw error();}
function keys(x,list){need(object(x)&&Object.keys(x).sort().join('|')===[...list].sort().join('|'));}
function text(x,max=2000){need(typeof x==='string'&&x.length<=max&&!/[\u0000\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(x));}
export function thresholdScopeV147(account,instanceId,record){
 const scope={actor_id:account?.user_id,workspace_id:account?.workspace_id,instance_id:instanceId,observation_id:record?.id};
 need(Object.values(scope).every(x=>typeof x==='string'&&uuid.test(x)));return scope;
}
export function thresholdIdentityV147(me,account){need(me?.user_id===account?.user_id&&me?.workspace_id===account?.workspace_id&&me?.role===account?.role);return me;}
const basisKeys=['schema','basis_hash','observation_id','observation_version','baseline_capability_id','baseline_capability_hash','rule_id','field','field_label','unit','operator','threshold','actual_value','actual_hit','instance_id','release_id','entity_id','source_id'];
export function checkedThresholdBasisV147(value,scope){
 keys(value,basisKeys);need(value.schema==='threshold-evolution-feedback-basis-v147'&&sha.test(value.basis_hash)&&sha.test(value.baseline_capability_hash)&&integer(value.observation_version));
 need(value.observation_id===scope.observation_id&&value.instance_id===scope.instance_id&&['baseline_capability_id','release_id','entity_id','source_id'].every(key=>uuid.test(value[key]||'')));
 text(value.rule_id,80);text(value.field,100);text(value.field_label,300);need(!!value.rule_id&&!!value.field&&!!value.field_label&&['gt','lt'].includes(value.operator));if(value.unit!==null)text(value.unit,80);
 need(typeof value.actual_hit==='boolean'&&typeof value.actual_value==='number'&&Number.isFinite(value.actual_value)&&typeof value.threshold==='number'&&Number.isFinite(value.threshold)&&Math.abs(value.threshold)<=1e15);
 need(value.actual_hit===(value.operator==='gt'?value.actual_value>value.threshold:value.actual_value<value.threshold));return value;
}
const entryKeys=['id','version','root_id','revision','previous_id','status','actor_id','label','reason','cancel_reason','classification'];
function checkedEntry(value,hit){
 keys(value,entryKeys);need(['id','root_id','actor_id'].every(key=>uuid.test(value[key]||''))&&integer(value.version)&&integer(value.revision)&&(value.previous_id===null||uuid.test(value.previous_id))&&['draft','submitted','cancelled'].includes(value.status));
 need(value.label===null||own(thresholdLabelsV147,value.label));text(value.reason);if(value.cancel_reason!==null)text(value.cancel_reason);
 need(value.status==='cancelled'?!!value.cancel_reason?.trim():value.cancel_reason===null);if(value.status==='submitted')need(value.label!==null&&!!value.reason.trim());
 const classification=value.label==='should_alert'?(hit?'TP':'FN'):value.label==='should_not_alert'?(hit?'FP':'TN'):'unknown';need(value.classification===classification);return value;
}
export function checkedThresholdViewV147(value,scope){
 keys(value,['schema',...entryKeys,'basis','history','latest_submitted','can_edit','can_cancel','can_submit','can_revise']);need(value.schema==='threshold-evolution-feedback-view-v147');checkedThresholdBasisV147(value.basis,scope);
 const entry=Object.fromEntries(entryKeys.map(key=>[key,value[key]]));checkedEntry(entry,value.basis.actual_hit);need(Array.isArray(value.history)&&value.history.length>0);
 let previous=null;for(const [index,item]of value.history.entries()){checkedEntry(item,value.basis.actual_hit);need(item.root_id===value.root_id&&item.revision===index+1&&item.previous_id===(previous?.id||null));if(previous)need(previous.status!=='draft');previous=item;}
 need(same(value.history.find(item=>item.id===value.id),entry));const submitted=value.history.filter(item=>item.status==='submitted');need(same(value.latest_submitted,submitted.at(-1)||null));
 for(const key of ['can_edit','can_cancel','can_submit','can_revise'])need(typeof value[key]==='boolean');
 if(value.can_edit)need(value.id===previous.id&&value.status==='draft'&&value.actor_id===scope.actor_id);need(value.can_cancel===value.can_edit);if(value.can_submit)need(value.can_edit&&value.label!==null&&!!value.reason.trim());if(value.can_revise)need(previous.status!=='draft');return value;
}
export function checkedThresholdContextV147(value,scope,ruleID){
 keys(value,['schema','supported','issue','basis','feedback']);need(value.schema==='threshold-evolution-feedback-context-v147'&&typeof value.supported==='boolean');
 if(!value.supported){need(typeof value.issue==='string'&&value.basis===null&&value.feedback===null);return value;}
 need(value.issue===null);checkedThresholdBasisV147(value.basis,scope);need(value.basis.rule_id===ruleID);if(value.feedback!==null){checkedThresholdViewV147(value.feedback,scope);need(value.feedback.basis.basis_hash===value.basis.basis_hash);}return value;
}
export const emptyThresholdFormV147=()=>({label:null,reason:'',cancel_reason:'',target:null});
function checkedForm(value){
 keys(value,['label','reason','cancel_reason','target']);need(value.label===null||own(thresholdLabelsV147,value.label));text(value.reason);text(value.cancel_reason);
 const target=value.target;if(target!==null){if(target.kind==='create'){keys(target,['kind','observation_version']);need(integer(target.observation_version));}else{keys(target,['kind','id','version']);need(target.kind==='save'&&uuid.test(target.id||'')&&integer(target.version));}}return value;
}
function pendingBody(pending,scope){
 keys(pending,['schema','scope','owner','method','path','request_id','exact_body','wire_sha256']);need(pending.schema==='threshold-feedback-original-v147'&&same(pending.scope,scope)&&uuid.test(pending.owner||'')&&uuid.test(pending.request_id||'')&&sha.test(pending.wire_sha256));
 need(['POST','PUT'].includes(pending.method)&&typeof pending.exact_body==='string'&&new TextEncoder().encode(pending.exact_body).length<=65536);
 const body=JSON.parse(pending.exact_body);need(object(body)&&JSON.stringify(body)===pending.exact_body&&body.request_id===pending.request_id);
 const tail=pending.path.replace('/api'+THRESHOLD_ROOT_V147,'');need(pending.path==='/api'+THRESHOLD_ROOT_V147+tail);
 if(tail==='/feedback'){need(pending.method==='POST');keys(body,['request_id','observation_id','rule_id','expected_observation_version','label','reason']);need(body.observation_id===scope.observation_id&&integer(body.expected_observation_version));text(body.rule_id,80);}
 else{const match=tail.match(/^\/feedback\/([a-f0-9-]+)(?:\/(submit|cancel|revisions))?$/);need(!!match&&uuid.test(match[1]));const action=match[2];need(pending.method===(action?'POST':'PUT'));if(!action)keys(body,['request_id','expected_version','label','reason']);else if(action==='submit'){keys(body,['request_id','expected_version','confirm']);need(body.confirm===true);}else if(action==='cancel')keys(body,['request_id','expected_version','reason']);else keys(body,['request_id','expected_head_revision','label','reason']);need(integer(body[action==='revisions'?'expected_head_revision':'expected_version']));}
 if(own(body,'label'))need(body.label===null||own(thresholdLabelsV147,body.label));if(own(body,'reason'))text(body.reason);return body;
}
export async function checkedThresholdPendingV147(value,scope){pendingBody(value,scope);need(await hashBytes(new TextEncoder().encode(value.exact_body))===value.wire_sha256);return value;}
export function checkedThresholdRecoveryV147(value,scope,pending){
 keys(value,['schema','found','feedback']);need(value.schema==='threshold-evolution-request-recovery-v147'&&typeof value.found==='boolean');if(!value.found){need(value.feedback===null);return null;}
 const view=checkedThresholdViewV147(value.feedback,scope),body=pendingBody(pending,scope),tail=pending.path.replace('/api'+THRESHOLD_ROOT_V147,'');
 if(tail==='/feedback')need(view.basis.rule_id===body.rule_id&&view.revision===1);else{const [,identity,action]=tail.match(/^\/feedback\/([^/]+)(?:\/(.+))?$/);if(action==='revisions')need(view.root_id===identity&&view.revision===body.expected_head_revision+1);else{need(view.id===identity&&view.version>=body.expected_version+1);if(action==='submit')need(view.status==='submitted');if(action==='cancel')need(view.status==='cancelled');}}return view;
}
export function thresholdRecoveryPathV147(pending){return THRESHOLD_ROOT_V147+'/requests/'+pending.request_id+'?'+new URLSearchParams({expected_sha256:pending.wire_sha256,original_method:pending.method,original_path:pending.path});}

// Unsaved memory is only a loss-prevention warning, never a server receipt.
const unsaved=new Map(),unloadTargets=new Map(),activeOwners=new Set();
function remember(owner,scope,form,session){
 const target=globalThis.window;unsaved.set(owner,{scope,form:structuredClone(form),session,target});
 if(target&&!unloadTargets.has(target)){const handler=event=>{if([...unsaved.values()].some(value=>value.target===target)){event.preventDefault();event.returnValue='';}};target.addEventListener('beforeunload',handler);unloadTargets.set(target,handler);}
}
function forget(owner){unsaved.delete(owner);for(const[target,handler]of unloadTargets)if(![...unsaved.values()].some(value=>value.target===target)){target.removeEventListener('beforeunload',handler);unloadTargets.delete(target);}}
export class ThresholdFeedbackJournalV147{
 constructor(scope,{storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto}={}){this.scope=scope;this.storage=storage;this.session=session;this.locks=locks;this.crypto=crypto;this.prefix='ga-threshold-feedback-v147:'+Object.values(scope).join(':');this.active=this.prefix+':active';this.owner=null;this.ready=false;}
 exact(storage,key,value){storage.setItem(key,value);need(storage.getItem(key)===value);}
 readDraft(owner){need(uuid.test(owner||''));const raw=this.storage.getItem(this.prefix+':draft:'+owner);need(raw!==null);const row=JSON.parse(raw);keys(row,['schema','scope','owner','form']);need(row.schema==='threshold-feedback-draft-v147'&&same(row.scope,this.scope)&&row.owner===owner);checkedForm(row.form);return {raw,row};}
 open(){
  let form=emptyThresholdFormV147(),sourceOwner=this.session.getItem(this.prefix+':head'),rescueOwner=sourceOwner;if(sourceOwner)form=this.readDraft(sourceOwner).row.form;
  const memory=sourceOwner&&unsaved.get(sourceOwner);const candidates=[...unsaved.entries()].filter(([,value])=>value.session===this.session&&same(value.scope,this.scope));if(memory&&memory.session===this.session&&same(memory.scope,this.scope))form=memory.form;else if(candidates.length===1){rescueOwner=candidates[0][0];form=candidates[0][1].form;}
  this.owner=this.crypto.randomUUID();need(uuid.test(this.owner));activeOwners.add(this.owner);this.form=structuredClone(form);
  try{this.save(form);this.exact(this.session,this.prefix+':head',this.owner);if(rescueOwner&&!activeOwners.has(rescueOwner))forget(rescueOwner);this.ready=true;return this.form;}catch(cause){remember(this.owner,this.scope,form,this.session);throw cause;}
 }
 save(form){need(uuid.test(this.owner||''));this.form=structuredClone(form);try{checkedForm(form);this.exact(this.storage,this.prefix+':draft:'+this.owner,JSON.stringify({schema:'threshold-feedback-draft-v147',scope:this.scope,owner:this.owner,form}));this.exact(this.session,this.prefix+':head',this.owner);forget(this.owner);this.ready=true;return this.form;}catch(cause){remember(this.owner,this.scope,form,this.session);this.ready=false;throw cause;}}
 canSend(){return this.ready&&!!this.locks?.request&&!unsaved.has(this.owner);}
 isUnsaved(){return unsaved.has(this.owner);}
 dispose(){activeOwners.delete(this.owner);}
 archive(){need(uuid.test(this.owner||''));checkedForm(this.form);const owner=this.crypto.randomUUID();this.exact(this.storage,this.prefix+':draft:'+owner,JSON.stringify({schema:'threshold-feedback-draft-v147',scope:this.scope,owner,form:this.form}));}
 drafts(){const out=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix+':draft:')){const owner=key.slice((this.prefix+':draft:').length);if(owner!==this.owner)out.push({owner,form:this.readDraft(owner).row.form});}}return out;}
 restore(owner){need(uuid.test(this.owner||''));need(this.storage.getItem(this.active)===null);const form=this.readDraft(owner).row.form;this.archive();return this.save(form);}
 async pending(){const raw=this.storage.getItem(this.active);if(raw===null)return null;return checkedThresholdPendingV147(JSON.parse(raw),this.scope);}
 async install(method,path,body,check){need(this.canSend());return this.locks.request(this.prefix+':original',{mode:'exclusive'},async()=>{await check();need(this.storage.getItem(this.active)===null);const exact_body=JSON.stringify(body),pending={schema:'threshold-feedback-original-v147',scope:this.scope,owner:this.owner,method,path:'/api'+path,request_id:body.request_id,exact_body,wire_sha256:await hashBytes(new TextEncoder().encode(exact_body))};await checkedThresholdPendingV147(pending,this.scope);await check();this.exact(this.storage,this.active,JSON.stringify(pending));return pending;});}
 async withOriginal(pending,check,callback){need(!!this.locks?.request);return this.locks.request(this.prefix+':original',{mode:'exclusive'},async()=>{await check();await checkedThresholdPendingV147(pending,this.scope);const active=await this.pending();need(active===null||same(active,pending));await check();return callback(pending,active!==null);});}
 settleInside(pending,view){const active=this.storage.getItem(this.active),saved={schema:'threshold-feedback-saved-v147',pending,result_id:view.id,root_id:view.root_id},key=this.prefix+':saved:'+pending.request_id,prior=this.storage.getItem(key);if(prior!==null)need(prior===JSON.stringify(saved));else this.exact(this.storage,key,JSON.stringify(saved));if(active!==null){need(active===JSON.stringify(pending));this.storage.removeItem(this.active);need(this.storage.getItem(this.active)===null);}}
}
