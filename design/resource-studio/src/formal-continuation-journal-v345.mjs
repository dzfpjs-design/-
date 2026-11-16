// Durable originals for a single actor/task/advice. This module never sends.
import {pixelCanonical as canonical,pixelSHA as sha,pixelUUID as uuid,pixelHash as hash,pixelNeed as need} from './pixel-change-v249-protocol.mjs';
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const positive=v=>Number.isSafeInteger(v)&&v>0;
const text=(v,max)=>typeof v==='string'&&v.trim().length>0&&Array.from(v).length<=max&&!/[\u0000\ud800-\udfff]/u.test(v);
const allowed={create:['request_id','expected_task_version','execution_id','context_hash','acknowledge_cross_version','reason','preview_hash'],
 activate:['request_id','expected_version','reason'],withdraw:['request_id','expected_version','reason'],
 complete_step:['request_id','expected_version','reason','step_key','result'],return_step:['request_id','expected_version','reason','step_key']};
export function formalScope(account,task,advice){
 const s={actor_id:account?.user_id,workspace_id:account?.workspace_id,task_id:task?.id,release_id:task?.instance_release_id,advice_id:advice?.plan_id};
 need(Object.values(s).every(uuid),'账号、任务或原建议范围尚未核对。');return s;
}
export const formalPrefix=scope=>'ga-formal-continuation-v345:'+canonical(scope)+':';
export class FormalContinuationJournalV345{
 constructor({scope,storage=globalThis.localStorage,crypto=globalThis.crypto,locks=globalThis.navigator?.locks,check=()=>{}}){
  need(exact(scope,['actor_id','workspace_id','task_id','release_id','advice_id'])&&Object.values(scope).every(uuid));
  Object.assign(this,{scope,storage,crypto,locks,check});this.prefix=formalPrefix(scope);
  this.base='/workspace/tasks/'+scope.task_id+'/business-advice/preparations/'+scope.advice_id+'/formal-continuation';
 }
 path(operation,planId){
  need(Object.hasOwn(allowed,operation));
  if(operation==='create'){need(planId===null);return this.base;}
  need(uuid(planId));const suffix={activate:'activate',withdraw:'withdraw',complete_step:'complete-step',return_step:'return-step'}[operation];
  return this.base+'/'+planId+'/'+suffix;
 }
 async checked(raw){
  let p;try{p=JSON.parse(raw);}catch{throw Error('本机原申请损坏，原件保留，请恢复存储后核对。');}
  need(exact(p,['scope','operation','plan_id','path','body','body_hash','raw_body'])&&canonical(p.scope)===canonical(this.scope));
  need(Object.hasOwn(allowed,p.operation)&&exact(p.body,allowed[p.operation])&&uuid(p.body.request_id));
  need(p.path===this.path(p.operation,p.plan_id)&&p.raw_body===canonical(p.body)&&hash(p.body_hash)&&await sha(p.raw_body,this.crypto)===p.body_hash);
  need(text(p.body.reason,2000));
  if(p.operation==='create')need(positive(p.body.expected_task_version)&&uuid(p.body.execution_id)&&hash(p.body.context_hash)&&hash(p.body.preview_hash)&&typeof p.body.acknowledge_cross_version==='boolean');
  else {need(positive(p.body.expected_version));if(['complete_step','return_step'].includes(p.operation))need(text(p.body.step_key,200));if(p.operation==='complete_step')need(text(p.body.result,12000));}
  return p;
 }
 write(key,value,{immutable=false}={}){
  this.check();const old=this.storage.getItem(key);need(!immutable||old===null||old===value,'本机已有不同的原申请，未覆盖。');
  this.storage.setItem(key,value);need(this.storage.getItem(key)===value,'本机原申请未完整保存，尚未发送。');
 }
 async lock(work){need(this.locks?.request,'当前浏览器无法保护多窗口原申请；仅能查看，不能提交。');return this.locks.request(this.prefix+'lock',{mode:'exclusive'},async()=>{this.check();return work();});}
 keys(){const result=[];for(let i=0;i<this.storage.length;i++){const k=this.storage.key(i);if(k?.startsWith(this.prefix+'original:'))result.push(k);}
  need(result.length<=1000,'本机原申请过多，请先核对历史。');return result;}
 async pending(){
  this.check();const pointer=this.storage.getItem(this.prefix+'pending');
  if(pointer!==null){const p=await this.checked(pointer),original=this.storage.getItem(this.prefix+'original:'+p.body.request_id);
   need(original===pointer,'等待申请与本机原件不一致，原件保留，请先修复。');return p;}
  const unresolved=[];
  for(const key of this.keys()){
   const raw=this.storage.getItem(key),p=await this.checked(raw);need(key===this.prefix+'original:'+p.body.request_id);
   const settled=this.storage.getItem(this.prefix+'settled:'+p.body.request_id);
   if(settled===null)unresolved.push(p);else {let marker;try{marker=JSON.parse(settled);}catch{throw Error('本机回执损坏，原申请保留。');}
    need(marker?.body_hash===p.body_hash&&marker?.request_id===p.body.request_id&&hash(marker?.record_hash),'本机回执与原申请不一致。');}
  }
  need(unresolved.length<=1,'本机有多份待核对的原申请，暂不创建另一份。');return unresolved[0]||null;
 }
 async stage(operation,planId,body){
  return this.lock(async()=>{
   need(await this.pending()===null,'已有原申请待核对，未生成另一请求。');
   const raw_body=canonical(body),p={scope:this.scope,operation,plan_id:planId,path:this.path(operation,planId),body,body_hash:await sha(raw_body,this.crypto),raw_body};
   const raw=canonical(await this.checked(canonical(p)));this.write(this.prefix+'original:'+body.request_id,raw,{immutable:true});this.write(this.prefix+'pending',raw);return p;
  });
 }
 async repair(p){return this.lock(async()=>{const raw=canonical(await this.checked(canonical(p))),key=this.prefix+'original:'+p.body.request_id;
  this.write(key,raw,{immutable:true});const pointer=this.storage.getItem(this.prefix+'pending');need(pointer===null||pointer===raw,'另一页面已有不同的待核对申请。');this.write(this.prefix+'pending',raw);return p;});}
 async abandonment(p){
  const raw=this.storage.getItem(this.prefix+'abandon:'+p.body.request_id);if(raw===null)return null;
  let body;try{body=JSON.parse(raw);}catch{throw Error('终止申请的本机原件损坏，原申请保留。');}
  need(exact(body,['request_id','operation','plan_id','original_body','reason'])&&body.request_id===p.body.request_id
   &&body.operation===p.operation&&body.plan_id===p.plan_id&&canonical(body.original_body)===canonical(p.body)&&text(body.reason,2000)
   &&raw===canonical(body),'终止申请与原申请不一致，未覆盖。');return {body,raw};
 }
 async markAbandon(p,reason){return this.lock(async()=>{
  const current=await this.pending();need(current&&canonical(current)===canonical(p),'原申请已变化，请先核对。');need(text(reason,2000),'请填写终止原因。');
  const body={request_id:p.body.request_id,operation:p.operation,plan_id:p.plan_id,original_body:p.body,reason};
  const raw=canonical(body);this.write(this.prefix+'abandon:'+p.body.request_id,raw,{immutable:true});return {body,raw};
 });}
 async settle(p,result){return this.lock(async()=>{
  const saved=await this.pending();need(saved&&canonical(saved)===canonical(p),'本机原申请已变化，请重新核对。');
  need(result?.found===true&&result.body_hash===p.body_hash);
  const abandoned=result.abandoned===true,receipt=abandoned?result:result.receipt;
  need(receipt?.request_id===p.body.request_id&&receipt?.operation===p.operation&&hash(receipt?.record_hash));
  if(abandoned)need(receipt.plan_id===p.plan_id);else {need(uuid(receipt.plan_id));if(p.plan_id!==null)need(receipt.plan_id===p.plan_id);}
  const marker=canonical({request_id:p.body.request_id,body_hash:p.body_hash,record_hash:receipt.record_hash});
  this.write(this.prefix+'settled:'+p.body.request_id,marker,{immutable:true});
  const raw=canonical(p),pointer=this.storage.getItem(this.prefix+'pending');need(pointer===null||pointer===raw,'另一页面改变了原申请，未清除。');
  if(pointer!==null){this.storage.removeItem(this.prefix+'pending');need(this.storage.getItem(this.prefix+'pending')===null,'本机待核对标记未能清除。');}
  return abandoned?{abandoned:true,request_id:p.body.request_id}:receipt;
 });}
}
