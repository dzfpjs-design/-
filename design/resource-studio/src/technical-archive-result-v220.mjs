import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';

export const TECHNICAL_ARCHIVE_RESULT='workflow-quality-archive-result-v220',TECHNICAL_ARTIFACT_MAX=2*1024*1024;
export const TECHNICAL_STATUS={pass:'技术核验通过',needs_review:'仍需技术复核',blocked:'存在技术阻断'};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
const obj=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),copy=v=>structuredClone(v);
const need=(v,m='内部技术汇总或原文件无法核对，未显示或下载。')=>{if(!v)throw Error(m);};
const keys=(v,n)=>obj(v)&&Object.keys(v).sort().join('|')===[...n].sort().join('|');
const FLAGS={internal_technical_summary:true,formal_deliverable:false,physical_state_verified:false,species_identification_verified:false,semantic_completeness_verified:false,independent_data_ack:false,originals_deleted:false,originals_modified:false,cloud_authorized:false};
export function checkedTechnicalArchiveV220(value){
 need(keys(value,['schema','technical_status','source_report_hashes','artifacts','archive_hash',...Object.keys(FLAGS)])&&value.schema===TECHNICAL_ARCHIVE_RESULT&&Object.hasOwn(TECHNICAL_STATUS,value.technical_status)&&SHA.test(value.archive_hash||'')&&Object.entries(FLAGS).every(([k,v])=>value[k]===v));
 need(keys(value.source_report_hashes,['quality','dedup'])&&Object.values(value.source_report_hashes).every(v=>typeof v==='string'&&SHA.test(v))&&keys(value.artifacts,['markdown','machine_json']));
 for(const[kind,type]of [['markdown','text/markdown'],['machine_json','application/json']]){const d=value.artifacts[kind];need(keys(d,['evidence_id','sha256','size_bytes','media_type'])&&UUID.test(d.evidence_id||'')&&SHA.test(d.sha256||'')&&Number.isInteger(d.size_bytes)&&d.size_bytes>0&&d.size_bytes<=TECHNICAL_ARTIFACT_MAX&&d.media_type===type);}
 need(value.artifacts.markdown.evidence_id!==value.artifacts.machine_json.evidence_id);return value;
}
export function technicalArtifactPathV220(taskId,evidenceId,download=false){need(UUID.test(taskId||'')&&UUID.test(evidenceId||'')&&typeof download==='boolean');return '/workspace/tasks/'+taskId+'/technical-artifacts/'+evidenceId+'?download='+String(download);}
export async function technicalArtifactSHA(bytes,crypto=globalThis.crypto){need(crypto?.subtle);return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');}
export async function readTechnicalArtifactV220(response,descriptor,kind,{signal,crypto=globalThis.crypto}={}){
 const cancel=async()=>{try{await response?.body?.cancel();}catch{}};
 try{
  need(response&&response.ok===true&&['markdown','machine_json'].includes(kind));
  const type=String(response.headers?.get('content-type')||'').split(';')[0].trim().toLowerCase(),length=response.headers?.get('content-length'),sha=response.headers?.get('x-content-sha256');
  need(type===descriptor.media_type&&/^[1-9][0-9]*$/.test(length||'')&&Number(length)===descriptor.size_bytes&&Number(length)<=TECHNICAL_ARTIFACT_MAX&&sha===descriptor.sha256&&response.headers?.get('x-technical-artifact-kind')===kind,'文件响应的类型、大小或摘要与任务回执不一致，未读取为成果。');
  need(response.body?.getReader,'当前浏览器不能安全读取完整技术文件，请使用支持流式读取的浏览器。');
  const reader=response.body.getReader(),chunks=[];let size=0;const stop=()=>{reader.cancel().catch(()=>{});};signal?.addEventListener('abort',stop,{once:true});
  try{for(;;){need(!signal?.aborted,'已停止读取，原技术文件保持不变。');const part=await reader.read();need(!signal?.aborted,'已停止读取，原技术文件保持不变。');if(part.done)break;need(part.value instanceof Uint8Array,'技术文件字节流无法核对。');size+=part.value.byteLength;need(size<=descriptor.size_bytes&&size<=TECHNICAL_ARTIFACT_MAX,'技术文件超过声明容量，已停止读取，未截取后当作完整文件。');chunks.push(part.value);}need(size===descriptor.size_bytes,'技术文件尚未完整到达，未预览或下载。');}
  catch(e){try{await reader.cancel();}catch{}throw e;}
  finally{signal?.removeEventListener('abort',stop);reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}need(await technicalArtifactSHA(bytes,crypto)===descriptor.sha256,'实际文件摘要与原任务回执不一致，未预览或下载。');need(!signal?.aborted,'已停止读取，原技术文件保持不变。');return {bytes,type};
 }catch(e){await cancel();throw e;}
}

// Controlled technical Markdown stays text-only. Links, images and HTML are
// never executed. The original evidence index remains available in a separate
// collapsed section and the complete byte-identical download.
export function technicalMarkdownV220(text){
 need(typeof text==='string'&&text.length>0);const body=[],advanced=[];let hidden=false;
 for(const line of text.replace(/\r\n?/g,'\n').split('\n')){if(/^##\s+证据与报告索引\s*$/.test(line)){hidden=true;advanced.push(line);continue;}if(hidden&&/^##\s+/.test(line))hidden=false;(hidden?advanced:body).push(line);}
 function blocks(lines){const result=[];for(let i=0;i<lines.length;i++){const line=lines[i];if(!line.trim())continue;const heading=/^(#{1,6})\s+(.+)$/.exec(line);if(heading){result.push({kind:'heading',level:heading[1].length,text:heading[2]});continue;}if(/^\|.*\|\s*$/.test(line)&&i+1<lines.length&&/^\|(?:\s*:?-+:?\s*\|)+\s*$/.test(lines[i+1])){const cells=row=>row.trim().slice(1,-1).split('|').map(s=>s.trim()),rows=[];const headers=cells(line);i++;while(i+1<lines.length&&/^\|.*\|\s*$/.test(lines[i+1]))rows.push(cells(lines[++i]));result.push({kind:'table',headers,rows});continue;}if(/^[-*]\s+/.test(line)){const items=[line.replace(/^[-*]\s+/,'')];while(i+1<lines.length&&/^[-*]\s+/.test(lines[i+1]))items.push(lines[++i].replace(/^[-*]\s+/,''));result.push({kind:'list',items});continue;}result.push({kind:'paragraph',text:line});}return result;}
 return {body:blocks(body),advanced:blocks(advanced)};
}

export class TechnicalArchiveReaderV220{
 constructor({value,taskId,account,requestJSON,readBinary,session=globalThis.sessionStorage,crypto=globalThis.crypto,urlAPI=globalThis.URL,currentAvailable=()=>true,onChange=()=>{}}){
  checkedTechnicalArchiveV220(value);need(UUID.test(taskId||'')&&UUID.test(account?.user_id||'')&&UUID.test(account?.workspace_id||'')&&typeof account.role==='string','当前任务与账号上下文尚未核对，不能打开内部文件。');
  this.value=copy(value);this.scope={task_id:taskId,actor_id:account.user_id,workspace_id:account.workspace_id};this.role=account.role;this.session=session;this.token=session.getItem('ga-setup-csrf');this.json=scopedPreparationRequester(account,requestJSON,session);this.binary=scopedPreparationRequester(account,readBinary,session);this.crypto=crypto;this.urlAPI=urlAPI;this.currentAvailable=currentAvailable;this.onChange=onChange;this.active=true;this.suspended=false;this.denied=false;this.generation=0;this.abort=null;this.urls=new Set();this.busy=false;this.error='';this.notice='';this.preview=null;this.download=null;this.last=null;
 }
 notify(){if(this.active&&!this.suspended)this.onChange(this);}
 clearURLs(){for(const url of this.urls)this.urlAPI.revokeObjectURL?.(url);this.urls.clear();this.download=null;}
 deny(){this.denied=true;this.preview=null;this.clearURLs();this.abort?.abort();this.notify();}
 check(ticket=this.generation){need(this.active&&!this.suspended&&!this.denied&&ticket===this.generation&&this.currentAvailable(),'任务、账号或文件范围已变化，未采用迟到的内容。');try{need(this.token&&this.session.getItem('ga-setup-csrf')===this.token&&this.session.getItem('ga-setup-csrf-user')===this.scope.actor_id&&this.session.getItem('ga-setup-csrf-workspace')===this.scope.workspace_id);}catch(e){this.deny();throw e;}}
 async identity(ticket){this.check(ticket);try{const me=await this.json('/me',{method:'GET',signal:this.abort?.signal,cache:'no-store'});this.check(ticket);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||me?.role!==this.role){this.deny();throw Error('实际账号、工作区或权限已变化，请重新打开当前任务。');}}catch(e){if([401,403].includes(e.status)&&ticket===this.generation&&this.active&&!this.suspended)this.deny();throw e;}}
 async read(kind='markdown',mode='preview'){
  need(['markdown','machine_json'].includes(kind)&&['preview','download'].includes(mode)&&(mode!=='preview'||kind==='markdown'));this.check();this.abort?.abort();this.clearURLs();this.generation++;const ticket=this.generation;this.abort=new AbortController();this.busy=true;this.error='';this.notice='';this.preview=null;this.last={kind,mode};this.notify();
  const abort=this.abort;let response=null;
  try{await this.identity(ticket);const descriptor=this.value.artifacts[kind];response=await this.binary(technicalArtifactPathV220(this.scope.task_id,descriptor.evidence_id,mode==='download'),{method:'GET',signal:abort.signal,cache:'no-store'});this.check(ticket);await this.identity(ticket);const {bytes,type}=await readTechnicalArtifactV220(response,descriptor,kind,{signal:abort.signal,crypto:this.crypto});await this.identity(ticket);this.check(ticket);
   if(mode==='preview'){let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{throw Error('报告不是可核对的UTF-8文字，未作为网页内容展示。');}this.preview=technicalMarkdownV220(text);this.notice='原任务文件与完整字节摘要已核对；以下仅为内部技术汇总。';}
   else{const url=this.urlAPI.createObjectURL(new Blob([bytes],{type}));this.urls.add(url);this.download={url,name:kind==='markdown'?'任务内部技术汇总.md':'任务内部技术汇总.json',kind};this.notice='原任务文件与完整字节摘要已核对，可保存完整文件。';}return this.download||this.preview;
  }catch(e){abort.abort();try{await response?.body?.cancel();}catch{}if([401,403].includes(e.status)&&ticket===this.generation)this.deny();if(this.active&&!this.suspended&&!this.denied&&ticket===this.generation){this.error=e.message;this.preview=null;this.clearURLs();}throw e;}finally{if(this.active&&!this.suspended&&ticket===this.generation){this.busy=false;this.notify();}}
 }
 cancel(){this.check();this.generation++;this.abort?.abort();this.busy=false;this.preview=null;this.error='';this.clearURLs();this.notice='已停止本页文件读取；原技术汇总与任务没有改变。';this.notify();}
 close(){this.cancel();this.last=null;this.notice='';this.notify();}
 async retry(){need(this.last,'尚无可重试的文件读取。');return this.read(this.last.kind,this.last.mode);}
 dispose(){this.active=false;this.suspended=true;this.generation++;this.abort?.abort();this.clearURLs();this.onChange=()=>{};}
}
