import React,{useEffect,useMemo,useRef,useState} from 'react';
import {api,json} from './runtime-api';
import {TechnicalArchiveReaderV220,TECHNICAL_STATUS} from './technical-archive-result-v220.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';

function MarkdownBlocks({blocks}){return <>{blocks.map((block,i)=>{if(block.kind==='heading'){const Tag='h'+Math.min(6,block.level+3);return <Tag key={i}>{block.text}</Tag>;}if(block.kind==='list')return <ul key={i}>{block.items.map((text,j)=><li key={j}>{text}</li>)}</ul>;if(block.kind==='table')return <div key={i} style={{overflowX:'auto'}}><table><thead><tr>{block.headers.map((text,j)=><th key={j}>{text}</th>)}</tr></thead><tbody>{block.rows.map((row,j)=><tr key={j}>{row.map((text,k)=><td key={k}>{text}</td>)}</tr>)}</tbody></table></div>;return <p key={i} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{block.text}</p>;})}</>;}
export default function TechnicalArchiveResultV220({value,taskId,account,requestJSON=json,readBinary=api,tabStorage=globalThis.sessionStorage}){
 const [,draw]=useState(0),[advanced,setAdvanced]=useState(false),latest=useRef(null),lifetime=useRef(null);if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage?.getItem('ga-setup-csrf')||'';}catch{}
 const scopeKey=JSON.stringify([taskId,account?.user_id,account?.workspace_id,account?.role,token,value]);latest.current=scopeKey;
 const holder=useMemo(()=>{try{return {c:new TechnicalArchiveReaderV220({value,taskId,account,requestJSON,readBinary,session:tabStorage,currentAvailable:()=>latest.current===scopeKey}),error:''};}catch(e){return {c:null,error:e.message};}},[scopeKey,requestJSON,readBinary,tabStorage]);const c=holder.c;
useEffect(()=>{setAdvanced(false);if(!c)return;const lease=lifetime.current.setup(c,scopeKey);if(!lease)return;let mounted=true;c.onChange=()=>{if(mounted&&c.active&&!c.suspended)draw(n=>n+1);};const focus=()=>{const ticket=c.generation;c.identity(ticket).catch(e=>{if(c.active&&!c.suspended&&ticket===c.generation){c.error=e.message;c.notify();}});};globalThis.addEventListener?.('focus',focus);return()=>{mounted=false;globalThis.removeEventListener?.('focus',focus);lifetime.current.cleanup(lease);};},[c,scopeKey]);
 const act=async fn=>{try{await fn();}catch{}};
 const read=(kind,mode)=>{setAdvanced(false);return act(()=>c.read(kind,mode));};
 if(c?.denied)return <section className="media-form" aria-label="内部技术汇总"><h4>内部技术汇总</h4><p role="alert">账号、工作区或权限已变化，原文件内容与下载链接已隐藏，请重新打开当前任务。</p></section>;
return <section className="media-form" aria-label="内部技术汇总"><h4>内部技术汇总</h4><p>任务回执：{c?TECHNICAL_STATUS[c.value.technical_status]:'技术状态待核对'}。此步骤用于生成并保存本地技术文件，原件和原报告继续保留。</p><p>用于内部技术核查，不构成正式对外成果、生态或物种结论，也不代表数据管理已经独立收到。</p>
 {!c?<p role="alert">{holder.error}</p>:<>
 <p>文件需读取后核对。查看、下载和重试只读取这一次已保存的汇总，不会重新生成或调用模型。</p><div className="actions"><button type="button" disabled={c.busy} onClick={()=>read('markdown','preview')}>查看汇总报告</button><button type="button" disabled={c.busy} onClick={()=>read('markdown','download')}>下载报告</button><button type="button" disabled={c.busy} onClick={()=>read('machine_json','download')}>下载结构化数据</button></div>
 {c.busy&&<p role="status">正在核对原任务、文件完整性与读取权限… <button type="button" onClick={()=>{setAdvanced(false);act(()=>c.cancel());}}>取消文件读取</button></p>}{c.error&&<div role="alert"><p>{c.error}</p>{c.last&&<button type="button" disabled={c.busy} onClick={()=>{setAdvanced(false);act(()=>c.retry());}}>重试原文件读取</button>}</div>}{c.notice&&<p role="status">{c.notice}</p>}
 {c.preview&&<section aria-label="内部技术汇总报告正文"><MarkdownBlocks blocks={c.preview.body}/>{c.preview.advanced.length>0&&<details onToggle={event=>setAdvanced(event.currentTarget.open)}><summary>高级校验信息</summary>{advanced&&<MarkdownBlocks blocks={c.preview.advanced}/>}</details>}</section>}
 {c.download&&<p><a className="btn" href={c.download.url} download={c.download.name}>保存已核对的{c.download.kind==='markdown'?'报告':'结构化数据'}</a></p>}
 {(c.preview||c.download||c.error||c.notice)&&<button type="button" disabled={c.busy} onClick={()=>{setAdvanced(false);act(()=>c.close());}}>关闭汇总文件</button>}
 </>}
 </section>;
}
