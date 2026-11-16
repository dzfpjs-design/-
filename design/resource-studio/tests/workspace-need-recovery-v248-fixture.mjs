// Synthetic persistence and transport for real components/controllers. Root
// separately proves ordinary HTTP, PostgreSQL, restart and target-browser use.
import {webcrypto} from 'node:crypto';
import {needFixture,id,copy,sha,initializeNeedController} from './workspace-need-v246-fixture.mjs';
import {blankNeedDraft,makeNeedOriginal,needCanonical} from '../src/workspace-need-v246-protocol.mjs';
export {id,copy,sha,initializeNeedController};
export function needRecoveryFixture(){
 const f=needFixture(),transport=f.api,makeOriginal=f.make;
 f.state.lookupEvidence='typed_body_receipt';
 const recovery={paused:false,hideRecent:false,lookupVisible:true,lookupGate:null,lookupCalls:[],lookups:0,lookupOverride:null,detailOverride:null,detailReads:0,postAttempts:[]};
 function historic(view){
  if(!recovery.paused)return view;
  const v=copy(view);v.scope_valid=false;v.records=[];delete v.archive_evidence;v.reason='原实例或业务范围已改变；保留历史核对，不读取新范围证据';
  const d=v.copy_descriptor;d.target.release_id=null;d.target.fields=[];d.can_copy=false;d.reasons=['当前实例已暂停，不能复制或创建新的需求'];delete d.descriptor_hash;d.descriptor_hash=sha(needCanonical(d));return v;
 }
 f.api=async(path,options={})=>{
  if(options.method==='POST')recovery.postAttempts.push({path,raw:options.body});
  if(path.startsWith('/workspace/instances/'+f.instanceId+'/data-need-requests?')){
   recovery.lookups++;recovery.lookupCalls.push({path,method:options.method||'GET'});if(recovery.lookupGate)await recovery.lookupGate;
   f.state.lookupVisible=recovery.lookupVisible;const reply=await transport(path,options);return recovery.lookupOverride?recovery.lookupOverride(reply):reply;
  }
  if(path===f.base+'/options'&&recovery.paused)throw Object.assign(Error('当前实例已暂停，新需求暂不能提交'),{status:409});
  if(path===f.base&&options.method==='POST'&&recovery.paused)throw Object.assign(Error('当前实例已暂停，原请求也不能通过新建入口重发'),{status:409});
  if(path===f.base&&options.method!=='POST'&&recovery.hideRecent)return {items:[],truncated:true};
  const value=await transport(path,options);
  if(path.startsWith(f.base+'/')&&path!==f.base+'/options'&&!path.includes('/requests/')){recovery.detailReads++;const detail=historic(value);return recovery.detailOverride?recovery.detailOverride(detail):detail;}
  return value;
 };
 async function seedOriginal({saved=true,question=f.question,requestID=id(91),queryID=id(92),formQuestion=question,bodyOverrides={}}={}){
  const body={...copy(f.originalBody),...bodyOverrides,request_id:requestID,purpose:question},draft={...blankNeedDraft(f.scope),release_id:body.release_id,form:{business_id:body.business_id,entity_id:body.entity_id,purpose:question,fields:[...body.fields],source:body.source,confirmation:body.confirmation,max_age_hours:String(body.max_age_hours)}};
  const original=await makeNeedOriginal(f.scope,body,webcrypto,draft);
  f.storage.setItem(f.prefix+'form',JSON.stringify({...draft,form:{...draft.form,purpose:formQuestion}}));f.storage.setItem(f.prefix+'original:'+body.request_id,JSON.stringify(original));f.storage.setItem(f.prefix+'pending',JSON.stringify(original));
  if(saved)f.state.created.set(body.request_id,{body:copy(body),id:queryID});return original;
 }
 f.make=extra=>makeOriginal({api:f.api,...extra});
 f.ready=async(controller=f.make())=>{await controller.start();controller.edit({business_id:f.businessId,entity_id:f.entityId,purpose:'当前人稿需要保留',fields:[f.field]});return controller;};
 return {...f,recovery,historic,seedOriginal};
}
