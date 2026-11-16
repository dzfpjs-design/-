import {decodeConfigurationReply,configurationWire,savedConfigurationWire} from './configuration-numeric-wire.mjs';
import {stageOwnedDraft,claimSharedDraft,acknowledgeOwnedDraft,withDraftCacheLock,readDraftRecord,draftOutboxKey} from './draft-cache-contract.mjs';
// Storage may be blocked; boot reports the failure before any authenticated work.
let csrf = '';
try { csrf = sessionStorage.getItem('ga-setup-csrf') || ''; } catch {}
let workspace = '';
export function setSession(token,id,userID,username){sessionStorage.setItem('ga-setup-csrf',token);if(id)sessionStorage.setItem('ga-setup-csrf-workspace',id);else sessionStorage.removeItem('ga-setup-csrf-workspace');if(userID)sessionStorage.setItem('ga-setup-csrf-user',userID);else sessionStorage.removeItem('ga-setup-csrf-user');if(username)sessionStorage.setItem('ga-setup-csrf-username',username);else sessionStorage.removeItem('ga-setup-csrf-username');csrf=token;workspace=id;}
export function clearSession() { csrf = ''; workspace = ''; sessionStorage.removeItem('ga-setup-csrf');sessionStorage.removeItem('ga-setup-csrf-workspace');sessionStorage.removeItem('ga-setup-csrf-user');sessionStorage.removeItem('ga-setup-csrf-username'); }
export async function api(path, options = {}) {
  const headers = { ...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}),
    ...(csrf ? {'X-CSRF-Token':csrf} : {}), ...(workspace ? {'X-Workspace-ID':workspace} : {}), ...options.headers };
  let res;
  try { res = await fetch('/api' + path, {...options, headers, credentials:'same-origin'}); }
  catch (cause) { const err = Error(cause?.name === 'AbortError' ? '已停止等待，原请求与已保存记录保留。' : '暂时无法连接服务器，请核对连接后重试。'); err.cause = cause; if (cause?.name === 'AbortError') err.name = 'AbortError'; throw err; }
  if (!res.ok) { let detail; try { detail = (await res.json()).detail; } catch {}
    const err = Error(typeof detail === 'string' ? detail : '操作未完成，请核对输入后重试。'); err.status = res.status; err.detail = detail; throw err; }
  return res;
}
export async function json(path, options = {}) { return (await api(path, options)).json(); }
export async function readRuntimeDraft(options={}) {return decodeConfigurationReply(await (await api('/setup/draft',options)).text());}
export function hashBytes(bytes) {return crypto.subtle.digest('SHA-256',bytes).then(b=>Array.from(new Uint8Array(b)).map(n=>n.toString(16).padStart(2,'0')).join(''));}
export async function uploadRemote(file) {
  const form = new FormData(); form.append('file',file);
  const row = await json('/setup/materials',{method:'POST',body:form});
  if (row.hash !== await hashBytes(await file.arrayBuffer())) throw Error('服务器原件摘要不匹配，请保留本地原件并重试。');
  const saved = await api(`/setup/materials/${row.id}/content`);
  if (await hashBytes(await saved.arrayBuffer()) !== row.hash) throw Error('原件读回检查失败，请保留本地原件。');
  return row;
}
export async function readRemote(id) {
  const m = await json('/setup/materials/'+id);
  const text = (m.blocks||[]).map(b=>'['+Object.entries(b.location).map(([k,v])=>({'body_index':'正文位置','row':'行','line':'行','sheet':'工作表','page':'页'}[k]||k)+' '+v).join(' · ')+']\n'+b.text).join('\n\n');
  return {...m, text:text||null, reason:m.error||(m.warnings||[]).join('；')||'原件已保存，解析尚未结束。'};
}
export async function remoteURL(id) {
  const m = await json('/setup/materials/'+id), res = await api('/setup/materials/'+id+'/content'), blob = await res.blob();
  if (await hashBytes(await blob.arrayBuffer()) !== m.hash) throw Error('原件读回摘要不匹配，请重试。');
  return {name:m.name,url:URL.createObjectURL(blob)};
}

// Serial CAS writes; every unsent value is journaled before network activity.
export class DraftSync {
  constructor(storage,key,revision,onStatus,options={}) { Object.assign(this,{storage,key,revision,onStatus});this.tabStorage=options.tabStorage||globalThis.sessionStorage;this.locks=options.locks||globalThis.navigator?.locks;this.owner=crypto.randomUUID();this.generation=0;this.next=null;this.busy=false;this.error=null;this.configurationWire=null;this.configurationMetadataError=null;try{this.configurationWire=Object.hasOwn(options,'configurationWire')?options.configurationWire:readDraftRecord(storage,key)?.configuration_wire??null;}catch(error){this.configurationMetadataError=error;} }
  journal(value,generation=this.generation) {const wire=this.configurationWire===null?null:configurationWire(value,this.configurationWire);return stageOwnedDraft(this.storage,this.tabStorage,this.key,{revision:this.revision,pending:true,configuration:value,draft_owner:this.owner,draft_generation:generation,...(wire!==null?{configuration_wire:wire}:{})});}
  stage(value) {
    const generation=this.generation+1,row=this.journal(value,generation);this.generation=generation;this.next={value,generation,configurationWire:row.configuration_wire??null};
    this.onStatus({state:'saving',text:'本页人稿已保存，正在提交服务器…'});
  }
  save(value) {this.stage(value);this.flush();
  }
  async flush() {
    if(this.suspended||this.busy||this.error||!this.next)return;this.busy=true;
    try {
      while(this.next&&!this.suspended) {
        const sent=this.next;this.next=null;
        try {
          if(this.configurationMetadataError)throw this.configurationMetadataError;
          await withDraftCacheLock(this.key,()=>{const current=readDraftRecord(this.storage,draftOutboxKey(this.key,this.owner));if(!current||current.draft_generation<sent.generation||!current.pending||current.recovery_choice)throw Error('本页人稿尚未保存或已被明确处理，未向服务器提交。');claimSharedDraft(this.storage,this.key,current);},this.locks);
          if(this.suspended)throw Error('会话已变化，原人稿保留。');
          const typed=typeof sent.configurationWire==='string',options={method:'PUT',body:typed?'{\"revision\":'+this.revision+',\"configuration\":'+sent.configurationWire+'}':JSON.stringify({revision:this.revision,configuration:sent.value})};
          const r=typed?await readRuntimeDraft(options):await json('/setup/draft',options);
          this.revision=r.revision;if(typed)this.configurationWire=savedConfigurationWire(r);
          await withDraftCacheLock(this.key,()=>acknowledgeOwnedDraft(this.storage,this.tabStorage,this.key,this.owner,sent.generation,r.revision,typed?r:null),this.locks);
        } catch(e) {
          this.next=this.next||sent;
          try{this.journal(this.next.value,this.next.generation);await withDraftCacheLock(this.key,()=>claimSharedDraft(this.storage,this.key,readDraftRecord(this.storage,draftOutboxKey(this.key,this.owner))),this.locks);}catch(cacheError){if(cacheError.code!=='DRAFT_CACHE_CONFLICT')e=Error(e.message+' '+cacheError.message);}
          throw e;
        }
      }
      this.onStatus({state:'saved',text:'已保存到服务器，刷新或重启后可恢复。'});
    } catch(e) {this.error=e;this.onStatus({state:'error',text:e.status===409?'其他页面已更新配置或存在未提交稿。本页人稿已独立保留，请处理版本冲突。':e.message+' 本页人稿保留，可重试保存。'});}
    finally {this.busy=false;}
  }
  async wait() {while(this.busy)await new Promise(resolve=>setTimeout(resolve,50));if(this.error||this.next)throw this.error||Error('配置尚未保存，请重试。');}
  suspend() {this.suspended=true;this.error=Error('会话需要重新登录；尚未送达的人稿保留。');this.onStatus=()=>{};}
  retry() {if(this.suspended)return;this.error=null;this.flush();}
}

export async function readOCRImage({materialID,unitID,runID,confirmationID,imageSHA},signal){
 if(!materialID||!unitID||!(/^[0-9a-f]{64}$/.test(imageSHA||''))||(!runID&&!confirmationID))throw Error('原页来源与摘要尚未确定，暂不能读取。');
 const base='/setup/materials/'+encodeURIComponent(materialID)+'/ocr';
 const path=confirmationID?base+'/confirmations/'+encodeURIComponent(confirmationID)+'/units/'+encodeURIComponent(unitID)+'/image?image_sha256='+imageSHA:base+'/units/'+encodeURIComponent(unitID)+'/image?run_id='+encodeURIComponent(runID)+'&image_sha256='+imageSHA;
 const response=await api(path,{signal});const bytes=await response.arrayBuffer();
 if(await hashBytes(bytes)!==imageSHA)throw Error('原页图像摘要不一致，未作为核查依据。');
 if(!(response.headers.get('Content-Type')||'').startsWith('image/png'))throw Error('原页图像格式无法核对，未显示替代内容。');
 return new Blob([bytes],{type:'image/png'});
}
