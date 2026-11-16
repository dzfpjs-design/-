// SIM-only feedback and frozen comparisons. No production activation surface.
import {freshNumber,typedRequestJSON,parseTypedJSON,plainTyped,numericValue} from './typed-numbers-v132.mjs';
import {simulationScopeV176,simulationIdentityKeyV176,checkSimulationDetailV176} from './simulation-integration-v176.mjs';

export const EVOLUTION_BASE_V176='/workspace/simulation-v176';
export const evolutionLabelsV176=Object.freeze({should_alert:'应当提醒',should_not_alert:'不应提醒',uncertain:'暂时无法判断'});
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const SHA=/^[a-f0-9]{64}$/;
const memory=new Map();
const humanMemory=new Map();
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const need=(ok,message='原反馈或比较依据无法核对，输入保留，尚未执行。')=>{if(!ok)throw Error(message);};
const id=v=>typeof v==='string'&&UUID.test(v);
const version=v=>Number.isSafeInteger(v)&&v>=1;
const clone=v=>JSON.parse(JSON.stringify(v));
const exactKeys=(v,keys)=>object(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=1e15;
const sameIdentity=(a,b)=>a.actor_id===b.actor_id&&a.workspace_id===b.workspace_id&&a.instance_id===b.instance_id;
const identityScope=scope=>{const s=simulationScopeV176(scope);return {actor_id:s.actor_id,workspace_id:s.workspace_id,instance_id:s.instance_id};};
export const evolutionKeyV176=scope=>'simulation-evolution-v176:'+Object.values(identityScope(scope)).join(':');
export const evolutionIdentityV176=simulationIdentityKeyV176;

export function evolutionMarkV176(value){need(object(value)&&value.simulation===true&&value.origin_kind==='simulated_equipment'&&value.field_verified===false&&value.test_only===true&&value.production_activation===false,'记录未保留完整模拟边界，未显示为真实现场结果。');return value;}
export async function evolutionSHAV176(raw,crypto=globalThis.crypto){need(typeof raw==='string'&&crypto?.subtle,'浏览器无法保留原请求摘要，尚未提交。');return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))),v=>v.toString(16).padStart(2,'0')).join('');}
export function candidateThresholdV176(raw){const value=freshNumber(raw,'number','候选阈值');need(Math.abs(numericValue(value))<=1e15,'候选阈值超出支持范围，请核对单位。');return value;}
export function checkEvolutionRecordV176(value,kind,scope,environmentID){
 evolutionMarkV176(value);need(id(value.id)&&value.workspace_id===scope.workspace_id&&id(value.actor_id)&&version(value.version)&&value.environment_id===environmentID&&SHA.test(value.record_hash||'')&&object(value.data));
 evolutionMarkV176(value.data);
 if(kind==='feedback'){
  need(value.data.schema==='simulation-feedback-record-v176'&&id(value.observation_id)&&Object.hasOwn(evolutionLabelsV176,value.data.label)&&typeof value.data.reason==='string'&&value.data.reason.trim()&&object(value.data.basis));
  const basis=value.data.basis;evolutionMarkV176(basis);
  need(basis.environment_id===environmentID&&basis.observation_id===value.observation_id&&basis.origin?.instance_id===scope.instance_id&&finite(basis.value)&&typeof basis.unit==='string'&&typeof basis.observed_at==='string'&&typeof basis.field==='string');
 }else{
  need(value.data.schema==='simulation-study-record-v176'&&['frozen','completed','blocked'].includes(value.status)&&SHA.test(value.frozen_hash||'')&&object(value.data.freeze));
  const frozen=value.data.freeze;evolutionMarkV176(frozen);need(frozen.environment_id===environmentID&&typeof frozen.title==='string'&&typeof frozen.reason==='string'&&Array.isArray(frozen.samples)&&frozen.samples.every(row=>object(row)&&row.basis?.origin?.instance_id===scope.instance_id));
  if(value.data.result!==null){evolutionMarkV176(value.data.result);need(value.data.result.study_hash===value.frozen_hash&&value.data.result.status===value.status&&Array.isArray(value.data.result.per_sample)&&value.data.result.production_rule_changes===0&&value.data.result.shadow_started===false);}
 }
 return value;
}
export function evaluationReadinessV176(study){
 const samples=study?.data?.freeze?.samples;
 if(!Array.isArray(samples))return {ready:false,reason:'冻结样本尚未完整读取。'};
 const rows=samples.map(row=>({at:Date.parse(row.observed_at),clear:row.label!=='uncertain'})).sort((a,b)=>a.at-b.at);
 if(rows.some(row=>!Number.isFinite(row.at)))return {ready:false,reason:'有样本时间无法核对，请保留原稿并核对来源。'};
 for(let split=1;split<rows.length;split++)if(rows[split-1].at<rows[split].at&&rows.slice(0,split).filter(row=>row.clear).length>=2&&rows.slice(split).filter(row=>row.clear).length>=2)return {ready:true,reason:'可提交服务器核验分时比较条件；这不代表已获得改进结论。'};
 return {ready:false,reason:'需至少4条独立、明确判断的取样，按原时间分为前后各至少2条；同一时刻不能拆到两组。补充反馈后请新建比较。'};
}

function commandBody(record,scope){
 need(exactKeys(record,['schema','scope','operation','environment_id','target_id','request_id','path','raw','sha256'])&&record.schema==='simulation-evolution-original-v176'&&sameIdentity(record.scope,identityScope(scope))&&id(record.environment_id)&&id(record.request_id)&&SHA.test(record.sha256||'')&&typeof record.raw==='string');
 const typed=parseTypedJSON(record.raw),body=plainTyped(typed);need(typedRequestJSON(typed)===record.raw&&body.request_id===record.request_id,'原请求正文已改变，请保留记录。');
 if(record.operation==='feedback.save')need(record.path===EVOLUTION_BASE_V176+'/feedback'&&exactKeys(body,['request_id','expected_version','observation_id','label','reason'])&&record.target_id===body.observation_id&&id(body.observation_id)&&Number.isSafeInteger(body.expected_version)&&body.expected_version>=0&&Object.hasOwn(evolutionLabelsV176,body.label)&&typeof body.reason==='string'&&body.reason.trim());
 else if(record.operation==='study.create')need(record.path===EVOLUTION_BASE_V176+'/studies'&&record.target_id===null&&exactKeys(body,['request_id','environment_id','title','feedback_refs','candidate_threshold','reason'])&&body.environment_id===record.environment_id&&typeof body.title==='string'&&body.title.trim()&&typeof body.reason==='string'&&body.reason.trim()&&finite(body.candidate_threshold)&&Array.isArray(body.feedback_refs)&&body.feedback_refs.length>=1&&body.feedback_refs.length<=100&&body.feedback_refs.every(ref=>exactKeys(ref,['feedback_id','expected_version'])&&id(ref.feedback_id)&&version(ref.expected_version))&&new Set(body.feedback_refs.map(ref=>ref.feedback_id)).size===body.feedback_refs.length);
 else need(record.operation==='study.evaluate'&&id(record.target_id)&&record.path===EVOLUTION_BASE_V176+'/studies/'+record.target_id+'/evaluate'&&exactKeys(body,['request_id','expected_version','expected_study_hash'])&&version(body.expected_version)&&SHA.test(body.expected_study_hash||''));
 return body;
}

export class SimulationEvolutionClientV176{
 constructor(api,scope,{storage,locks,crypto,current=()=>true}={}){
  need(typeof api==='function');this.api=api;this.scope=simulationScopeV176(scope);this.key=evolutionKeyV176(scope);this.current=current;this.crypto=crypto||globalThis.crypto;this.locks=locks||globalThis.navigator?.locks;this.disposed=false;this.pendingRecord=null;this.dirty=false;this.owner=this.crypto?.randomUUID?.();
  try{this.storage=storage||globalThis.localStorage;}catch{this.storage=null;}
 }
 fence(){need(!this.disposed&&this.current(),'账号、工作区或实例已变化，旧操作已停止，原稿保留。');}
 async identity(write=false){this.fence();const me=await this.api('/me');this.fence();need(me?.user_id===this.scope.actor_id&&me?.workspace_id===this.scope.workspace_id&&me?.role===this.scope.role,'当前会话与页面范围不同，请重新打开工作区。');if(write)need(['admin','operator'].includes(me.role),'当前账号只读，不能提交反馈或比较。');return me;}
 write(key,value){this.fence();need(this.storage,'浏览器存储不可用；输入保留，尚未提交。');this.storage.setItem(key,value);need(this.storage.getItem(key)===value,'本机保存未读回，尚未提交。');}
 async locked(run){need(typeof this.locks?.request==='function','浏览器不支持安全原请求占用，现在只能查看服务器记录。');return this.locks.request(this.key,{mode:'exclusive'},async()=>{this.fence();return run();});}
 readPending(){
  this.fence();need(this.storage,'本机原请求尚未读取。');const candidates=new Map();
  const consider=raw=>{if(!raw)return;const record=JSON.parse(raw);commandBody(record,this.scope);const done=this.storage.getItem(this.key+':receipt:'+record.request_id);if(done!==null){need(done===record.sha256);return;}const prior=candidates.get(record.request_id);need(!prior||JSON.stringify(prior)===raw,'同一原号存在不同本机正文。');candidates.set(record.request_id,record);};
  consider(this.storage.getItem(this.key+':pending'));need(this.storage.length<=20000,'本机记录过多，请先核对存储。');for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.key+':original:'))consider(this.storage.getItem(key));}const retained=memory.get(this.key);if(retained)consider(JSON.stringify(retained));need(candidates.size<=1,'存在多份未核对原请求，暂不新增操作。');this.pendingRecord=candidates.size?clone([...candidates.values()][0]):null;
  this.dirty=!!this.pendingRecord&&(this.storage.getItem(this.key+':pending')!==JSON.stringify(this.pendingRecord)||this.storage.getItem(this.key+':original:'+this.pendingRecord.request_id)!==JSON.stringify(this.pendingRecord));return this.pendingRecord?clone(this.pendingRecord):null;
 }
 async pending(){return this.locked(async()=>{const record=this.readPending();if(record)need(await evolutionSHAV176(record.raw,this.crypto)===record.sha256,'原请求摘要不一致，未发送。');this.fence();return record;});}
 async persistPending(){return this.locked(async()=>{this.readPending();const record=this.pendingRecord;need(record);const raw=JSON.stringify(record),key=this.key+':original:'+record.request_id,old=this.storage.getItem(key);need(old===null||old===raw,'原编号已有不同正文。');this.write(key,raw);this.write(this.key+':pending',raw);this.dirty=false;return clone(record);});}
 readDrafts(){this.fence();const values=new Map();let storageError=null;try{need(this.storage);need(this.storage.length<=20000,'本机保留稿过多，请先核对存储。');for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.key+':draft:')){const value=JSON.parse(this.storage.getItem(key));need(value.schema==='simulation-evolution-draft-v176'&&sameIdentity(value.scope,identityScope(this.scope))&&object(value.value));values.set(value.owner,{...value,persisted:true});}}}catch(error){storageError=error;}
  for(const [key,entry]of humanMemory)if(key.startsWith(this.key+':draft:')){need(sameIdentity(entry.row.scope,identityScope(this.scope)));values.set(entry.row.owner,{...clone(entry.row),persisted:entry.persisted});}
  if(storageError&&!values.size)throw storageError;return [...values.values()].sort((a,b)=>String(b.at).localeCompare(String(a.at)));
 }
 saveDraft(value){this.fence();need(id(this.owner));const row={schema:'simulation-evolution-draft-v176',scope:identityScope(this.scope),at:new Date().toISOString(),owner:this.owner,value:clone(value)},key=this.key+':draft:'+this.owner;humanMemory.set(key,{row:clone(row),persisted:false});this.write(key,JSON.stringify(row));humanMemory.set(key,{row:clone(row),persisted:true});return row;}
 studyRefs(){this.fence();const raw=this.storage?.getItem(this.key+':study-refs');if(raw===null||raw===undefined)return [];const rows=JSON.parse(raw);need(Array.isArray(rows)&&rows.every(row=>exactKeys(row,['id','environment_id'])&&id(row.id)&&id(row.environment_id)));return rows;}
 rememberStudy(row){const refs=this.studyRefs();if(!refs.some(ref=>ref.id===row.id))this.write(this.key+':study-refs',JSON.stringify([...refs,{id:row.id,environment_id:row.environment_id}]));}
 async feedback(environmentID,observationID=null){need(id(environmentID)&&(!observationID||id(observationID)));await this.identity();const value=await this.api(EVOLUTION_BASE_V176+'/feedback?environment_id='+environmentID+(observationID?'&observation_id='+observationID:''));await this.identity();evolutionMarkV176(value);need(Array.isArray(value.rows)&&Number.isSafeInteger(value.limit)&&value.limit>=1&&value.limit<=100);for(const row of value.rows)checkEvolutionRecordV176(row,'feedback',this.scope,environmentID);if(observationID){need((value.observation_id===observationID&&value.complete===true)||value.rows.length<value.limit,'反馈清单已达返回上限，尚不能确认这次原取样的本人版本；当前服务需提供单次取样的完整反馈查询，未按版本0新建。');return value.rows.filter(row=>row.observation_id===observationID);}return value.rows;}
 async sample(row,environmentID){await this.identity();const value=checkSimulationDetailV176(await this.api(EVOLUTION_BASE_V176+'/samples/'+row.id),row,this.scope);await this.identity();need(value.origin?.environment_id===environmentID&&value.processing_status==='processed'&&value.extracted?.method==='registered_numeric_rules','原样本尚未成功完成数值提取，不能作为有效反馈样本。');return value;}
 async study(identity,environmentID){await this.identity();const value=await this.api(EVOLUTION_BASE_V176+'/studies/'+identity);await this.identity();return checkEvolutionRecordV176(value,'study',this.scope,environmentID);}
 async studies(environmentID){const refs=this.studyRefs().filter(row=>row.environment_id===environmentID);const results=await Promise.allSettled(refs.map(ref=>this.study(ref.id,environmentID)));this.fence();return {rows:results.filter(row=>row.status==='fulfilled').map(row=>row.value),issues:results.filter(row=>row.status==='rejected').map(row=>row.reason.message)};}
 async stage(operation,environmentID,targetID,value){
  await this.identity(true);return this.locked(async()=>{need(this.readPending()===null,'原请求尚未核对，不能更换编号。');const request_id=this.crypto?.randomUUID?.();need(id(request_id)&&id(environmentID));const body={request_id,...value};const path=operation==='feedback.save'?EVOLUTION_BASE_V176+'/feedback':operation==='study.create'?EVOLUTION_BASE_V176+'/studies':EVOLUTION_BASE_V176+'/studies/'+targetID+'/evaluate';const raw=typedRequestJSON(body);const record={schema:'simulation-evolution-original-v176',scope:identityScope(this.scope),operation,environment_id:environmentID,target_id:targetID,request_id,path,raw,sha256:await evolutionSHAV176(raw,this.crypto)};this.fence();commandBody(record,this.scope);memory.set(this.key,clone(record));this.pendingRecord=record;this.dirty=true;const text=JSON.stringify(record);this.write(this.key+':original:'+request_id,text);this.write(this.key+':pending',text);this.dirty=false;return clone(record);});
 }
 async probe(record){commandBody(record,this.scope);need(await evolutionSHAV176(record.raw,this.crypto)===record.sha256);await this.identity();const value=await this.api(EVOLUTION_BASE_V176+'/evolution/requests/'+record.request_id+'?expected_sha256='+record.sha256);await this.identity();evolutionMarkV176(value);need(value.request_id===record.request_id&&typeof value.found==='boolean');if(!value.found)return value;need(value.operation===record.operation&&value.body_sha256===record.sha256&&value.target_id===value.result?.id,'原请求回执的正文或操作不一致。');const body=commandBody(record,this.scope),kind=record.operation==='feedback.save'?'feedback':'study';checkEvolutionRecordV176(value.result,kind,this.scope,record.environment_id);
  if(kind==='feedback')need(value.result.actor_id===this.scope.actor_id&&value.result.observation_id===body.observation_id&&value.result.version===body.expected_version+1&&value.result.data.label===body.label&&value.result.data.reason===body.reason);
  else if(record.operation==='study.create')need(value.result.version===1&&value.result.actor_id===this.scope.actor_id&&value.result.data.freeze.title===body.title&&value.result.data.freeze.reason===body.reason);
  else need(value.result.id===record.target_id&&value.result.version===body.expected_version+1&&value.result.frozen_hash===body.expected_study_hash);
  return value;
 }
 async settle(record){const result=await this.probe(record);if(!result.found)return result;await this.locked(async()=>{this.readPending();if(this.storage.getItem(this.key+':receipt:'+record.request_id)===record.sha256)return;need(this.pendingRecord?.request_id===record.request_id&&this.pendingRecord.sha256===record.sha256);if(record.operation!=='feedback.save')this.rememberStudy(result.result);this.write(this.key+':receipt:'+record.request_id,record.sha256);need(this.storage.getItem(this.key+':pending')===JSON.stringify(record));this.storage.removeItem(this.key+':pending');need(this.storage.getItem(this.key+':pending')===null);memory.delete(this.key);this.pendingRecord=null;this.dirty=false;});return result;}
 async sendOriginal(record){const prior=await this.settle(record);if(prior.found)return prior;await this.identity(true);let deliveryError;
  await this.locked(async()=>{this.readPending();if(this.storage.getItem(this.key+':receipt:'+record.request_id)===record.sha256)return;need(!this.dirty&&this.pendingRecord?.raw===record.raw&&this.pendingRecord.sha256===record.sha256,'本机原请求尚未完整保存。');await this.identity(true);if((await this.probe(record)).found)return;try{await this.api(record.path,{method:'POST',body:record.raw});}catch(error){deliveryError=error;if([400,403,404,409,422].includes(error.status))this.write(this.key+':rejected:'+record.request_id,JSON.stringify({sha256:record.sha256,status:error.status}));}this.fence();});
  const saved=await this.settle(record);if(!saved.found&&deliveryError)throw Error(deliveryError.message+' 原请求保留，请核对后处理。');return saved;
 }
 async releaseRejected(record){const value=await this.probe(record);need(!value.found,'服务器已有原结果，请核对原回执。');return this.locked(async()=>{this.readPending();need(this.pendingRecord?.raw===record.raw);const rejection=JSON.parse(this.storage.getItem(this.key+':rejected:'+record.request_id)||'null');need(rejection?.sha256===record.sha256&&[400,403,404,409,422].includes(rejection.status),'原结果仍未知，不能停止使用原编号。');this.write(this.key+':retired:'+record.request_id,JSON.stringify({sha256:record.sha256,reason:'definite_rejection_and_negative_original_lookup'}));this.write(this.key+':receipt:'+record.request_id,record.sha256);this.storage.removeItem(this.key+':pending');need(this.storage.getItem(this.key+':pending')===null);memory.delete(this.key);this.pendingRecord=null;this.dirty=false;});}
 async saveFeedback(environmentID,detail,draft){need(detail?.origin?.environment_id===environmentID&&detail.origin.instance_id===this.scope.instance_id&&detail.processing_status==='processed'&&id(detail.observation_id),'所选样本与当前环境或处理状态不一致，未提交反馈。');const rows=await this.feedback(environmentID,detail.observation_id),own=rows.find(row=>row.actor_id===this.scope.actor_id);need((own?.version||0)===draft.expected_version,'服务器反馈版本已变化，本机原因保留，请先对照服务器原稿。');const record=await this.stage('feedback.save',environmentID,detail.observation_id,{expected_version:draft.expected_version,observation_id:detail.observation_id,label:draft.label,reason:draft.reason});return this.sendOriginal(record);}
 async createStudy(environmentID,draft,feedback){const threshold=candidateThresholdV176(draft.threshold);const refs=draft.feedbackIDs.map(identity=>{const row=feedback.find(item=>item.id===identity);need(row,'所选反馈已不在当前清单，请刷新核对。');return {feedback_id:row.id,expected_version:row.version};});const record=await this.stage('study.create',environmentID,null,{environment_id:environmentID,title:draft.title,feedback_refs:refs,candidate_threshold:threshold,reason:draft.reason});return this.sendOriginal(record);}
 async evaluate(study){const current=await this.study(study.id,study.environment_id);need(current.status==='frozen'&&current.version===study.version&&current.frozen_hash===study.frozen_hash,'比较稿版本已变化，请重新读取并确认。');need(evaluationReadinessV176(current).ready,evaluationReadinessV176(current).reason);const record=await this.stage('study.evaluate',study.environment_id,study.id,{expected_version:study.version,expected_study_hash:study.frozen_hash});return this.sendOriginal(record);}
 dispose(){this.disposed=true;}
}
