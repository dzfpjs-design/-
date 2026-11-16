import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';

export default function PlanningToolConfig({node,onChange,onBindingReviewed,onRemoveIncompatibleBinding,Btn}){
 const [options,setOptions]=useState([]),[loading,setLoading]=useState(true),[more,setMore]=useState(false),[search,setSearch]=useState(''),[error,setError]=useState('');
 const selectionKey=(node.planning_tools||[]).map(x=>x.capability_id).join(',');
 useEffect(()=>{const controller=new AbortController();let alive=true;const params=new URLSearchParams(selectionKey?selectionKey.split(',').map(id=>['selected',id]):[]);if(search.trim())params.set('q',search.trim());const timer=setTimeout(()=>{setLoading(true);setError('');json('/workspace/planning-tool-options?'+params.toString(),{signal:controller.signal}).then(value=>{if(alive){setOptions(value.items);setMore(value.has_more);}}).catch(e=>{if(alive)setError(e.message);}).finally(()=>{if(alive)setLoading(false);});},250);return()=>{alive=false;clearTimeout(timer);controller.abort();};},[selectionKey,search]);
 const selected=node.planning_tools||[];
 const needsReview=Object.hasOwn(node,'bindingReview'),reviewReason=typeof node.bindingReview?.reason==='string'?node.bindingReview.reason:'步骤职责或行为已变化，保留的规划工具需要重新核对。';
 const incompatibleExecution=!!node.execution&&Object.keys(node.execution).length>0;
 const reviewReady=!incompatibleExecution&&!loading&&!error&&selected.every(grant=>{const tool=options.find(x=>x.id===grant.capability_id);return tool&&(!tool.external||grant.allow_external===true);});
 function select(id,enabled){onChange(enabled?[...selected,{capability_id:id,allow_external:false}]:selected.filter(x=>x.capability_id!==id));}
 function external(id,enabled){onChange(selected.map(x=>x.capability_id===id?{...x,allow_external:enabled}:x));}
 return <section className="media-form"><h3>智能计划可使用的能力</h3><p>为这个人工步骤指定可供智能体选择的只读工具。默认不授予工具；生成版本后固定范围。生成的计划需逐次核对具体输入与目的地并批准执行；实际结果还需核对采用，不自动推进原人工流程。</p>
 {needsReview&&<section aria-label="规划工具待重新核对"><p role="alert">{reviewReason}</p><p>保留的规划授权：</p>{selected.length?<ul>{selected.map(grant=>{const tool=options.find(x=>x.id===grant.capability_id);return <li key={grant.capability_id}>{tool?.name||'原登记能力暂时无法核对'}{tool?' · '+tool.version:''} · {grant.allow_external===true?'原选择允许外发':'未允许外发'}</li>;})}</ul>:<p>当前未授予规划工具。</p>}<p>核对只更新当前草稿标记；保存并重新生成时仍需验证已发布能力及当前负责人权限，不会自动调用工具。</p><Btn disabled={!reviewReady||!onBindingReviewed} onClick={()=>{if(reviewReady)onBindingReviewed?.(node);}}>已核对此步骤的规划工具</Btn></section>}
 {incompatibleExecution&&<section aria-label="保留的自动执行绑定"><p role="alert">此步骤还保留原自动执行绑定，当前人工步骤不会执行它。请明确移除此绑定，再核对人工规划授权。</p><p>保留的能力：{options.find(tool=>tool.id===node.execution.capability_id)?.name||'原登记执行能力'}；原参数与输入来源仍保留在草稿中。</p><Btn disabled={!onRemoveIncompatibleBinding} onClick={()=>onRemoveIncompatibleBinding?.(node,'execution')}>移除此人工步骤的原执行绑定</Btn><p>原版本仍保留；移除后仍需明确核对当前绑定并保存草稿。</p></section>}
 <label>查找已注册工具<input aria-label="查找规划工具" maxLength={100} value={search} onChange={e=>setSearch(e.target.value)}/></label>
 {error&&<p role="alert">无法读取能力：{error}。原选择保留，请稍后重新打开此步骤。</p>}
 {more&&<p>目前显示最近200项符合条件的工具及已选择项。请输入更具体的名称查找其他工具。</p>}{loading?<p>正在读取已发布工具…</p>:<>{!options.length&&<p>暂无符合要求的只读工具。仍可生成资料核对及报告计划。</p>}{options.map(tool=>{const grant=selected.find(x=>x.capability_id===tool.id);return <article key={tool.id}><label className="checkbox"><input type="checkbox" aria-label={'允许智能计划使用 '+tool.name} checked={!!grant} onChange={e=>select(tool.id,e.target.checked)}/>{tool.name} · {tool.version} · {tool.external?'登记外部服务':'本地'}</label>{grant&&tool.external&&<><p>登记目的地：{tool.destination}</p><label className="checkbox"><input type="checkbox" aria-label={'允许规划工具向外发送 '+tool.name} checked={grant.allow_external===true} onChange={e=>external(tool.id,e.target.checked)}/>允许该工具发送其契约中绑定的业务输入；具体计划仍需单独核对。</label></>}</article>;})}</>}
 {!loading&&!error&&selected.filter(x=>!options.some(o=>o.id===x.capability_id)).map(x=><p role="alert" key={x.capability_id}>一项原选择的能力当前不可用。生成版本前需要核对。<Btn onClick={()=>select(x.capability_id,false)}>移除此项授权</Btn></p>)}
 </section>;
}
