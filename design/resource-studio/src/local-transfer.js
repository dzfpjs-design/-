// Explicit localhost migration request. Never export on page load or to arbitrary origins.
import {getMaterial} from './material-store';
import {withoutSecrets} from './catalog';
export function enableLocalTransfer(){
 if(location.origin!=='http://127.0.0.1:4187')return;
 window.addEventListener('message',async event=>{
  if(event.origin!=='http://127.0.0.1:8765'||event.source!==window.parent||window.parent===window)return;
  const {kind,nonce}=event.data||{};
  if(kind!=='setup-import-request'||typeof nonce!=='string'||nonce.length!==36)return;
  try{
   const raw=localStorage.getItem('three-agent-resource-design-v1');
   if(!raw)throw Error('原配置页面没有保存记录。');
   const draft=withoutSecrets(JSON.parse(raw)),files=[];
   for(const m of draft.materials||[]){const rec=await getMaterial(m.id);files.push({id:m.id,name:m.name,type:m.type,bytes:await rec.blob.arrayBuffer()});}
   event.source.postMessage({kind:'setup-import-response',nonce,draft,files},event.origin,files.map(f=>f.bytes));
  }catch(e){event.source.postMessage({kind:'setup-import-response',nonce,error:e.message},event.origin);}
 });
}
export async function requestLocalTransfer(){
 const frame=document.createElement('iframe');frame.hidden=true;frame.src='http://127.0.0.1:4187/?view=domain';
 const nonce=crypto.randomUUID();
 return new Promise((resolve,reject)=>{
  const cleanup=()=>{clearTimeout(timer);window.removeEventListener('message',receive);frame.remove();};
  const receive=event=>{if(event.origin!=='http://127.0.0.1:4187'||event.source!==frame.contentWindow||event.data?.kind!=='setup-import-response'||event.data.nonce!==nonce)return;cleanup();event.data.error?reject(Error(event.data.error)):resolve(event.data);};
  const timer=setTimeout(()=>{cleanup();reject(Error('原配置页面未响应，请恢复4187配置服务后重试。'));},10000);
  window.addEventListener('message',receive);frame.onload=()=>frame.contentWindow.postMessage({kind:'setup-import-request',nonce},'http://127.0.0.1:4187');document.body.append(frame);
 });
}
