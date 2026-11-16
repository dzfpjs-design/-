import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import FullSemanticGraphEditor from './FullSemanticGraphEditor';
import {FullSemanticGraphAdoptionController,graphAdoptionPendingScopes} from './full-semantic-graph-adoption-controller.mjs';
import {NODE_LABELS,nodeLabel,referenceLabel} from './full-semantic-graph-editor-model.mjs';
import {objectGraphFamily,graphHasEvents} from './full-semantic-graph-adoption-protocol.mjs';

const message=e=>e?.detail?.message||e?.message||'操作尚未完成，原输入和请求仍保留。';

export function GraphAdoptionPreview({preview,context,members,abilities,draft}){
 const binding=preview.value.binding,graph=binding.graph;
 const name=id=>nodeLabel({...draft,graph},context,id);
 const condition=(ast,depth=0)=>{if(!ast||depth>12)return '条件尚不能完整说明，需重新核对';if(ast.op==='ref')return referenceLabel({...draft,graph},context,ast.key);if(ast.op==='value')return ast.value===true?'是':ast.value===false?'否':String(ast.value);if(ast.op==='not')return '不满足（'+condition(ast.arg,depth+1)+'）';if(['all','any'].includes(ast.op))return '('+(ast.args||[]).map(item=>condition(item,depth+1)).join(ast.op==='all'?' 且 ':' 或 ')+')';const operation={eq:'等于',ne:'不等于',gt:'大于',gte:'不小于',lt:'小于',lte:'不大于'}[ast.op];return operation?condition(ast.left,depth+1)+' '+operation+' '+condition(ast.right,depth+1):'条件尚待核对';};
 const conditions=guards=>Object.entries(guards||{}).filter(([,id])=>id!==null).map(([phase,id])=>({label:({start:'开始条件',precondition:'前置条件',stop:'停止条件',exception:'例外条件'})[phase]||'运行条件',text:condition(graph.predicates[id])}));
 return <section className="card" aria-label="服务器完整执行预览">
  <h4>采用前核对本次完整安排</h4>
  <p>{binding.business_name} · {preview.kind==='revision'?'修订已有执行设计，仅供后续生成的版本使用':'首次采用执行设计'}。共有 {graph.nodes.length} 个步骤与控制结构、{graph.edges.length} 条明确连线、{graph.goals.length} 项完成标准。</p>
  {conditions(graph.guards).length>0&&<section aria-label="本次流程实际条件"><h5>本次流程实际条件</h5>{conditions(graph.guards).map((item,index)=><p key={index}>{item.label}：{item.text}</p>)}</section>}
  <ul>{graph.nodes.map(node=><li key={node.id}><strong>{name(node.id)}</strong> · {NODE_LABELS[node.kind]}{node.actor_id&&' · '+(members.find(m=>m.id===node.actor_id)?.name||'已冻结的原账号')}{node.kind==='ability'&&' · '+(abilities.find(a=>a.id===node.execution.capability_id)?.name||'已登记能力')+' · 版本 '+node.execution.release_version}{node.kind==='ability'&&<span> · {preview.command.external_node_ids.includes(node.id)?'本次明确允许按所选合同外发输入':'本地处理'}</span>}</li>)}</ul>
  {graph.nodes.filter(node=>conditions(node.guards).length||node.kind==='decision'||node.role).map(node=><article key={node.id}><h5>{name(node.id)}的职责与实际条件</h5>{node.role&&<p>职责：{node.role}；负责人：{members.find(member=>member.id===node.actor_id)?.name||'已冻结的原账号'}。</p>}{conditions(node.guards).map((item,index)=><p key={index}>{item.label}：{item.text}</p>)}{node.kind==='decision'&&<p>分支判断：{condition(graph.predicates[node.predicate_id])}</p>}</article>)}
  {graphHasEvents(preview.family)&&<section aria-label="本次消息来源与等待范围"><h5>本次消息来源与等待范围</h5>
   {graph.nodes.filter(node=>node.kind==='wait').map(node=>{const subscription=graph.subscriptions.find(s=>s.id===node.subscription_id),pin=binding.event_source_bindings[subscription.id],source=context.event_wait.sources.find(s=>s.producer_id===pin.producer_id),contract=pin.contract;return <article className="media-form" key={node.id}><h6>{name(node.id)}</h6><p>来源：{source?.label||'已冻结的原登记来源'} · 来源版本 {contract.source_release_version} · 提取能力版本 {contract.extractor_release_version}</p><p>关联对象来自“{referenceLabel({...draft,graph},context,subscription.correlation_ref)}”；只接纳本任务冻结对象范围内的匹配结果，不扩大对象范围。</p><p>最长等待 {node.timeout_seconds} 秒。以后台持久接纳时间判断到达，先前消息不补入新窗口，期限边界不算及时到达。</p><p>完整处理结果合同随来源冻结；收到结果仍是候选依据，不认证采集时间或现场事实。</p><ul>{Object.entries(contract.result_schema.properties||{}).map(([field,spec],index)=><li key={field}>{spec.title||'来源结果字段 '+(index+1)}{spec.description?'：'+spec.description:''}</li>)}</ul></article>;})}
   {!graph.subscriptions.length&&<p>本次安排没有消息等待订阅。</p>}
  </section>}
  {preview.family===objectGraphFamily&&<section aria-label="本次对象与身份范围"><h5>本次对象与身份范围</h5>{binding.source_design.result.objects.map(object=><article key={object.name}><h6>{object.name}</h6><p>业务标识：{object.business_identity.status==='not_established'?'尚未明确':object.business_identity.field_labels.join('、')}。{object.business_identity.explanation}</p><p>平台独立生成技术记录编号，不把它当作业务字段、业务编号或唯一性承诺。</p><ul>{object.fields.map((field,index)=><li key={index}>{field.label} · {field.kind} · {field.required?'必填':'可选'} · {field.unit===null?'未声明单位':field.unit}</li>)}</ul></article>)}</section>}
  <details><summary>核对全部连线与完成标准</summary><ul>{graph.edges.map(edge=><li key={edge.id}>{name(edge.from)} → {name(edge.to)}{edge.on!=='next'&&' · '+({true:'判断成立',false:'判断不成立',timeout:'等待超时'}[edge.on]||'明确选择的后续安排')}{edge.loop_id&&' · 有次数上限的重复'}</li>)}</ul><ul>{graph.goals.map(goal=><li key={goal.id}>{draft.ui?.goal_labels?.[goal.id]||'完成标准'}：{goal.required_nodes.map(name).join('、')}</li>)}</ul></details>
  <p>已逐字段保存 {binding.owner_bindings.length} 条原设计对应。位置证明用于核对来源位置，采用本次安排不会认证原文事实。实例仍须检查、生成并单独启用。</p>
 </section>;
}

export default function FullSemanticGraphAdoption({api=json,design=null,handoffId=design?.handoff_id,actorId,workspaceId,businessName,prepareMutation,readCurrentConfiguration,onRegisterGuard,recoveryOnly=false,contextFamily=design?.schema==='report-object-semantic-design-v169'?objectGraphFamily:'v4'}){
 const holder=useRef(null),live=useRef(true),busyRef=useRef(false),initial=useRef('');
 const [,render]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[consent,setConsent]=useState(false);
 const scope=[actorId,workspaceId,handoffId,contextFamily].join(':');
 if(holder.current?.scopeKey!==scope){holder.current?.controller?.dispose();try{holder.current={scopeKey:scope,controller:new FullSemanticGraphAdoptionController({api,design,handoffId,actorId,workspaceId,contextFamily,prepareMutation,onChange:()=>{if(live.current)render(n=>n+1);}})};initial.current='';}catch(e){holder.current={scopeKey:scope,controller:null};initial.current=message(e);}}
 const lifetime=useRef(null);if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();
 const c=holder.current.controller;if(c)c.prepareMutation=prepareMutation;
 async function act(fn){if(!c||busyRef.current||c.busy)return;busyRef.current=true;setBusy(true);setError('');setNotice('');try{await fn();c.check();}catch(e){if(c.active)setError(message(e));}finally{busyRef.current=false;if(c.active)setBusy(false);}}
 useEffect(()=>{const lease=c?lifetime.current.setup(c,scope):null;live.current=!!lease;if(lease)act(async()=>{c.check();let failed;try{await c.load();}catch(e){failed=e;}if(c.pending)await c.recover();if(failed)throw failed;});return()=>{live.current=false;if(lease)lifetime.current.cleanup(lease);};},[c]);
 useEffect(()=>{const guard=()=>{try{return c?c.persist():true;}catch{return false;}};onRegisterGuard?.(guard);const leave=e=>{if(guard())return;e.preventDefault();e.returnValue='';};globalThis.addEventListener?.('beforeunload',leave);return()=>{onRegisterGuard?.(null);globalThis.removeEventListener?.('beforeunload',leave);};},[c,onRegisterGuard]);
 useEffect(()=>{const changed=e=>{if(!c?.active||!e.key?.startsWith(c.prefix))return;try{c.refreshDrafts();c.pending=c.readPending();c.notify();setConsent(false);}catch(e){setError(message(e));}};globalThis.addEventListener?.('storage',changed);return()=>globalThis.removeEventListener?.('storage',changed);},[c]);
 if(!c)return <section aria-label="完整执行设计"><h3>完整执行设计</h3><p role="alert">{initial.current} 原本机记录保留，请保留页面并核对浏览器保存能力。</p></section>;
 if(c.denied)return <section><h3>完整执行设计</h3><p role="alert">账号或工作区已变化，原账号输入已隐藏。原草稿与提交请求仍保留。</p></section>;
 const writable=['admin','operator'].includes(c.role),locked=!c.locks?.request,p=c.pending,disabled=busy||c.busy||!writable||!!p;
 const problems=c.issues(),value=c.state.value,preview=c.preview;
 function change(next){setConsent(false);try{c.edit(next);}catch(e){setError(message(e));}}
 return <section className="full-semantic-graph-adoption" aria-label={recoveryOnly?'原完整流程请求恢复':'完整条件与协同执行配置'}>
  <h3>{recoveryOnly?'原完整流程请求恢复':'完整条件与协同执行配置'}</h3>
  {!recoveryOnly&&<p>在这里核对业务的人工步骤、注册能力、条件、并行协同和负责人。新修订只用于后续生成的业务版本；已有任务继续使用原版本。</p>}
  {!recoveryOnly&&businessName&&<p>当前查看的业务：<strong>{businessName}</strong>。请在下方选择对应业务，或先恢复需要继续处理的原流程稿。</p>}
  <div className="row"><button type="button" disabled={busy} onClick={()=>act(async()=>{await c.load();setConsent(false);})}>刷新来源、成员与可用能力</button><span role="status">{busy?'正在核对，请稍候…':c.localSaved?'本页输入已在本机保存':'本页输入尚未可靠保存'}</span></div>
  {locked&&<p role="alert">当前浏览器不能保护多窗口提交，可以查询原请求并保留草稿，采用提交暂不可用。</p>}
  {!c.localSaved&&<p role="alert">{c.localError}<button type="button" disabled={busy} onClick={()=>{try{c.persist();}catch(e){setError(message(e));}}}>重试保存本机稿</button><button type="button" disabled={busy} onClick={()=>{try{c.forkWorking();}catch(e){setError(message(e));}}}>另存本页输入</button></p>}
  {c.draftError&&<p role="alert">{c.draftError}</p>}
  {p&&<section className="card" aria-label="完整流程原请求状态"><h4>{p.body.business_name} · 原{p.kind==='revision'?'修订':'采用'}请求</h4>
   <p>{p.receipt?'服务器原回执已读回。读取当前配置后，可以继续配置。':p.rejection?'服务器明确拒绝原请求，原正文和输入仍保留。':'提交结果尚未核对，原编号和完整正文已保留。查询不会重发。'}</p>
   {p.rejection&&<p role="alert">{p.rejection.message}</p>}
   <button type="button" disabled={busy} onClick={()=>act(async()=>{const found=await c.recover();setNotice(found?'原回执已读回核对，请读取当前配置。':'服务器尚未查到原请求，原正文仍保留。');})}>查询原请求结果</button>
   {c.missing&&!p.rejection&&!p.receipt&&<button type="button" disabled={busy||!writable||locked} onClick={()=>act(()=>c.recover(true))}>明确重试同一请求</button>}
   {p.receipt&&<button type="button" disabled={busy||locked||!readCurrentConfiguration} onClick={()=>act(async()=>{await c.acceptCurrent(readCurrentConfiguration);setConsent(false);setNotice('原回执已保留，已读取服务器当前配置；可核对后续版本。');await c.load();})}>读取当前配置并保留原回执</button>}
   {p.rejection&&!p.receipt&&<button type="button" disabled={busy||locked} onClick={()=>act(async()=>{await c.archiveRejected();setConsent(false);})}>保留拒绝记录，返回修改</button>}
  </section>}
  {!recoveryOnly&&<>
   {c.otherDrafts.length>0&&<details><summary>其他本机流程稿 · {c.otherDrafts.length}份</summary>{c.otherDrafts.map((item,i)=><p key={item.key}>{item.state.value.business_name||'未选业务'} · 保留稿 {i+1} <button type="button" disabled={disabled} onClick={()=>act(()=>{c.restoreDraft(item.key);setConsent(false);})}>保留本页，读取此稿</button></p>)}</details>}
   {c.context?<><label>新建安排或读取当前采用的修订稿 <select value="" disabled={disabled||!c.context.active} onChange={e=>{if(e.target.value)try{c.chooseBusiness(e.target.value);setConsent(false);}catch(e){setError(message(e));}}}><option value="">请选择本次业务</option>{c.context.design.result.businesses.map(b=><option key={b.name} value={b.name}>{b.name}{c.context.adoptions.some(a=>a.is_current&&a.business_name===b.name)?' · 从当前采用建立修订稿':' · 新建安排'}</option>)}</select></label>
    <FullSemanticGraphEditor context={c.context} members={c.members} abilities={c.abilities} value={value} onChange={change} disabled={disabled||!c.localSaved} issues={problems}/>
    {value&&<div className="row"><button type="button" disabled={disabled||locked||!c.localSaved||!c.context.active||problems.length>0} onClick={()=>act(async()=>{setConsent(false);await c.makePreview();})}>核对本次完整执行预览</button><span>还有 {problems.length} 项待明确</span></div>}
   </>:<p role="status">尚未读到可核对的服务器设计状态。原输入与请求保留，可刷新或查询原请求。</p>}
   {preview&&value&&<><GraphAdoptionPreview preview={preview} context={c.context} members={c.members} abilities={c.abilities} draft={value}/><label><input type="checkbox" checked={consent} disabled={disabled} onChange={e=>setConsent(e.target.checked)}/>我已核对本次完整步骤、负责人、能力输入、条件、协同及外发范围{graphHasEvents(preview.family)?'，以及真实消息来源、关联对象和等待期限':''}，明确采用该执行安排；新修订供后续生成的版本使用。</label><button type="button" disabled={disabled||locked||!consent||!c.localSaved} onClick={()=>act(async()=>{await c.submit(consent);setConsent(false);setNotice('采用回执已保存，请读取服务器当前配置。');})}>{preview.kind==='revision'?'明确采用本次修订':'明确采用本次完整执行设计'}</button></>}
  </>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}

export function FullSemanticGraphRecoveries(props){
 const [selected,setSelected]=useState(null),[,refresh]=useState(0);
 useEffect(()=>{const changed=()=>refresh(n=>n+1);globalThis.addEventListener?.('storage',changed);globalThis.addEventListener?.('ga-full-semantic-graph-pending-change',changed);return()=>{globalThis.removeEventListener?.('storage',changed);globalThis.removeEventListener?.('ga-full-semantic-graph-pending-change',changed);};},[]);
 if(!props.actorId||!props.workspaceId||sessionStorage.getItem('ga-setup-csrf-user')!==props.actorId||sessionStorage.getItem('ga-setup-csrf-workspace')!==props.workspaceId)return null;
 let rows=[];try{rows=graphAdoptionPendingScopes(localStorage,props).filter(row=>row.handoff_id!==props.excludeHandoffId);}catch{return <p role="alert">其他流程的本机请求尚无法读取，原记录保留。</p>;}
 if(!rows.length)return null;
 return <section aria-label="尚未核对的完整流程提交"><h3>尚未核对的完整流程提交</h3><p>来源替换后，原请求仍可在这里查询。读取结果不会重新采用。</p>{rows.map((row,i)=>{const identity=row.family+':'+row.handoff_id;return row.unreadable?<p key={i} role="alert">有一份原请求无法核对，原记录保留。</p>:<div key={identity}><button type="button" onClick={()=>setSelected(identity)}>核对“{row.business_name}”的原提交</button>{selected===identity&&<FullSemanticGraphAdoption key={identity} {...props} handoffId={row.handoff_id} contextFamily={row.family||'v4'} design={null} recoveryOnly/>}</div>;})}</section>;
}
