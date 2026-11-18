import React,{useEffect,useRef,useState} from 'react';
import {digest,fullInt,fullKeys,fullSha,fullUUID} from './report-full-reading-contract.mjs';

const check=(condition)=>{if(!condition)throw Error('原件字符对照无法核对，请刷新后重试。');};
export async function verifyDocxVisibleComparison(value,account,material,row){
 check(fullKeys(value,'schema,actor_id,workspace_id,material,legacy_reading_hash,visible_reading_hash,visible_text_policy,paragraph_count,changes,legacy_preserved,saved_new_view,can_apply,can_send,comparison_hash'));
 check(value.schema==='docx-visible-text-comparison-v2'&&value.actor_id===account.user_id&&value.workspace_id===account.workspace_id);
 check(fullKeys(value.material,'id,version,hash')&&value.material.id===material.id&&value.material.version===material.version&&value.material.hash===material.hash);
 check(value.visible_text_policy==='preserve-ooxml-run-hyphens-v2'&&fullSha.test(value.visible_reading_hash||'')&&(value.legacy_reading_hash===null||fullSha.test(value.legacy_reading_hash||''))&&fullInt(value.paragraph_count,0)&&Array.isArray(value.changes)&&value.changes.length<=500);
 check(value.legacy_preserved===true&&value.saved_new_view===false&&value.can_apply===false&&value.can_send===false&&fullSha.test(value.comparison_hash||''));
 const reading=row?.preview?.source_snapshot?.original?.reading,ref=row?.preview?.source_snapshot?.original?.ref;
 check(reading&&ref&&ref.id===material.id&&ref.version===material.version&&ref.hash===material.hash&&value.legacy_reading_hash===await digest(reading));
 let previous=0;
 for(const x of value.changes){check(fullKeys(x,'paragraph_index,body_index,legacy_id,visible_id,before,after,nonbreaking_hyphens,optional_hyphens')&&fullInt(x.paragraph_index,1)&&x.paragraph_index>previous&&fullInt(x.body_index,1)&&typeof x.before==='string'&&typeof x.after==='string'&&fullInt(x.nonbreaking_hyphens,0)&&fullInt(x.optional_hyphens,0)&&(x.legacy_id===null||/^p[1-9][0-9]*$/.test(x.legacy_id))&&/^v2:p[1-9][0-9]*$/.test(x.visible_id||''));previous=x.paragraph_index;}
 const {comparison_hash,...rest}=value;check(await digest(rest)===comparison_hash);return value;
}
const visibleText=value=>value.replaceAll('\u00ad','［可选连字符］');
export default function DocxVisibleTextComparison({account,material,row,request,identity,Btn='button',onSessionInvalid}){
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,material?.id,material?.version,material?.hash,row?.id,row?.preview?.source_hash]);
 const current=useRef({scope,serial:0,controller:null});if(current.current.scope!==scope){current.current.scope=scope;current.current.serial++;}
 const [state,setState]=useState(null),[index,setIndex]=useState(0),shown=state?.scope===scope?state:null;
 useEffect(()=>()=>{current.current.serial++;current.current.controller?.abort();},[scope]);
 async function read(){const serial=++current.current.serial,controller=new AbortController();current.current.controller?.abort();current.current.controller=controller;const active=()=>current.current.scope===scope&&current.current.serial===serial&&!controller.signal.aborted;setState({scope,phase:'loading'});
  try{check(fullUUID.test(material.id)&&fullInt(material.version,1)&&fullSha.test(material.hash||''));const q=new URLSearchParams({version:String(material.version),source_hash:material.hash});const value=await request('/setup/materials/'+encodeURIComponent(material.id)+'/docx-visible-text-v2?'+q,{signal:controller.signal,cache:'no-store'});await verifyDocxVisibleComparison(value,account,material,row);await identity();if(active()){setIndex(0);setState({scope,phase:'ready',value});}}
  catch(error){if(!active())return;if([401,403].includes(error?.status)||error?.code==='SESSION_SCOPE_CHANGED'||/页面会话已变化/.test(error?.message||''))onSessionInvalid?.();setState({scope,phase:'error',message:[401,403].includes(error?.status)?'账号权限已变化，报告内容已隐藏。':'原件或字符对照暂时无法核对，请刷新后重试。'});}
  finally{if(current.current.controller===controller)current.current.controller=null;}}
 const value=shown?.value,change=value?.changes[index];
 return <section aria-label="Word原文字符核对"><h4>核对 Word 原文中的特殊连字符</h4><p>从已保存的 Word 原件重新核对；旧报告读取结果不会改变，本操作不调用模型。新对照每次从原件重新计算，仅供查看，尚未形成可发送的新存档，尚不能用于本次分析。</p><Btn disabled={shown?.phase==='loading'} onClick={read}>{shown?.phase==='loading'?'正在核对原件…':'查看原文字符对照'}</Btn>
 {shown?.phase==='error'&&<p role="alert">{shown.message}</p>}
 {value&&<div role="status"><p>{value.changes.length?'发现 '+value.changes.length+' 处原文字的特殊连字符，请结合原件核对其含义。':'未发现此类特殊连字符。'}</p><p>“非断行连字符”可能是负号或词内连字符；“可选连字符”是否显示取决于原文排版。此处不自动判断数值。</p>{change&&<div><p>第 {index+1} / {value.changes.length} 处 · 按读取顺序为正文第 {change.paragraph_index} 段，非页码</p><p>旧版文字</p><blockquote style={{whiteSpace:'pre-wrap'}}>{visibleText(change.before)||'（旧版此处为空）'}</blockquote><p>原件保留特殊字符后的文字</p><blockquote style={{whiteSpace:'pre-wrap'}}>{visibleText(change.after)}</blockquote><Btn disabled={index===0} onClick={()=>setIndex(index-1)}>上一处</Btn><Btn disabled={index+1>=value.changes.length} onClick={()=>setIndex(index+1)}>下一处</Btn></div>}</div>}</section>;
}
