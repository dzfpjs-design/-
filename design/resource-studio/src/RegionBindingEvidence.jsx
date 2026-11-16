import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {MaterialOCRImage} from './MaterialOCRReview';
import {verifyRegionEvidence} from './quote-binding-contract.mjs';
import {uuid} from './report-draft-contract.mjs';

function EvidencePage({view,entryKey,check,Btn}){
 const [selected,setSelected]=useState(view.binding.spans[0].region_id),[verified,setVerified]=useState(false),ready=useRef(null);
 if(!ready.current)ready.current=image=>{try{check();const unit=view.unit;setVerified(!!image&&image.materialID===view.binding.copy_material_id&&image.unitID===unit.id&&image.runID===view.run_id&&image.sha256===unit.image_sha256&&image.width===unit.width&&image.height===unit.height);}catch{setVerified(false);}};
 return <section><p>已保存的完整引文：</p><blockquote>{view.binding.reference.quote}</blockquote><p>{view.material_name} · PDF第{view.unit.page}页；这是正式复核时冻结的文字版本，后续编辑不会替换这处证据。</p><MaterialOCRImage key={entryKey} materialID={view.binding.copy_material_id} runID={view.run_id} confirmationID={view.confirmation_id} unit={view.unit} regions={view.regions.map(r=>({...r,box:r.box.map(Number)}))} selected={selected} onSelect={setSelected} onReady={ready.current} draw={false} Btn={Btn}/>{!verified&&<p role="status">原图尚未读回核对，不作为当前已看到的图像依据。</p>}<ol>{view.binding.spans.map(s=><li key={s.region_id}><Btn onClick={()=>setSelected(s.region_id)}>定位此段原图</Btn>第{view.regions.findIndex(r=>r.id===s.region_id)+1}处文字，取第{s.start+1}至{s.end}字（按Unicode字符计数）。</li>)}</ol></section>;
}

export default function RegionBindingEvidence({account,marker,citation,Btn,request=json,tabStorage=globalThis.sessionStorage}){
 const target=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,marker,citation]),latest=useRef(target);latest.current=target;
 const context=useRef(null),reads=useRef(0),[owner,setOwner]=useState(target),[view,setView]=useState(null),[loaded,setLoaded]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(context.current?.target!==target)context.current={target,token:tabStorage?.getItem('ga-setup-csrf')||'',send:account?scopedPreparationRequester(account,request,tabStorage):null};
 function snapshot(){const original=target,{send,token}=context.current;return {send,check(){if(original!==latest.current||!send||!token||tabStorage?.getItem('ga-setup-csrf')!==token||tabStorage?.getItem('ga-setup-csrf-user')!==account.user_id||tabStorage?.getItem('ga-setup-csrf-workspace')!==account.workspace_id)throw Error('账号或引用已变化，未显示原页面证据。');}};}
 async function load(){const snap=snapshot(),sequence=++reads.current;setBusy(true);setError('');setView(null);setLoaded('');try{if(!uuid.test(marker?.handoff_id)||!uuid.test(marker?.source_review_id)||!/^[a-f0-9]{64}$/.test(citation?.span_binding_id||''))throw Error('此引用显式标记了跨区域来源，但证明标识不完整；不会改用旧单区域引用。');const me=await snap.send('/me');snap.check();if(me.user_id!==account.user_id||me.workspace_id!==account.workspace_id||me.role!==account.role)throw Error('当前会话身份已变化，未显示旧账号证据。');const value=await snap.send('/setup/report-handoffs/'+marker.handoff_id+'/source-reviews/'+marker.source_review_id+'/region-bindings/'+citation.span_binding_id);snap.check();await verifyRegionEvidence(value,account,marker,citation);snap.check();if(sequence!==reads.current)return;setView(value);setLoaded(target);}catch(e){try{snap.check();if(sequence===reads.current)setError(e.detail?.message||e.message);}catch{}}finally{try{snap.check();if(sequence===reads.current)setBusy(false);}catch{}}}
 useEffect(()=>{reads.current++;setOwner(target);setView(null);setLoaded('');setBusy(false);setError('');return()=>{reads.current++;};},[target]);
 if(owner!==target)return <p>正在核对当前账号与引文范围…</p>;
 return <details><summary>跨区域引文与冻结原图</summary><Btn disabled={busy} onClick={load}>{loaded===target?'重新读取这处正式引文证据':'读取这处跨区域引文证据'}</Btn>{error&&<p role="alert">{error}</p>}{loaded===target&&view&&<EvidencePage key={target+':'+view.view_hash} view={view} entryKey={target} check={snapshot().check} Btn={Btn}/>}</details>;
}
