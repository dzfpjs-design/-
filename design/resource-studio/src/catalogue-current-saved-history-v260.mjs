// Read-only saved-state family. This is not a full source/readiness/admission
// decoder and never reads or changes any allocation original or human journal.
import {canonical,copy,uuid,hash,sha,same} from './catalogue-current-allocation-v260-protocol.mjs';

export const CURRENT_SAVED_HISTORY_BASE_V260='/setup/report-saved-map-designs';
const BASE=CURRENT_SAVED_HISTORY_BASE_V260;
const STATUSES=['queued','running','response_unknown','review','needs_clarification','failed'];
const READY=['review','needs_clarification'];
const ERROR='已保存记录、原回执或读取范围无法核对；没有重新核对当前资料，也没有发送新调用。';
const need=(v,message=ERROR)=>{if(!v)throw Error(message);};
const obj=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,fields)=>obj(v)&&Object.keys(v).sort().join('|')===fields.split(' ').sort().join('|');
const int=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const text=(v,max,required=false)=>typeof v==='string'&&Array.from(v).length<=max&&!/[\u0000\ud800-\udfff]/u.test(v)&&(!required||v.trim().length>0);
const key=v=>typeof v==='string'&&/^[A-Za-z][A-Za-z0-9_:-]{0,127}$/.test(v);
const unique=values=>new Set(values).size===values.length;
const positions=v=>Array.isArray(v)&&v.length<=1920&&v.every((p,i)=>int(p,0,1919)&&(i===0||p>v[i-1]));
const businessKeys=v=>Array.isArray(v)&&v.length<=64&&v.every(key)&&unique(v);
const FLAGS='machine_only human_review_verified current_iam_verified source_ownership_verified actual_paid_verified semantic_quality_verified human_confirmed can_quote can_apply can_confirm can_publish dispatch_allowed';
const INDEX_VERIFICATION='command_state_anchors_checked complete_operational_inventory_checked source_revalidated files_revalidated current_configuration_revalidated can_request_new_call can_apply can_publish';
const RECORD_VERIFICATION='saved_record_checked source_revalidated files_revalidated current_configuration_revalidated semantic_quality_verified can_apply can_publish';

export function currentSavedHistoryScopeV260(account,producerId,bindingId){
 need(uuid(account?.user_id)&&uuid(account?.workspace_id)&&['admin','operator','viewer'].includes(account.role)&&uuid(producerId)&&uuid(bindingId));
 return {actor_id:account.user_id,workspace_id:account.workspace_id,role:account.role,producer_id:producerId,binding_id:bindingId};
}
function verification(v,fields,trueKey){need(exact(v,fields)&&Object.entries(v).every(([k,value])=>value===(k===trueKey)));}
function startReceipt(v,consumerId,version){
 need(exact(v,'schema id version operation status job_id')&&v.schema==='report-saved-map-design-result-v176'&&v.id===consumerId&&int(v.version,1)&&v.version<=version&&v.operation==='start'&&STATUSES.includes(v.status)&&uuid(v.job_id));
}
function summary(v){
 need(exact(v,'consumer_id version status note request_sha256 start_receipt state_hash')&&uuid(v.consumer_id)&&int(v.version,1)&&STATUSES.includes(v.status)&&text(v.note,2000)&&hash(v.request_sha256)&&hash(v.state_hash));
 startReceipt(v.start_receipt,v.consumer_id,v.version);return v;
}
export function checkedCurrentSavedIndexV260(v,s,{offset,limit=20}){
 need(int(offset)&&int(limit,1,20)&&exact(v,'schema workspace_id actor_id producer_id binding_id offset limit has_more records verification')&&v.schema==='catalogue-current-saved-index-v260'&&v.workspace_id===s.workspace_id&&v.actor_id===s.actor_id&&v.producer_id===s.producer_id&&v.binding_id===s.binding_id&&v.offset===offset&&v.limit===limit&&typeof v.has_more==='boolean'&&Array.isArray(v.records)&&v.records.length<=limit&&(!v.has_more||v.records.length===limit));
 verification(v.verification,INDEX_VERIFICATION,'command_state_anchors_checked');
 v.records.forEach(summary);need(unique(v.records.map(row=>row.consumer_id)));return copy(v);
}
function claim(v){
 need(exact(v,'kind statement span_refs status explanation')&&text(v.kind,200,true)&&text(v.statement,16000)&&['explicit','inferred','unknown'].includes(v.status)&&text(v.explanation,16000)&&Array.isArray(v.span_refs)&&v.span_refs.every(ref=>text(ref,1000,true))&&unique(v.span_refs));
}
function group(v){
 need(exact(v,'key kind business_keys input_positions reason question_keys')&&key(v.key)&&['business','shared','background','pending'].includes(v.kind)&&businessKeys(v.business_keys)&&positions(v.input_positions)&&text(v.reason,4000)&&Array.isArray(v.question_keys)&&v.question_keys.every(key)&&unique(v.question_keys));
 if(v.kind==='business')need(v.business_keys.length===1);
 if(v.kind==='shared')need(v.business_keys.length>=2);
 if(v.kind==='background')need(v.business_keys.length===0);
}
function question(v){
 need(exact(v,'key kind input_positions candidate_business_keys question status resolution')&&key(v.key)&&['ownership','missing_info'].includes(v.kind)&&positions(v.input_positions)&&businessKeys(v.candidate_business_keys)&&text(v.question,4000,true)&&['open','resolved'].includes(v.status)&&text(v.resolution,8000));
}
function rawDecision(v){
 need(exact(v,'disposition business_keys reason questions')&&['assign','background','pending'].includes(v.disposition)&&businessKeys(v.business_keys)&&text(v.reason,2000,true)&&Array.isArray(v.questions)&&v.questions.length<=4);
 for(const q of v.questions)need(exact(q,'kind question')&&['ownership','missing_info'].includes(q.kind)&&text(q.question,4000,true));
 if(v.disposition==='assign')need(v.business_keys.length>0&&v.questions.every(q=>q.kind==='missing_info'));
 if(v.disposition==='background')need(v.business_keys.length===0&&v.questions.every(q=>q.kind==='missing_info'));
 if(v.disposition==='pending')need(v.questions.some(q=>q.kind==='ownership'));
}
async function savedProjection(v,s){
 need(exact(v,'schema source_pins plan_hash batch_hash batch_ordinal base_draft_ref catalogue_review_hash raw_arguments_sha256 raw_wire output_schema_hash decisions batch_decisions_complete review_required flags projection_hash')&&v.schema==='report-business-catalogue-allocation-projection-v213'&&['plan_hash','batch_hash','catalogue_review_hash','raw_arguments_sha256','output_schema_hash','projection_hash'].every(k=>hash(v[k]))&&int(v.batch_ordinal,0,1919)&&v.batch_decisions_complete===true&&v.review_required===true&&Array.isArray(v.decisions)&&v.decisions.length>=1&&v.decisions.length<=80);
 const pins=v.source_pins,ref=v.base_draft_ref;
 need(exact(pins,'source_snapshot_hash indexed_inputs_hash input_claims_hash input_order_hash catalog_hash input_count')&&int(pins.input_count,1,1920)&&Object.entries(pins).every(([k,value])=>k==='input_count'||hash(value)));
 need(exact(ref,'id workspace_id actor_id version record_hash origin_hash document_hash')&&uuid(ref.id)&&ref.workspace_id===s.workspace_id&&ref.actor_id===s.actor_id&&int(ref.version,1)&&['record_hash','origin_hash','document_hash'].every(k=>hash(ref[k])));
 verification(v.flags,FLAGS,'machine_only');
 need(exact(v.raw_wire,'schema decisions')&&v.raw_wire.schema==='report-business-catalogue-allocation-output-v213'&&obj(v.raw_wire.decisions));
 const ordered=v.decisions.map(d=>d.decision_key);
 need(unique(ordered)&&ordered.every((k,i)=>typeof k==='string'&&/^p[0-9]{6}$/.test(k)&&(i===0||k>ordered[i-1]))&&same(Object.keys(v.raw_wire.decisions).sort(),ordered));
 for(const d of v.decisions){
  need(exact(d,'decision_key input_position input_id claim source_spans before_groups before_questions raw_decision proposed_group proposed_questions question_origins platform_derived review_required')&&int(d.input_position,0,pins.input_count-1)&&d.decision_key==='p'+String(d.input_position).padStart(6,'0')&&text(d.input_id,1000,true)&&d.platform_derived===true&&d.review_required===true&&Array.isArray(d.source_spans)&&Array.isArray(d.before_groups)&&Array.isArray(d.before_questions)&&Array.isArray(d.proposed_questions)&&Array.isArray(d.question_origins));
  claim(d.claim);need(d.source_spans.length===d.claim.span_refs.length);
  for(const [i,span]of d.source_spans.entries()){
   need(exact(span,'span_ref record')&&span.span_ref===d.claim.span_refs[i]);const r=span.record;
   need(exact(r,'source_ref start end text text_hash')&&text(r.source_ref,1000,true)&&int(r.start)&&int(r.end)&&r.end>r.start&&text(r.text,900000)&&hash(r.text_hash));
  }
  rawDecision(d.raw_decision);need(same(d.raw_decision,v.raw_wire.decisions[d.decision_key]));
  for(const g of d.before_groups){group(g);need(g.input_positions.includes(d.input_position));}
  for(const q of d.before_questions){question(q);need(q.input_positions.includes(d.input_position));}
  group(d.proposed_group);
  const kind=d.raw_decision.disposition==='pending'?'pending':d.raw_decision.disposition==='background'?'background':d.raw_decision.business_keys.length===1?'business':'shared';
  need(d.proposed_group.kind===kind&&same(d.proposed_group.business_keys,d.raw_decision.business_keys)&&same(d.proposed_group.input_positions,[d.input_position])&&d.proposed_group.reason===d.raw_decision.reason&&same(d.proposed_group.question_keys,d.proposed_questions.map(q=>q.key))&&d.proposed_questions.length===d.raw_decision.questions.length&&d.question_origins.length===d.proposed_questions.length);
  for(const [i,q]of d.proposed_questions.entries()){
   question(q);need(q.kind===d.raw_decision.questions[i].kind&&q.question===d.raw_decision.questions[i].question&&same(q.input_positions,[d.input_position])&&same(q.candidate_business_keys,d.raw_decision.business_keys)&&q.status==='open'&&q.resolution==='');
   const origin=d.question_origins[i];need(exact(origin,'key decision_key question_index platform_derived model_question')&&origin.key===q.key&&origin.decision_key===d.decision_key&&origin.question_index===i&&origin.platform_derived===true&&same(origin.model_question,d.raw_decision.questions[i]));
  }
 }
 const {projection_hash,...body}=v,bytes=canonical(body);
 need(new TextEncoder().encode(bytes).byteLength<=16*1024*1024&&await sha(bytes)===projection_hash,'已保存投影摘要不一致；原建议没有通过本次读取核对。');
}
export async function checkedCurrentSavedRecordV260(v,s,indexRow){
 summary(indexRow);need(READY.includes(indexRow.status));
 need(exact(v,'schema workspace_id actor_id consumer_id version status state_hash start_receipt request_sha256 call answer verification full_read_path')&&v.schema==='catalogue-current-allocation-saved-record-v260'&&v.workspace_id===s.workspace_id&&v.actor_id===s.actor_id&&v.consumer_id===indexRow.consumer_id&&v.version===indexRow.version&&v.status===indexRow.status&&v.state_hash===indexRow.state_hash&&v.request_sha256===indexRow.request_sha256&&same(v.start_receipt,indexRow.start_receipt)&&v.full_read_path==='/api'+BASE+'/'+v.consumer_id,'已保存记录与本页索引版本不一致，请先刷新列表；未采用另一份结果。');
 verification(v.verification,RECORD_VERIFICATION,'saved_record_checked');startReceipt(v.start_receipt,v.consumer_id,v.version);
 const c=v.call;need(exact(c,'id status charged_tokens usage usage_unavailable')&&uuid(c.id)&&c.status==='succeeded'&&int(c.charged_tokens)&&typeof c.usage_unavailable==='boolean');
 if(c.usage_unavailable)need(c.usage===null);else need(exact(c.usage,'prompt_tokens completion_tokens total_tokens')&&Object.values(c.usage).every(x=>int(x))&&c.usage.prompt_tokens+c.usage.completion_tokens===c.usage.total_tokens&&c.charged_tokens===c.usage.total_tokens);
 need(exact(v.answer,'raw_arguments_sha256 projection_hash projection')&&hash(v.answer.raw_arguments_sha256)&&hash(v.answer.projection_hash));
 await savedProjection(v.answer.projection,s);
 // The API checks the original arguments bytes. Parsed raw_wire cannot be
 // re-serialized here and misrepresented as those original bytes.
 need(v.answer.raw_arguments_sha256===v.answer.projection.raw_arguments_sha256&&v.answer.projection_hash===v.answer.projection.projection_hash);
 return copy(v);
}

export class CatalogueCurrentSavedHistoryClientV260{
 constructor({account,producerId,bindingId,api,onChange=()=>{}}){this.scope=currentSavedHistoryScopeV260(account,producerId,bindingId);need(typeof api==='function');this.api=api;this.onChange=onChange;this.key=canonical(this.scope);this.closed=false;this.denied=false;this.epoch=0;this.pageSequence=0;this.recordSequence=0;this.limit=20;this.offset=0;this.requestedOffset=0;this.items=[];this.hasMore=false;this.loaded=false;this.loading=false;this.reading=false;this.error='';this.recordError='';this.listUnverified=false;this.record=null;this.selected=null;}
 view(){return {key:this.key,denied:this.denied,offset:this.offset,limit:this.limit,items:copy(this.items),hasMore:this.hasMore,loaded:this.loaded,loading:this.loading,reading:this.reading,error:this.error,recordError:this.recordError,listUnverified:this.listUnverified,record:copy(this.record),selected:copy(this.selected)};}
 emit(){if(!this.closed)this.onChange(this.view());}
 current(epoch){return !this.closed&&!this.denied&&epoch===this.epoch;}
 fence(epoch){need(this.current(epoch),'账号、工作区或历史页面已变化，迟到的读取结果未采用。');}
 deny(){this.denied=true;this.items=[];this.record=null;this.selected=null;this.loaded=false;this.hasMore=false;this.emit();}
 async identity(epoch){this.fence(epoch);let me;try{me=await this.api('/me',{method:'GET',headers:{'X-Workspace-ID':this.scope.workspace_id}});}catch(e){this.fence(epoch);if([401,403].includes(e.status))this.deny();throw e;}this.fence(epoch);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||me?.role!==this.scope.role){this.deny();throw Error('登录身份或工作区已变化，已保存历史已隐藏。');}}
 async get(path,epoch){await this.identity(epoch);let value;try{value=await this.api(path,{method:'GET',headers:{'X-Workspace-ID':this.scope.workspace_id}});}catch(e){this.fence(epoch);await this.identity(epoch);throw e;}this.fence(epoch);await this.identity(epoch);this.fence(epoch);return value;}
 open(){return this.page(0);}
 async page(offset){
  need(int(offset)&&offset%this.limit===0);const epoch=this.epoch,sequence=++this.pageSequence;this.recordSequence++;this.reading=false;this.loading=true;this.error='';this.requestedOffset=offset;this.emit();
  try{
   const query=new URLSearchParams({binding_id:this.scope.binding_id,offset:String(offset),limit:String(this.limit)}),v=checkedCurrentSavedIndexV260(await this.get(BASE+'/saved-records/by-producer/'+this.scope.producer_id+'?'+query,epoch),this.scope,{offset,limit:this.limit});this.fence(epoch);if(sequence!==this.pageSequence)return false;
   this.items=v.records;this.offset=offset;this.hasMore=v.has_more;this.loaded=true;this.listUnverified=false;this.record=null;this.selected=null;this.recordError='';return true;
  }catch(e){if(this.current(epoch)&&sequence===this.pageSequence){this.error='本次未重新核对历史列表：'+e.message;this.listUnverified=this.loaded;}return false;}
  finally{if(this.current(epoch)&&sequence===this.pageSequence){this.loading=false;this.emit();}}
 }
 recent(){return this.page(0);}
 next(){need(!this.loading&&this.hasMore);return this.page(this.offset+this.limit);}
 previous(){need(!this.loading&&this.offset>0);return this.page(Math.max(0,this.offset-this.limit));}
 retry(){return this.page(this.requestedOffset);}
 async select(consumerId){
  this.fence(this.epoch);need(!this.loading&&!this.reading);const row=this.items.find(v=>v.consumer_id===consumerId);need(row&&READY.includes(row.status),'这项活动尚无可读取的已保存建议，未触发模型或恢复命令。');
  const epoch=this.epoch,sequence=++this.recordSequence,page=this.pageSequence;this.reading=true;this.recordError='';this.emit();
  try{const v=await checkedCurrentSavedRecordV260(await this.get(BASE+'/'+consumerId+'/saved-record',epoch),this.scope,row);this.fence(epoch);if(sequence!==this.recordSequence||page!==this.pageSequence)return false;this.record=v;this.selected=copy(row);return true;}
  catch(e){if(this.current(epoch)&&sequence===this.recordSequence&&page===this.pageSequence)this.recordError='本次未重新核对这份已保存建议：'+e.message;return false;}
  finally{if(this.current(epoch)&&sequence===this.recordSequence&&page===this.pageSequence){this.reading=false;this.emit();}}
 }
 dispose(){this.closed=true;this.epoch++;this.pageSequence++;this.recordSequence++;}
}
