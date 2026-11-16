const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,sha=/^[0-9a-f]{64}$/,nonce=/^[A-Za-z0-9_-]{12,100}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const need=(value,message='视频核查记录无法核对，原输入与原请求保留。')=>{if(!value)throw Error(message);};
const copy=value=>JSON.parse(JSON.stringify(value)),same=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
const text=(value,max=3000)=>typeof value==='string'&&Array.from(value).length<=max&&!/[\u0000\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const decisions=['consistent','needs_correction','uncertain'];
const memoryDrafts=new Map(),memoryOwners=new Set();let warningWindow=null;
function warn(event){if(memoryDrafts.size){event.preventDefault();event.returnValue='';}}
function protect(){const target=globalThis.window;if(warningWindow!==target||!memoryDrafts.size){warningWindow?.removeEventListener('beforeunload',warn);warningWindow=null;}if(memoryDrafts.size&&target&&!warningWindow){target.addEventListener('beforeunload',warn);warningWindow=target;}}

export const videoReviewDecisionsV165={consistent:'候选与画面一致',needs_correction:'候选需要纠正',uncertain:'仍无法判断'};
export const emptyVideoReviewFormV165=()=>({note:'',frame_ids:[],decision:null,confirm_candidate_only:false});
// Match Python str.strip, including NEL/control separators but not BOM.
export const videoReviewNoteReadyV165=value=>typeof value==='string'&&Array.from(value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'')).length>=5;
export const videoReviewRootV165=instance=>{need(uuid.test(instance));return '/workspace/instances/'+instance+'/native-video-reviews';};
export const videoReviewDetailPathV165=(instance,observation)=>{need(uuid.test(observation));return videoReviewRootV165(instance)+'/observations/'+observation;};
export function videoReviewIdentityV165(value,account){need(value?.user_id===account?.user_id&&value?.workspace_id===account?.workspace_id&&value?.username===account?.username&&value?.role===account?.role,'登录账号或工作区已变化，请重新核对；原稿和原请求保留。');return value;}
function baseScope(account,instance,observation){const value={actor_id:account?.user_id,workspace_id:account?.workspace_id,username:account?.username,instance_id:instance,observation_id:observation};need(['actor_id','workspace_id','instance_id','observation_id'].every(key=>uuid.test(value[key]||''))&&text(value.username,200)&&value.username.length>0);return value;}
export function videoReviewScopeV165(account,instance,observation,currentRelease,view){
 const value={...baseScope(account,instance,observation),role:account.role,current_release_id:currentRelease??null,release_id:view.release_id,receipt_hash:view.receipt_hash};
 need(['admin','operator','viewer'].includes(value.role)&&(value.current_release_id===null||uuid.test(value.current_release_id))&&uuid.test(value.release_id)&&sha.test(value.receipt_hash));return Object.freeze(value);
}
const scopeKeys=['actor_id','workspace_id','username','instance_id','observation_id','role','current_release_id','release_id','receipt_hash'];
function checkedScope(value){need(exact(value,scopeKeys));return videoReviewScopeV165({user_id:value.actor_id,workspace_id:value.workspace_id,username:value.username,role:value.role},value.instance_id,value.observation_id,value.current_release_id,value);}
const baseEqual=(left,right)=>['actor_id','workspace_id','username','instance_id','observation_id'].every(key=>left[key]===right[key]);
const prefix=scope=>'ga-native-video-review-v165:'+['actor_id','workspace_id','username','instance_id','observation_id'].map(key=>encodeURIComponent(scope[key])).join(':')+':';
const draftPrefix=scope=>prefix(scope)+'scope:'+encodeURIComponent(JSON.stringify([scope.role,scope.current_release_id,scope.release_id,scope.receipt_hash]))+':';
function localForm(value){need(exact(value,['note','frame_ids','decision','confirm_candidate_only'])&&text(value.note,6000)&&Array.isArray(value.frame_ids)&&value.frame_ids.length<=120&&value.frame_ids.every(id=>uuid.test(id))&&new Set(value.frame_ids).size===value.frame_ids.length&&(value.decision===null||decisions.includes(value.decision))&&typeof value.confirm_candidate_only==='boolean','说明或画面选择无效，原输入保留。');return copy(value);}
export function checkedVideoReviewFormV165(value){const result=localForm(value);need(text(result.note),'说明最多3000字；当前输入仍保留在本机，暂不能发送。');return result;}
function opinion(value){need(exact(value,['note','frame_ids','decision']));return checkedVideoReviewFormV165({...value,confirm_candidate_only:false});}
function history(value,owner=null,human=false){
 need(exact(value,['version','actor_id','note','frame_ids','decision','saved_at',...(human?['operation']:['actor_label'])])&&integer(value.version)&&value.version>=1&&uuid.test(value.actor_id)&&(!owner||value.actor_id===owner));
 if(!human)need(text(value.actor_label,200)&&value.actor_label.trim().length>0);
 opinion({note:value.note,frame_ids:value.frame_ids,decision:value.decision});need(typeof value.saved_at==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$/.test(value.saved_at)&&Number.isFinite(Date.parse(value.saved_at)));
 if(human)need(['draft','submit'].includes(value.operation));if(!human||value.operation==='submit')need(videoReviewNoteReadyV165(value.note)&&value.frame_ids.length>0&&decisions.includes(value.decision));return value;
}
export function checkedVideoReviewDetailV165(value,account,instance,observation){
 const keys=['schema','instance_id','observation_id','run_id','release_id','receipt_hash','version','status','owner_id','draft','submitted_history','latest_submitted','human_history','source_available','source_issue','run','frames','can_save_draft','can_submit'];
 need(exact(value,keys)&&value.schema==='native-video-review-view-v165'&&value.instance_id===instance&&value.observation_id===observation&&['run_id','release_id'].every(key=>uuid.test(value[key]))&&sha.test(value.receipt_hash)&&integer(value.version)&&['empty','draft','reviewed'].includes(value.status));
 need(value.version===0?value.owner_id===null&&value.status==='empty':value.owner_id===account.user_id&&value.status!=='empty');opinion(value.draft);
 need(Array.isArray(value.submitted_history)&&Array.isArray(value.human_history)&&value.human_history.length===value.version);
 const seen=new Set();for(const item of value.submitted_history){history(item);const key=item.actor_id+':'+item.version;need(!seen.has(key));seen.add(key);}value.human_history.forEach((item,index)=>{history(item,account.user_id,true);need(item.version===index+1);});
 need(value.latest_submitted===null?value.submitted_history.length===0:same(value.latest_submitted,value.submitted_history.at(-1)));
 need(typeof value.source_available==='boolean'&&(value.source_issue===null||text(value.source_issue,2000))&&typeof value.can_save_draft==='boolean'&&typeof value.can_submit==='boolean');
 need(Array.isArray(value.frames)&&value.frames.length<=120);
 if(!value.source_available){need(value.run===null&&value.frames.length===0&&!value.can_save_draft&&!value.can_submit&&!!value.source_issue);return value;}
 const run=value.run;need(exact(run,['id','name','status','input_sha256','gateway_capture','original_path','candidate_only','source_time_verified','physical_state_verified'])&&run.id===value.run_id&&text(run.name,1000)&&run.status==='succeeded'&&sha.test(run.input_sha256)&&run.gateway_capture===true&&run.original_path==='/api/videos/'+run.id+'/original'&&run.candidate_only===true&&run.source_time_verified===false&&run.physical_state_verified===false);
 const frameIDs=new Set();value.frames.forEach((frame,index)=>{need(exact(frame,['id','sample_index','seconds','evidence_id','sha256','width','height','tracks','utc','content_path'])&&uuid.test(frame.id)&&!frameIDs.has(frame.id)&&frame.sample_index===index&&Number.isFinite(frame.seconds)&&frame.seconds>=0&&uuid.test(frame.evidence_id)&&sha.test(frame.sha256)&&integer(frame.width)&&frame.width>0&&integer(frame.height)&&frame.height>0&&Array.isArray(frame.tracks)&&(frame.utc===null||text(frame.utc,100))&&frame.content_path==='/api/evidence/'+frame.evidence_id+'/content');frameIDs.add(frame.id);for(const track of frame.tracks)need(object(track)&&text(track.track_ref,100)&&text(track.label,200)&&Number.isFinite(track.score)&&track.score>=0&&track.score<=1&&Array.isArray(track.bbox)&&track.bbox.length===4&&track.bbox.every(Number.isFinite));});
 need(value.frames.length>0&&value.draft.frame_ids.every(id=>frameIDs.has(id)));return value;
}
export function videoReviewCommandV165(scope,form,expectedVersion,operation,requestID){
 form=checkedVideoReviewFormV165(form);need(integer(expectedVersion)&&nonce.test(requestID)&&['draft','submit'].includes(operation));
 const body={request_id:requestID,expected_version:expectedVersion,note:form.note,frame_ids:[...form.frame_ids],decision:form.decision};
 if(operation==='submit'){need(form.confirm_candidate_only&&videoReviewNoteReadyV165(form.note)&&form.frame_ids.length>0&&decisions.includes(form.decision),'请填写至少5字的核查说明、选择依据画面和判断，并明确确认只核查候选。');body.confirm_candidate_only=true;}
 checkedScope(scope);return body;
}
export async function videoReviewSHA256V165(raw){need(typeof raw==='string'&&new TextEncoder().encode(raw).length<=65536);const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));return [...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');}
export async function checkedVideoReviewOriginalV165(value,scope){
 need(exact(value,['schema','scope','request_id','operation','method','path','exact_body','wire_sha256'])&&value.schema==='native-video-review-original-v165');checkedScope(value.scope);need(baseEqual(value.scope,scope)&&nonce.test(value.request_id)&&['draft','submit'].includes(value.operation)&&value.method==='POST'&&value.path==='/api'+videoReviewDetailPathV165(value.scope.instance_id,value.scope.observation_id)+'/'+value.operation&&sha.test(value.wire_sha256));
 need(typeof value.exact_body==='string'&&await videoReviewSHA256V165(value.exact_body)===value.wire_sha256);const body=JSON.parse(value.exact_body),keys=['request_id','expected_version','note','frame_ids','decision',...(value.operation==='submit'?['confirm_candidate_only']:[])];need(exact(body,keys)&&body.request_id===value.request_id&&JSON.stringify(body)===value.exact_body);videoReviewCommandV165(value.scope,{note:body.note,frame_ids:body.frame_ids,decision:body.decision,confirm_candidate_only:body.confirm_candidate_only===true},body.expected_version,value.operation,body.request_id);return value;
}
export function checkedVideoReviewRecoveryV165(value,original){
 need(exact(value,['schema','request_id','wire_sha256','outcome','result'])&&value.schema==='native-video-review-request-v165'&&value.request_id===original.request_id&&value.wire_sha256===original.wire_sha256&&['saved','unknown'].includes(value.outcome));
 if(value.outcome==='unknown'){need(value.result===null);return value;}const result=value.result,body=JSON.parse(original.exact_body);need(exact(result,['observation_id','version','operation','status'])&&result.observation_id===original.scope.observation_id&&result.version===body.expected_version+1&&result.operation===original.operation&&result.status===(original.operation==='submit'?'reviewed':'draft'));return value;
}
export const videoReviewRecoveryPathV165=original=>videoReviewRootV165(original.scope.instance_id)+'/requests/'+original.request_id+'?expected_sha256='+original.wire_sha256;

export class NativeVideoReviewJournalV165{
 constructor(scope,{storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks}={}){checkedScope(scope);Object.assign(this,{scope,storage,session,locks});this.prefix=prefix(scope);this.draftPrefix=draftPrefix(scope);this.owner=crypto.randomUUID();memoryOwners.add(this.owner);this.generation=0;this.form=emptyVideoReviewFormV165();this.unsaved=false;this.recoveredMemory=[];}
 dispose(){this.disposed=true;memoryOwners.delete(this.owner);protect();}
 write(store,key,raw){need(typeof raw==='string'&&raw.length<=131072);store.setItem(key,raw);need(store.getItem(key)===raw,'本机保存后未完整读回，原输入保留，暂不发送。');}
 lock(callback){need(typeof this.locks?.request==='function','浏览器缺少多窗口协调，原记录可查，暂不能提交。');return this.locks.request(this.prefix+'lock',{mode:'exclusive'},callback);}
 open(){
  need(!this.disposed,'核查窗口已关闭，原输入保留。');this.recoveredMemory=[];
  const raw=this.session.getItem(this.draftPrefix+'head');this.hasLocal=raw!==null;if(raw!==null){try{const head=JSON.parse(raw);need(exact(head,['key','owner','generation'])&&uuid.test(head.owner)&&integer(head.generation)&&head.generation>0&&head.key===this.draftPrefix+'draft:'+head.owner+':'+head.generation%2);const row=JSON.parse(this.storage.getItem(head.key));need(exact(row,['scope','owner','generation','form'])&&same(row.scope,this.scope)&&row.owner===head.owner&&row.generation===head.generation);this.form=localForm(row.form);}catch(error){this.blocked=true;throw error;}}
  for(const[key,row]of memoryDrafts)if(!memoryOwners.has(row.owner)&&same(row.scope,this.scope)){this.form=copy(row.form);this.hasLocal=true;this.recoveredMemory=[...(row.rescued||[]),{key,owner:row.owner,row}];this.unsaved=true;}
  protect();return copy(this.form);
 }
 save(form){
  need(!this.disposed,'核查窗口已关闭，原输入保留。');form=localForm(form);this.form=copy(form);const memoryKey=this.draftPrefix+this.owner;
  try{need(!this.blocked,'本机原稿指针无法核对，未覆盖原稿。');const generation=this.generation+1,key=this.draftPrefix+'draft:'+this.owner+':'+generation%2;this.write(this.storage,key,JSON.stringify({scope:this.scope,owner:this.owner,generation,form}));this.write(this.session,this.draftPrefix+'head',JSON.stringify({key,owner:this.owner,generation}));this.generation=generation;this.unsaved=false;memoryDrafts.delete(memoryKey);for(const rescued of this.recoveredMemory)if(!memoryOwners.has(rescued.owner)&&memoryDrafts.get(rescued.key)===rescued.row)memoryDrafts.delete(rescued.key);this.recoveredMemory=[];protect();return copy(form);}catch(error){this.unsaved=true;memoryDrafts.set(memoryKey,{owner:this.owner,scope:this.scope,form:copy(form),rescued:this.recoveredMemory.slice()});protect();throw error;}
 }
 async pending(){const headRaw=this.storage.getItem(this.prefix+'active');if(headRaw===null)return null;const head=JSON.parse(headRaw);need(exact(head,['key','request_id'])&&nonce.test(head.request_id)&&head.key===this.prefix+'request:'+head.request_id);const raw=this.storage.getItem(head.key);need(typeof raw==='string');const value=await checkedVideoReviewOriginalV165(JSON.parse(raw),this.scope);need(value.request_id===head.request_id);return {key:head.key,raw,headRaw,value};}
 async install(operation,body,authorize){return this.lock(async()=>{await authorize();need(!this.unsaved&&!this.blocked,'本机稿尚未完整保存，暂不发送。');need(!await this.pending(),'原保存请求仍待核对；当前输入保留，暂不建立新请求。');const exact_body=JSON.stringify(body),value={schema:'native-video-review-original-v165',scope:this.scope,request_id:body.request_id,operation,method:'POST',path:'/api'+videoReviewDetailPathV165(this.scope.instance_id,this.scope.observation_id)+'/'+operation,exact_body,wire_sha256:await videoReviewSHA256V165(exact_body)};await checkedVideoReviewOriginalV165(value,this.scope);await authorize();const key=this.prefix+'request:'+body.request_id;need(this.storage.getItem(key)===null);this.write(this.storage,key,JSON.stringify(value));this.write(this.storage,this.prefix+'active',JSON.stringify({key,request_id:body.request_id}));return this.pending();});}
 async withOriginal(original,authorize,callback){return this.lock(async()=>{await authorize();const actual=await this.pending();need(actual&&actual.raw===original.raw&&actual.headRaw===original.headRaw&&same(actual.value.scope,this.scope),'原请求或当前版本范围已变化，未重发。');await authorize();return callback(actual.value);});}
 async finish(original,state,authorize){checkedVideoReviewRecoveryV165(state,original.value);need(state.outcome==='saved');return this.lock(async()=>{await authorize();const actual=await this.pending();need(actual&&actual.raw===original.raw&&actual.headRaw===original.headRaw);await authorize();this.write(this.storage,this.prefix+'saved:'+original.value.request_id,original.raw);need(this.storage.getItem(this.prefix+'active')===original.headRaw);this.storage.removeItem(this.prefix+'active');need(this.storage.getItem(this.prefix+'active')===null);});}
 async resolveConflict(original,authorize,readDetail,readOriginal){return this.lock(async()=>{await authorize();const actual=await this.pending();need(actual&&actual.raw===original.raw&&same(actual.value.scope,this.scope));const detail=await readDetail();need(detail.release_id===this.scope.release_id&&detail.receipt_hash===this.scope.receipt_hash&&detail.version>JSON.parse(original.value.exact_body).expected_version,'服务器版本尚未推进，原请求不能结清。');const state=checkedVideoReviewRecoveryV165(await readOriginal(original.value),original.value);const finalPending=await this.pending();await authorize();need(finalPending&&finalPending.raw===original.raw&&finalPending.headRaw===original.headRaw&&this.storage.getItem(this.prefix+'active')===original.headRaw&&this.storage.getItem(original.key)===original.raw);this.write(this.storage,this.prefix+(state.outcome==='saved'?'saved:':'cas-conflict:')+original.value.request_id,original.raw);need(this.storage.getItem(this.prefix+'active')===original.headRaw);this.storage.removeItem(this.prefix+'active');need(this.storage.getItem(this.prefix+'active')===null);return {state,detail};});}
}
