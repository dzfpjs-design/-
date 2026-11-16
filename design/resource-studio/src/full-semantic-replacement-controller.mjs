import {scopedPreparationRequester,exactPreparationWrite} from './understanding-preparation-contract.mjs';
import {copy,same,fullSemanticScope,fullSemanticWritable} from './full-semantic-handoff-contract.mjs';
import {FullSemanticHandoffController} from './full-semantic-handoff-controller.mjs';
import {replacementPrefix,replacementPending,readReplacementEntry,verifyReplacementPreview,verifyReplacementReply,replacementCommandHash,verifyFullSemanticVersionView} from './full-semantic-replacement-contract.mjs';
export class FullSemanticReplacementController{
 constructor({account,request,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){this.account=copy(account);this.storage=storage;this.request=scopedPreparationRequester(account,request,tabStorage);this.locks=locks;this.prefix=replacementPrefix(account);this.lock='ga-full-semantic-handoff:'+account.workspace_id+':'+account.user_id+':lock';this.old=new FullSemanticHandoffController({account,request,storage,tabStorage,locks});this.active=true;this.busy=false;}
 check(){if(!this.active)throw Error('账号或页面已变化，原替换请求保留。');}
 async identity(){this.check();const user=await this.request('/me');this.check();if(user.user_id!==this.account.user_id||user.workspace_id!==this.account.workspace_id||user.role!==this.account.role)throw Object.assign(Error('当前账号或工作区已变化，未继续替换。'),{code:'SESSION_SCOPE_CHANGED'});}
 key(e){return this.prefix+e.body.request_id;}
 read(key){const e=readReplacementEntry(this.storage.getItem(key),this.account);if(this.key(e)!==key)throw Error('原替换记录的范围无法核对，未发送。');return e;}
 pending(){return replacementPending(this.account,this.storage);}
 hasPending(){return this.pending().length>0||this.old.pending().length>0;}
 exact(e){this.check();if(!same(this.read(this.key(e)),e))throw Error('原替换正文或记录已变化，未发送或清除。');}
 write(e){this.check();const saved=exactPreparationWrite(this.storage,this.key(e),e);globalThis.window?.dispatchEvent?.(new Event('ga-full-semantic-replacement-journal'));return saved;}
 async readVersion(id){await this.identity();const view=await this.request('/setup/full-semantic-design-versions/'+id);await verifyFullSemanticVersionView(view,this.account,id);this.check();return view;}
 async validate(e){await verifyReplacementPreview(e.preview,this.account);const {preview_hash,consent,...body}=e.body;if(e.report_id!==e.preview.report_id||preview_hash!==e.preview.preview_hash||!same(body,e.preview.body)||e.command_hash!==await replacementCommandHash(this.account,e.report_id,e.body))throw Error('原替换范围与摘要不同，未发送。');this.check();}
 async run({preview,original=null,allowPost=false,beforeSend=()=>{}}){
  if(this.busy)throw Error('正在核对原替换，请稍候。');if(allowPost&&!fullSemanticWritable(this.account))throw Error('当前账号只读。');this.busy=true;
  try{let e=original?copy(original):null;if(!e){if(!allowPost)throw Error('尚无原替换请求。');await verifyReplacementPreview(preview,this.account);const body={...copy(preview.body),preview_hash:preview.preview_hash,consent:true};e={schema:'report-full-semantic-replacement-outbox-v1',scope:fullSemanticScope(this.account),report_id:preview.report_id,body,command_hash:await replacementCommandHash(this.account,preview.report_id,body),preview:copy(preview),attempted:false,status:'pending',receipt:null};}
   const execute=async()=>{this.check();beforeSend();if(!original){if(this.hasPending())throw Error('请先核对原加入或替换回执，不另起请求。');if(this.storage.getItem(this.key(e))!==null)throw Error('此替换编号已有记录，未覆盖。');this.write(e);}this.exact(e);await this.validate(e);if(e.status!=='pending')throw Error('原替换请求已结束，未重发。');await this.identity();beforeSend();this.exact(e);
    const base='/setup/report-evidence-readings/'+e.report_id,receipt=base+'/full-configuration-replacement-receipts/'+e.body.request_id;let reply;
    try{reply=await this.request(receipt);}catch(error){this.check();if(error.status!==404)throw error;if(!allowPost)throw Error('暂未查到原替换回执，原编号保留；可明确重试同一请求。');beforeSend();this.exact(e);e=this.write({...e,attempted:true});try{await this.request(base+'/full-configuration-replacement',{method:'POST',body:JSON.stringify(e.body)});}catch(failure){this.check();if([409,413,422].includes(failure.status)){this.exact(e);this.write({...e,status:'rejected'});throw failure;}try{reply=await this.request(receipt);}catch{throw failure;}}if(!reply)reply=await this.request(receipt);}
    this.check();await verifyReplacementReply(reply,this.account,e);this.check();this.exact(e);return {reply,pending:e};};
   return this.locks?.request?await this.locks.request(this.lock,{mode:'exclusive'},execute):await execute();
  }finally{this.busy=false;}
 }
 settle(e,reply){this.exact(e);if(!fullSemanticWritable(this.account))throw Error('当前账号只读，原替换回执保留。');return this.write({...e,status:'resolved',receipt:copy(reply.receipt)});}
 dispose(){this.active=false;this.old.dispose();}
}
