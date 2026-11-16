import {scopedPreparationRequester,exactPreparationWrite} from './understanding-preparation-contract.mjs';
import {fullKeys,fullUUID,fullSha} from './report-evidence-reading-contract.mjs';
import {copy,same,fullSemanticScope,fullSemanticWritable,checkFullSemanticCommand,fullSemanticCommandHash,verifyFullSemanticPreview,verifyFullSemanticReply} from './full-semantic-handoff-contract.mjs';
import {replacementPending} from './full-semantic-replacement-contract.mjs';
const invalid=()=>{throw Error('本机完整设计交接记录无法核对；原记录保留，未发送。');};
export class FullSemanticHandoffController{
 constructor({account,request,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){this.account=copy(account);this.storage=storage;this.request=scopedPreparationRequester(account,request,tabStorage);this.locks=locks;this.prefix='ga-full-semantic-handoff:'+account.workspace_id+':'+account.user_id+':';this.active=true;this.busy=false;}
 check(){if(!this.active||this.suspended)throw Error('账号或页面已变化，原完整设计交接请求保留。');}
 async identity(){this.check();const user=await this.request('/me');this.check();if(user.user_id!==this.account.user_id||user.workspace_id!==this.account.workspace_id||user.role!==this.account.role)throw Object.assign(Error('账号或工作区已变化，未继续交接。'),{code:'SESSION_SCOPE_CHANGED'});}
 key(entry){return this.prefix+entry.body.request_id;}
 read(key){let e;try{e=JSON.parse(this.storage.getItem(key));}catch{invalid();}if(!e||!fullKeys(e,'schema,scope,report_id,body,command_hash,preview,attempted,status,receipt')||e.schema!=='report-full-semantic-handoff-outbox-v1'||!same(e.scope,fullSemanticScope(this.account))||!fullUUID.test(e.report_id)||!fullSha.test(e.command_hash)||typeof e.attempted!=='boolean'||!['pending','rejected','resolved'].includes(e.status)||e.receipt!==null&&typeof e.receipt!=='object')invalid();checkFullSemanticCommand(e.body,{submit:true});if(this.key(e)!==key)invalid();return e;}
 pending(){const result=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix)){const e=this.read(key);if(e.status==='pending')result.push(e);}}return result;}
 exact(entry){this.check();if(!same(this.read(this.key(entry)),entry))throw Error('原交接记录已变化；未覆盖或发送。');}
 write(entry){this.check();const saved=exactPreparationWrite(this.storage,this.key(entry),entry);globalThis.window?.dispatchEvent?.(new Event('ga-full-semantic-handoff-journal'));return saved;}
 async validate(entry){checkFullSemanticCommand(entry.body,{submit:true});await verifyFullSemanticPreview(entry.preview,this.account);const {preview_hash,consent,...body}=entry.body;if(entry.report_id!==entry.preview.report_id||preview_hash!==entry.preview.preview_hash||!same(body,entry.preview.body)||entry.command_hash!==await fullSemanticCommandHash(this.account,entry.report_id,entry.body))invalid();this.check();}
 async run({preview,original=null,allowPost=false,beforeSend=()=>{}}){
  if(this.busy)throw Error('正在核对原交接，请稍候。');if(allowPost&&!fullSemanticWritable(this.account))throw Error('当前账号只读。');this.busy=true;
  try{
   let entry=original?copy(original):null;
   if(!entry){if(!allowPost)throw Error('尚无原交接请求可核对。');await verifyFullSemanticPreview(preview,this.account);const body={...copy(preview.body),preview_hash:preview.preview_hash,consent:true};entry={schema:'report-full-semantic-handoff-outbox-v1',scope:fullSemanticScope(this.account),report_id:preview.report_id,body,command_hash:await fullSemanticCommandHash(this.account,preview.report_id,body),preview:copy(preview),attempted:false,status:'pending',receipt:null};}
   const execute=async()=>{
    this.check();beforeSend();if(!original){if(this.pending().length||replacementPending(this.account,this.storage).length)throw Error('请先核对原加入或替换回执，不另起请求。');if(this.storage.getItem(this.key(entry))!==null)throw Error('此交接编号已有记录，未覆盖。');this.write(entry);}this.exact(entry);await this.validate(entry);if(entry.status!=='pending')throw Error('原交接已结束，未重发。');await this.identity();beforeSend();this.exact(entry);
    const base='/setup/report-evidence-readings/'+entry.report_id,receipt=base+'/full-configuration-receipts/'+entry.body.request_id;let reply;
    try{reply=await this.request(receipt);}catch(error){this.check();if(error.status!==404)throw error;if(!allowPost)throw Error('尚未查到原加入结果；原编号保留，可明确重试同一请求。');beforeSend();this.exact(entry);entry=this.write({...entry,attempted:true});
     try{await this.request(base+'/full-configuration-handoff',{method:'POST',body:JSON.stringify(entry.body)});}catch(failure){this.check();if([409,413,422].includes(failure.status)){this.exact(entry);this.write({...entry,status:'rejected'});throw failure;}try{reply=await this.request(receipt);}catch{throw failure;}}
     if(!reply)reply=await this.request(receipt);
    }
    this.check();await verifyFullSemanticReply(reply,this.account,entry);this.check();this.exact(entry);return {reply,pending:entry};
   };
   return this.locks?.request?await this.locks.request(this.prefix+'lock',{mode:'exclusive'},execute):await execute();
  }finally{this.busy=false;}
 }
 settle(entry,reply){this.exact(entry);if(!fullSemanticWritable(this.account))throw Error('当前账号只读，原回执保留。');return this.write({...entry,status:'resolved',receipt:copy(reply.receipt)});}
 dispose(){this.active=false;}
}
