import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {flowFor, flowSignature} from './setup-schema';
import {sameSignature} from './content-signature';
import {flowSourceIds} from './flow-sources.mjs';

function token() { try { return globalThis.sessionStorage?.getItem('ga-setup-csrf') || ''; } catch { return ''; } }
function signature(workspace, flow) { try { return flowSignature(workspace, flow); } catch { return ''; } }
function sourcesCurrent(workspace,flow,canonicalMaterials,readStatus){
 const ids=new Set(flowSourceIds(flow));
 for(const item of [flow,...(flow?.steps||[])]){const pending=item?.candidateUnresolvedSources||[];if(!Array.isArray(pending))return false;
  for(const source of pending){if(typeof source?.material_id!=='string'||!source.material_id)return false;ids.add(source.material_id);}}
 if(!ids.size)return true;
 if(readStatus!=='ready'||!Array.isArray(canonicalMaterials))return false;
 for(const id of ids){const saved=workspace?.materials?.find(x=>x.id===id),current=canonicalMaterials.find(x=>x.id===id);
  if(!saved||!current||current.archived||saved.archived||saved.hash!==current.hash||saved.version!==current.version)return false;}
 return true;
}

// This confirms only the user's draft content.  It never confirms evidence,
// creates a business instance, or authorizes execution.
export default function DraftContentReview({account, business, flow, workspace, liveWorkspace,canonicalMaterials,materialStatusRead,
 canManage, prepareDraft, onAdopt, Btn, request=json}) {
 const scope=JSON.stringify([account?.user_id||'', account?.username||'', account?.workspace_id||'', account?.role||'', token()]);
 const identity=useRef({scope,epoch:0});
 if(identity.current.scope!==scope)identity.current={scope,epoch:identity.current.epoch+1};
 const epoch=identity.current.epoch, mounted=useRef(false), generation=useRef(0), abort=useRef(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const current=signature(workspace,flow),saved=flow?.candidateContentReview;
 const basisCurrent=sourcesCurrent(workspace,flow,canonicalMaterials,materialStatusRead);
 const confirmed=!!saved&&saved.business===business&&!!current&&basisCurrent&&sameSignature(saved.signature,current);
 useEffect(()=>{generation.current++;abort.current?.abort();setBusy(false);setError('');setNotice('');},[scope,business]);
 useEffect(()=>{mounted.current=true;const check=()=>{if(token()!==JSON.parse(identity.current.scope)[4]){generation.current++;abort.current?.abort();setBusy(false);setError('登录状态已变化，请刷新后继续。');}};window.addEventListener('focus',check);return()=>{mounted.current=false;generation.current++;abort.current?.abort();window.removeEventListener('focus',check);};},[]);
 if(!flow?.candidateKey)return null;
 async function act(action) {
  if(busy||!canManage||!prepareDraft||!onAdopt||!business||!current||!basisCurrent)return;
  const serial=++generation.current,auth=token(),control=new AbortController();abort.current=control;
  const valid=()=>mounted.current&&generation.current===serial&&identity.current.epoch===epoch&&identity.current.scope===scope&&token()===auth;
  setBusy(true);setError('');setNotice('');let sent=false;
  try {
   const revision=await prepareDraft();if(!valid())return;
   const latest=liveWorkspace?.()||workspace;
   if(signature(latest,flowFor(latest,business))!==current)throw Error('业务内容已变化，请重新核对。');
   sent=true;
   const result=await request('/setup/candidates/content-review',{method:'POST',body:JSON.stringify({revision,business,action}),signal:control.signal});
   if(!valid())return;
   if(!result?.draft||!Number.isInteger(result.draft.revision)||!result.draft.configuration)throw Error('确认结果暂未核对，请刷新查看。');
   const still=liveWorkspace?.()||workspace;
   if(signature(still,flowFor(still,business))!==current)throw Error('草案内容已变化，请先保存并刷新查看确认状态。');
   await onAdopt(result.draft,{actor_id:account.user_id,workspace_id:account.workspace_id});
   if(valid())setNotice(action==='confirm'?'草案内容已确认。':'已取消草案内容确认。');
  } catch(e) {
   if(valid())setError(e?.status===409?'草案已变化，请刷新后重新核对。':sent?'确认结果暂未核对，请刷新查看。':(e?.message||'暂时无法确认，请重试。'));
  } finally {if(valid()){setBusy(false);abort.current=null;}}
 }
 return <div className="draft-content-review" aria-label="业务草案内容确认">
  <span role="status">{confirmed?'草案内容已确认':'草案内容待确认'}</span>{' '}
  {canManage&&<Btn disabled={busy||!current||!basisCurrent} onClick={()=>act(confirmed?'clear':'confirm')}>{busy?'正在保存…':confirmed?'取消确认':'确认草案内容'}</Btn>}
  {!basisCurrent&&<p role="status">关联资料已变化，请刷新资料后重新核对。</p>}
  <small>正式生成或启用仍需核对资料依据。</small>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </div>;
}
