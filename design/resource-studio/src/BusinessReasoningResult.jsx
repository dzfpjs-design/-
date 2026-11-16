import React,{useEffect,useRef,useState} from 'react';
import {api} from './runtime-api.js';
import {businessReasoningResultView,businessEvidencePath,evidenceExtension,isBusinessReasoningResult,readBusinessEvidencePreview} from './business-reasoning-result.mjs';

function References({value}){return <p>引用：{value.items.length?value.items.map(item=>item.label).join('、'):'未提供可核对的资料引用'}{value.unavailable&&'；部分引用的资料当前无法核对'}</p>;}

function EvidenceFile({item}){
 const [state,setState]=useState({status:'idle'}),life=useRef(null);
 useEffect(()=>{const current={active:true,generation:0,controller:null,urls:new Set(),timers:new Set()};life.current=current;setState({status:'idle'});return()=>{current.active=false;current.generation++;current.controller?.abort();for(const timer of current.timers)clearTimeout(timer);for(const url of current.urls)URL.revokeObjectURL(url);current.urls.clear();};},[item.id]);
 async function load(mode){
  const current=life.current;if(!current?.active)return;
  current.controller?.abort();const controller=new AbortController();current.controller=controller;const generation=++current.generation;
  const live=()=>current.active&&generation===current.generation;
  const revoke=url=>{if(current.urls.delete(url))URL.revokeObjectURL(url);};
  for(const url of [...current.urls])revoke(url);
  setState({status:'loading',mode});
  try{
   const response=await api(businessEvidencePath(item.id),{signal:controller.signal});
   if(!live()){try{await response.body?.cancel();}catch{}return;}
   if(mode==='download'){
    const blob=await response.blob();if(!live())return;
    const url=URL.createObjectURL(blob);current.urls.add(url);
    const anchor=document.createElement('a');anchor.href=url;anchor.download=item.label+evidenceExtension(blob.type);document.body.appendChild(anchor);
    try{anchor.click();}finally{anchor.remove();const timer=setTimeout(()=>{current.timers.delete(timer);revoke(url);},1000);current.timers.add(timer);}
    if(live())setState({status:'download',message:'下载已交给浏览器，请确认文件保存。'});
   }else{
    const preview=await readBusinessEvidencePreview(response);if(!live())return;
    if(preview.kind==='image'){const url=URL.createObjectURL(preview.blob);current.urls.add(url);setState({status:'ready',kind:'image',url});}
    else setState({status:'ready',...preview});
   }
  }catch(error){if(!live()||error?.name==='AbortError')return;setState({status:'error',mode,message:error?.status===403?'当前账号无权读取这份资料。':error?.status===404?'这份资料当前无法读取，请核对原记录后重试。':'资料读取失败，请重试。'});}
 }
 const busy=state.status==='loading';
 return <article className="media-form" aria-label={item.label+'原件'}><h5>{item.label}</h5><div className="row"><button type="button" disabled={busy} onClick={()=>load('view')}>查看{item.label}</button><button type="button" disabled={busy} onClick={()=>load('download')}>下载{item.label}</button></div>
  {busy&&<p role="status">正在读取{item.label}…</p>}{state.status==='error'&&<><p role="alert">{state.message}</p><button type="button" onClick={()=>load(state.mode)}>重试{state.mode==='download'?'下载':'查看'}{item.label}</button></>}
  {state.status==='download'&&<p role="status">{state.message}</p>}{state.status==='ready'&&state.kind==='download'&&<p role="status">{state.reason}</p>}
  {state.status==='ready'&&state.kind==='text'&&<pre aria-label={item.label+'原文'} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'24rem',overflow:'auto'}}>{state.text}</pre>}
  {state.status==='ready'&&state.kind==='image'&&<img src={state.url} alt={item.label+'已保存原件'} onError={()=>{const current=life.current;if(current?.active&&current.urls.has(state.url)){current.urls.delete(state.url);URL.revokeObjectURL(state.url);setState({status:'error',mode:'view',message:'图片原件未能显示，可以重试或下载原件。'});}}} style={{maxWidth:'100%',maxHeight:'32rem',objectFit:'contain'}}/>}
 </article>;
}

export default function BusinessReasoningResult({value,inputs}){
 if(!isBusinessReasoningResult(value))return <p role="alert">业务分析结果暂不能完整核对，请重新读取任务记录。</p>;
 const view=businessReasoningResultView(value,inputs);
 return <section className="media-form" aria-label="业务分析结果"><h4>业务分析</h4><p role="note">模型建议不等于业务批准；设备动作仍须按原流程授权执行。</p>
  <h5>业务问题</h5><p style={{whiteSpace:'pre-wrap'}}>{view.question||'原业务问题未随回执提供，请核对任务记录。'}</p>
  <p role="status"><strong>{view.state}</strong></p><h5>分析摘要</h5><p style={{whiteSpace:'pre-wrap'}}>{view.summary}</p>
  <h5>逐项方案核对</h5>{view.rows.map((row,index)=><article className="media-form" key={index}><h6>{row.label} · {row.assessment}</h6>{row.summary&&<p style={{whiteSpace:'pre-wrap'}}>{row.summary}</p>}<p style={{whiteSpace:'pre-wrap'}}>{row.reason}</p><References value={row.citations}/>{row.missing.length>0&&<><strong>还需补充</strong><ul>{row.missing.map((item,i)=><li key={i} style={{whiteSpace:'pre-wrap'}}>{item}</li>)}</ul></>}</article>)}
  <h5>推荐说明</h5><p><strong>{view.recommendation.label}</strong></p><p style={{whiteSpace:'pre-wrap'}}>{view.recommendation.reason}</p><References value={view.recommendation.citations}/>
  <h5>本次资料</h5>{view.evidence.length?view.evidence.map(item=><EvidenceFile key={item.id} item={item}/>):<p>本次资料清单暂无法核对，请重新读取原任务记录。</p>}
 </section>;
}
