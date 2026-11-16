// Store request metadata, never raster bytes. The server's SHA check owns file identity.
export const uploadKey=(workspace,user)=>'ga-geo-upload:'+workspace+':'+user;
const filename=f=>f.name.replaceAll('\\','/').split('/').at(-1).slice(0,250);
export class UploadJournal{
 constructor(storage,key){this.storage=storage;this.key=key;const raw=storage.getItem(key);this.intent=raw?JSON.parse(raw):null;if(this.intent&&(!/^[a-zA-Z0-9_-]{12,100}$/.test(this.intent.request_id)||typeof this.intent.name!=='string'||!Number.isInteger(this.intent.size)||this.intent.size<=0))throw Error('上传恢复记录无法读取，原记录保留。请勿另建请求。');}
 begin(file,id){if(this.intent){if(this.intent.name!==filename(file)||this.intent.size!==file.size)throw Error('上次上传结果尚待核对，请重新选择原文件或先核对已保存结果。');return this.intent;}if(!file.size||file.size>512*1024*1024)throw Error('请选择非空且不超过512MB的GeoTIFF原件。');const intent={request_id:id(),name:filename(file),size:file.size};this.storage.setItem(this.key,JSON.stringify(intent));this.intent=intent;return intent;}
 confirm(row){const i=this.intent;if(!i||row.upload_request_id!==i.request_id||row.name!==i.name||row.size_bytes!==i.size||!/^[a-f0-9]{64}$/.test(row.input_sha256))throw Error('服务器保存记录与原上传请求不一致；请求保留，请核对原件。');this.storage.removeItem(this.key);this.intent=null;return row;}
}
export async function resolveAssociation(body,query,send){
 try{return await query(body.request_id);}catch(e){if(e.status!==404)throw e;}
 try{return await send(body);}catch(e){
  e.definitelyRejected=false;
  try{return await query(body.request_id);}catch(check){if(check.status===404&&[400,403,409,415,422].includes(e.status))e.definitelyRejected=true;}
  throw e;
 }
}
