import {scopedPreparationRequester,exactPreparationWrite} from './understanding-preparation-contract.mjs';
import {copy,same,digest,uuid,reportScope,reportWritable,verifyReportDraft,verifyReportLocalReview} from './report-draft-contract.mjs';

export class ReportDraftLocalReview{
 constructor({account,request,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){this.account=copy(account);this.storage=storage;this.request=scopedPreparationRequester(account,request,tabStorage);this.locks=locks;this.prefix='ga-report-local-review:'+account.workspace_id+':'+account.user_id+':';this.active=true;this.busy=false;}
 check(){if(!this.active)throw Error('页面或账号已变化，原本地复核请求保留。');}
 key(row){return this.prefix+row.draft_id+':'+row.body.request_id;}
 read(key){let row;try{row=JSON.parse(this.storage.getItem(key));}catch{throw Error('本地复核日志损坏，原记录保留。');}if(!row||row.schema!=='report-local-review-outbox-v1'||!same(row.scope,reportScope(this.account))||!uuid.test(row.draft_id)||!uuid.test(row.body?.request_id)||!Number.isSafeInteger(row.body?.revision)||row.body.revision<1||!['pending','resolved','rejected'].includes(row.status)||this.key(row)!==key)throw Error('本地复核日志范围或原请求无法核对。');return row;}
 pending(draftID){const rows=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix)){const row=this.read(key);if(row.status==='pending'&&row.draft_id===draftID)rows.push(row);}}return rows;}
 write(row){this.check();exactPreparationWrite(this.storage,this.key(row),row);return row;}
 async identity(){this.check();const me=await this.request('/me');this.check();if(me.user_id!==this.account.user_id||me.workspace_id!==this.account.workspace_id||me.role!==this.account.role){const e=Error('当前登录身份已变化，原复核请求保留。');e.code='SESSION_SCOPE_CHANGED';throw e;}}
 async run(draft,{original,retry=false,probeOnly=false,beforeSend=()=>{}}={}){
  if(this.busy)return null;if(!probeOnly&&!reportWritable(this.account))throw Error('当前账号只读，不能发起本地复核。');this.busy=true;
  try{
   this.check();let pending=original&&copy(original);
   if(!pending){if(this.pending(draft.id).length)throw Error('请先核对原本地复核回执，不另起请求。');const body={revision:draft.revision,request_id:crypto.randomUUID()};pending={schema:'report-local-review-outbox-v1',scope:reportScope(this.account),draft_id:draft.id,source_hash:draft.preview.source_hash,body,request_hash:await digest(body),status:'pending',attempted:false};this.check();beforeSend();this.write(pending);}
   const execute=async()=>{
    this.check();const saved=this.read(this.key(pending));if(!same(saved,pending)||saved.status!=='pending'||await digest(saved.body)!==saved.request_hash||saved.source_hash!==draft.preview.source_hash||saved.draft_id!==draft.id)throw Error('本地复核原请求或来源已变化，未发送。');this.check();beforeSend();await this.identity();this.check();beforeSend();
    const receiptPath='/setup/report-drafts/'+pending.draft_id+'/local-review-receipts/'+pending.body.request_id;let reply;
    try{reply=await this.request(receiptPath);}catch(e){this.check();if(e.status!==404)throw e;if(probeOnly||pending.attempted&&!retry)throw Error('服务器暂未查到原本地复核回执；可明确重试同一请求，不会调用模型。');
     beforeSend();pending=this.write({...pending,attempted:true});
     let posted=false;try{await this.request('/setup/report-drafts/'+pending.draft_id+'/local-review',{method:'POST',body:JSON.stringify(pending.body)});posted=true;}catch(error){this.check();if(error.status===409||error.status===413||error.status===422){if(error.detail?.current)await verifyReportDraft(error.detail.current,this.account);this.check();this.write({...pending,status:'rejected'});throw error;}try{reply=await this.request(receiptPath);}catch{throw error;}}
     if(posted)reply=await this.request(receiptPath);
    }
    this.check();const row=await verifyReportLocalReview(reply,this.account,pending);this.check();if(!same(this.read(this.key(pending)),pending))throw Error('原本地复核日志已变化，未采用迟到回执。');
    if(reportWritable(this.account))this.write({...pending,status:'resolved',receipt:copy(reply.receipt)});this.lastReceipt=copy(reply.receipt);return row;
   };
   return this.locks?.request?await this.locks.request(this.prefix+draft.id,{mode:'exclusive'},execute):await execute();
  }finally{this.busy=false;}
 }
 dispose(){this.active=false;}
}
