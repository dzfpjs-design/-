import {scopedPreparationRequester,exactPreparationWrite} from './understanding-preparation-contract.mjs';
import {uuid,reportScope,reportWritable} from './report-draft-contract.mjs';
import {copy,same,digest,commandHash,verifyConfigurationReply,normalizeSourceProjectionRefs} from './report-configuration-contract.mjs';
export class ReportConfigurationController{
 constructor({account,request,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){this.account=copy(account);this.storage=storage;this.request=scopedPreparationRequester(account,request,tabStorage);this.locks=locks;this.prefix='ga-report-configuration:'+account.workspace_id+':'+account.user_id+':';this.owner=crypto.randomUUID();this.active=true;this.busy=false;}
 check(){if(!this.active)throw Error('账号或页面已变化，原交接请求保留。');}
 async identity(){this.check();const user=await this.request('/me');this.check();if(user.user_id!==this.account.user_id||user.workspace_id!==this.account.workspace_id||user.role!==this.account.role){const error=Error('当前账号或工作区已变化，原请求保留。');error.code='SESSION_SCOPE_CHANGED';throw error;}}
 key(row){return this.prefix+row.kind+':'+row.target+':'+row.body.request_id;}
 read(key){let row;try{row=JSON.parse(this.storage.getItem(key));}catch{throw Error('本机交接日志损坏，原记录保留。');}if(!row||row.schema!=='report-configuration-outbox-v1'||!same(row.scope,reportScope(this.account))||!['handoff','source_review'].includes(row.kind)||!uuid.test(row.target)||!uuid.test(row.body?.request_id)||!['pending','resolved','rejected'].includes(row.status)||this.key(row)!==key)throw Error('本机交接日志的范围或原请求无法核对。');return row;}
 pending(kind,target){const result=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix)&&!key.startsWith(this.prefix+'selection:')){const row=this.read(key);if(row.status==='pending'&&(!kind||row.kind===kind)&&(!target||row.target===target))result.push(row);}}return result;}
 write(row){this.check();const result=exactPreparationWrite(this.storage,this.key(row),row);globalThis.window?.dispatchEvent?.(new Event('ga-report-configuration-journal'));return result;}
 saveSelection(reportID,indexes,binding){this.check();if(!reportWritable(this.account))throw Error('当前账号只读。');exactPreparationWrite(this.storage,this.prefix+'selection:'+reportID+':'+this.owner,{schema:'report-configuration-selection-v1',scope:reportScope(this.account),owner:this.owner,report_id:reportID,business_indexes:copy(indexes),binding});}
 selections(reportID){const result=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix+'selection:'+reportID+':')){let row;try{row=JSON.parse(this.storage.getItem(key));}catch{throw Error('本机业务选择记录损坏，原记录保留。');}if(!same(row.scope,reportScope(this.account))||row.report_id!==reportID||!uuid.test(row.owner)||!Array.isArray(row.business_indexes)||row.business_indexes.some(x=>!Number.isInteger(x)||x<0))throw Error('本机业务选择范围无法核对。');if(row.owner!==this.owner)result.push(row);}}return result;}
 async run({kind,target,body,preview,original,probeOnly=false,retry=false,beforeSend=()=>{}}){
  if(this.busy)throw Error('原请求正在核对，请稍候。');if(!probeOnly&&!reportWritable(this.account))throw Error('当前账号只读。');this.busy=true;
  try{
   this.check();let pending=original&&copy(original);
   if(!pending){if(this.pending(kind,target).length)throw Error('请先核对原交接回执，不另起请求。');pending={schema:'report-configuration-outbox-v1',scope:reportScope(this.account),kind,target,body:copy(body),command_hash:await commandHash(this.account,kind,target,body),status:'pending',attempted:false,preview:preview?copy(preview):null};this.check();beforeSend();}
   const execute=async()=>{
    this.check();if(!original){if(this.pending(kind,target).length)throw Error('请先核对原交接回执，不另起请求。');beforeSend();this.write(pending);}if(!same(this.read(this.key(pending)),pending)||pending.status!=='pending'||pending.kind!==kind||pending.target!==target||pending.command_hash!==await commandHash(this.account,kind,target,pending.body))throw Error('原交接正文或日志已变化，未发送。');this.check();await this.identity();beforeSend();
    const base=kind==='handoff'?'/setup/report-drafts/'+target:'/setup/report-handoffs/'+target,receipt=base+(kind==='handoff'?'/configuration-receipts/':'/source-review-receipts/')+pending.body.request_id;let reply;
    try{reply=await this.request(receipt);}catch(e){this.check();if(e.status!==404)throw e;if(probeOnly||pending.attempted&&!retry)throw Error('服务器暂未查到原回执；保留原编号，可明确重试原请求。');beforeSend();pending=this.write({...pending,attempted:true});
     let endpoint='/configuration-handoff';if(kind==='source_review'){endpoint='/source-review';if(pending.body.review_mode!==undefined){if(pending.body.review_mode!=='confirmed_final_view_v1'||pending.body.final_view_confirmed!==true)throw Error('原来源核对方式或明确确认无法核对，原请求保留；未发送。');normalizeSourceProjectionRefs(pending.body.projection_refs);endpoint='/projected-source-review';}}
     try{await this.request(base+endpoint,{method:'POST',body:JSON.stringify(pending.body)});}catch(error){this.check();if([409,413,422].includes(error.status)){this.write({...pending,status:'rejected'});throw error;}try{reply=await this.request(receipt);}catch{throw error;}}
     if(!reply)reply=await this.request(receipt);
    }
    this.check();await verifyConfigurationReply(reply,this.account,pending);this.check();beforeSend();if(!same(this.read(this.key(pending)),pending))throw Error('原交接日志已变化，未采用迟到回执。');
    // Adoption can fail on a later human edit. Keep the exact pending receipt recoverable.
    return {reply,pending};
   };
   return this.locks?.request?await this.locks.request(this.prefix+kind+':'+target,{mode:'exclusive'},execute):await execute();
  }finally{this.busy=false;}
 }
 settle(pending,reply){this.check();if(!same(this.read(this.key(pending)),pending))throw Error('原交接日志已变化，未清除。');if(reportWritable(this.account))this.write({...pending,status:'resolved',receipt:copy(reply.receipt)});}
 dispose(){this.active=false;}
}
