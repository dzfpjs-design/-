import React,{useState} from 'react';
import {json} from './runtime-api';
import {businessNames} from './business-schema';
import {flowFor} from './setup-schema';

function Summary({value}){return <><p>实例：{value.instanceProfile?.name||'未填写'}</p><p>领域：{value.instanceProfile?.domain||'未填写'} · 目标：{value.instanceProfile?.goal||'未填写'}</p><p>资料：{value.materials?.map(m=>`${m.name}${m.archived?'（归档）':''}`).join('、')||'无'}</p>{businessNames({...value,businesses:value.businesses||[]}).map(name=>{
 const flow=flowFor(value,name),label=id=>id==='end'?'结束流程':id==='auto'||!id?'顺序进入下一步':flow.steps.find(s=>s.id===id)?.name||'去向待核对';
 return <section key={name}><strong>{name}</strong><p>启动：{flow.trigger||'未填写'} · 产出：{flow.output||'未填写'}</p>{flow.steps.map((s,i)=><article className="history-step" key={s.id||i}><strong>{i+1}. {s.name}</strong><p>目的：{s.purpose||'未填写'}</p><p>{s.agent} {s.role&&' / '+s.role} · 输出：{s.output}</p><p>下一步：{label(s.nextId)}</p>{s.condition&&<p>条件：{s.condition}；未满足时：{label(s.elseId)}</p>}<small className="history-citation">{s.citation}</small></article>)}</section>;
 })}</>;}


export default function ConfigHistoryDialog({Dialog,Btn,rows:initial,onClose,onRestored,prepareDraft,workspace}){
 const [rows,setRows]=useState(initial),[more,setMore]=useState(initial.length===30),[plan,setPlan]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function run(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
 function loadMore(){run(async()=>{const cutoff=Math.min(...rows.map(r=>r.revision));const next=await json('/setup/draft/history?before_revision='+cutoff);setRows(old=>[...old,...next]);setMore(next.length===30);});}
 function inspect(row){run(async()=>{const revision=await prepareDraft();setPlan(await json('/setup/draft/restore/preview',{method:'POST',body:JSON.stringify({revision,target_revision:row.revision})}));});}
 function restore(){run(async()=>{const saved=await json('/setup/draft/restore',{method:'POST',body:JSON.stringify({revision:plan.current_revision,target_revision:plan.target_revision,preview_hash:plan.preview_hash})});await onRestored(saved);onClose();});}
 return <Dialog title={plan?'核对旧配置恢复范围':'配置历史'} onClose={()=>{if(!busy)onClose();}}><div className="modal-body history-body">{error&&<p className="error" role="alert">{error}</p>}
 {!plan?<><p>选择已保存版本，先核对恢复范围，再另存为新版本。现有版本与原件保留。</p>{rows.map(row=><details key={row.revision}><summary>第{row.revision}版 · {row.configuration.instanceProfile?.name||'初始配置'} · {new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</summary><Summary value={row.configuration}/><Btn disabled={busy||!row.configuration.version} onClick={()=>inspect(row)}>核对恢复第{row.revision}版</Btn></details>)}{more&&<Btn disabled={busy} onClick={loadMore}>查看更早版本</Btn>}<div className="actions"><Btn disabled={busy} onClick={onClose}>返回当前配置</Btn></div></>:<>
 <p>将第{plan.target_revision}版恢复为新的配置版本；当前第{plan.current_revision}版仍可从历史查看。</p><div className="understanding-comparison"><section><h3>当前配置</h3><Summary value={workspace}/></section><section><h3>待恢复配置</h3><Summary value={plan.configuration}/></section></div>
 {Object.entries({added:'恢复的业务',removed:'移出当前清单的业务',changed:'发生变化的流程'}).map(([key,label])=>plan.businesses[key].length>0&&<p key={key}>{label}：{plan.businesses[key].join('、')}</p>)}
 {plan.material_changes.length>0&&<><h3>资料状态与说明的变化</h3>{plan.material_changes.map(m=><div key={m.id}><strong>{m.name}</strong><p>{m.before.archived?'归档':'在用'} → {m.after.archived?'归档':'在用'}</p>{m.before.note!==m.after.note&&<p>说明：{m.before.note||'无'} → {m.after.note||'无'}</p>}{m.before.kind!==m.after.kind&&<p>分类：{m.before.kind} → {m.after.kind}</p>}</div>)}</>}
 {plan.retained_newer_materials.length>0&&<p>后续添加的资料转为归档保留，可随时恢复：{plan.retained_newer_materials.map(m=>m.name).join('、')}</p>}
 {plan.flows_requiring_confirmation.length>0&&<p>资料状态或解析版本已变，以下流程需要重新核对确认：{plan.flows_requiring_confirmation.join('、')}</p>}
 {(plan.connections_requiring_validation>0||plan.resources_requiring_validation>0)&&<p>{plan.connections_requiring_validation}个连接、{plan.resources_requiring_validation}项资源需要重新验证后才能使用。</p>}
 {plan.warnings.map((message,i)=><p key={i}>{message}</p>)}<p>本次恢复配置不会启动业务、发送云请求或操作设备。</p><div className="actions"><Btn disabled={busy} onClick={()=>setPlan(null)}>返回版本列表</Btn><Btn primary disabled={busy} onClick={restore}>{busy?'正在保存恢复版本…':'确认恢复并另存新版本'}</Btn></div></>}
 </div></Dialog>;
}
