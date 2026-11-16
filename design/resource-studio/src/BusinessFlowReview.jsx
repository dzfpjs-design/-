import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api} from './runtime-api.js';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {authorReviewState} from './business-flow-author-review.mjs';
import BusinessFlowExecutionEditor from './BusinessFlowExecutionEditor.jsx';
import {executionEditorEntries} from './business-flow-execution-editor.mjs';
import {BusinessFlowReviewController,reviewCopy,reviewIssueTarget} from './business-flow-review-controller.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import BusinessFlowAuthorReview from './BusinessFlowAuthorReview.jsx';
import BusinessFlowCausalEvidence from './BusinessFlowCausalEvidence.jsx';
import NestedFlowReview from './NestedFlowReview.jsx';
import NestedFlowAuthorReview from './NestedFlowAuthorReview.jsx';
import {nestedAuthorReviewState} from './nested-author-review.mjs';
import NestedBusinessFlowExecutionEditor from './NestedBusinessFlowExecutionEditor.jsx';
import {nestedExecutionEntries} from './nested-business-flow-execution-editor.mjs';
import {isNestedFlowPlan,nestedCitationUses,nestedFlow} from './nested-flow-review.mjs';

const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),list=v=>Array.isArray(v)?v:[],text=v=>typeof v==='string'?v:'';
const actors={business_agent:'业务智能体',perception_agent:'多模感知智能体',data_agent:'数据管理智能体',task_center:'任务中心',execution_service:'执行服务',human:'人工岗位'};
const edgeNames={sequence:'依次进行',condition:'满足条件后进行',parallel:'并行分支',wait:'等待其他步骤'};
const attr=()=>({citations:[],inferred:true,reason:''});
const newId=kind=>kind+'_'+crypto.randomUUID().replaceAll('-','').slice(0,20);
const toLines=v=>{const items=list(v);return items.every(x=>typeof x==='string')?items.join('\n'):'';};
const fromLines=v=>v===''?[]:v.split('\n');
function Field({label,value,onChange,area=false,disabled=false,readOnly=false,path}){const Tag=area?'textarea':'input';return <label className="bfr-field" data-review-path={path}><span>{label}</span><Tag aria-label={label} value={text(value)} disabled={disabled} readOnly={readOnly} onChange={e=>{if(!readOnly)onChange?.(e.target.value);}} rows={area?3:undefined}/></label>;}

// Count actual occurrences, including duplicated/unknown citations. This is a
// view of the working draft; it neither repairs references nor changes values.
export function reviewCitationUses(value){
 if(isNestedFlowPlan(value))return nestedCitationUses(value);
 const uses=[];
 function add(refs,path,label){list(refs).forEach((ref,index)=>{if(typeof ref==='string')uses.push({span_ref:ref,path:path+'/'+index,label});});}
 list(value?.business?.flows).forEach((flow,f)=>{
  const base='/business/flows/'+f,title=text(flow?.name)||`流程 ${f+1}`;
  add(flow?.trigger?.attribution?.citations,base+'/trigger/attribution/citations',title+' · 触发条件');
  for(const [kind,label]of [['nodes','步骤'],['edges','连线'],['deliverables','产出']])list(flow?.[kind]).forEach((item,i)=>add(item?.attribution?.citations,base+'/'+kind+'/'+i+'/attribution/citations',title+' · '+label+' '+(i+1)+(text(item?.name)?' · '+item.name:'')));
 });
 list(value?.business?.questions).forEach((question,i)=>add(question?.citations,'/business/questions/'+i+'/citations','待核问题 '+(i+1)));
 return uses;
}

export function reviewRestoreRemoval(value,removal){
 const fail=()=>{throw Error('原数组已变化或条目已经存在，不能安全撤销。当前输入与完整本机备份保留，可先导出备份核对。');};
 if(!obj(removal)||!Array.isArray(removal.path)||!Array.isArray(removal.original_array)||!Number.isSafeInteger(removal.index)||removal.index<0||removal.index>=removal.original_array.length)fail();
 const path=removal.path.join('.');if(path!=='evidence'&&path!=='business.questions')fail();
 if(JSON.stringify(removal.original_array[removal.index])!==JSON.stringify(removal.item))fail();
 const next=reviewCopy(value),array=path==='evidence'?next?.evidence:next?.business?.questions;
 if(!Array.isArray(array)||removal.index>array.length)fail();
 const key=JSON.stringify(removal.item),expected=removal.original_array.filter((item,i)=>i!==removal.index&&JSON.stringify(item)===key).length;
 if(array.filter(item=>JSON.stringify(item)===key).length>expected)fail();
 array.splice(removal.index,0,reviewCopy(removal.item));return next;
}

export function ReadValue({value,sources=[],onCitation}){
 if(isNestedFlowPlan(value))return <div className="bfr-readonly" aria-label="完整递归流程对照">{list(value.business?.flows).map((flow,index)=><NestedFlowReview key={index} value={value} flowIndex={index} readOnly sources={sources} onCitation={onCitation} showQuestions={index===value.business.flows.length-1}/>)}</div>;
 const business=obj(value?.business)?value.business:null,quotes=list(value?.evidence);
 const cite=refs=><p>关联原文：{list(refs).map((ref,i)=>{const at=quotes.findIndex(q=>q?.span_ref===ref);return <span key={i}>{i?'、':''}{at<0?'未列入本稿依据表的引用':`原文片段 ${at+1}`}</span>;})}{!list(refs).length&&'未关联'}</p>;
 const attribution=a=><div><p>{a?.inferred===true?'原稿标注为推断':a?.inferred===false?'原稿标注为非推断，仍需核对':'推断标记待补'}；说明：{text(a?.reason)||'未填写'}</p>{cite(a?.citations)}</div>;
 return <div className="bfr-readonly">{!business?<p>原内容暂不能按流程结构展示；原模型稿仍由服务器保留。</p>:<>
  <h3>{text(business.name)||'未命名业务'}</h3>
  {list(business.flows).map((f,i)=>{
   const nodes=list(f?.nodes),name=id=>text(nodes.find(n=>n?.node_id===id)?.name)||'原去向缺失或步骤未命名',groups=[...new Set(list(f?.edges).filter(e=>e?.kind==='parallel').map(e=>e.group))];
   return <article key={i}><h4>{text(f?.name)||`流程 ${i+1}`}</h4>
    <p>启动方式：{{manual:'人工发起',schedule:'按计划启动',event:'发生业务事件时',data_arrival:'收到相关数据时'}[f?.trigger?.kind]||'待填写'}</p><p>触发条件：{text(f?.trigger?.description)||'未填写'}</p>{attribution(f?.trigger?.attribution)}
    <p>开始步骤：{name(f?.start_node)}</p><p>结束步骤：{list(f?.end_nodes).map(name).join('、')||'未填写'}</p>
    <h5>步骤与职责</h5>{nodes.map((n,j)=><div className="bfr-read-node" key={j}><strong>{j+1}. {text(n?.name)||'未命名步骤'}</strong><p>{actors[n?.actor]||'职责类型待补'} · {text(n?.role)}</p><p>职责：{text(n?.responsibility)||'未填写'}</p><p>输入：{list(n?.inputs).map(text).join('、')||'无已填输入'}</p><p>输出：{list(n?.outputs).map(text).join('、')||'未填写'}</p>{attribution(n?.attribution)}</div>)}
    <h5>全部连线与等待条件</h5>{list(f?.edges).map((e,j)=><div className="bfr-read-node" key={j}><strong>{edgeNames[e?.kind]||'连线方式待核对'} {j+1}</strong><p>{e?.kind==='wait'?list(e.from_nodes).map(name).join('、'):name(e?.from_node)} → {name(e?.to_node)}</p>{e?.kind==='condition'&&<p>条件：{text(e.condition)||'未填写'}</p>}{e?.kind==='wait'&&<p>等待规则：{e.mode==='all'?'全部完成后继续':e.mode==='any'?'任意一项完成后继续':'待核对'}</p>}{e?.kind==='parallel'&&<p>并行分组 {groups.indexOf(e.group)+1}</p>}{attribution(e?.attribution)}</div>)}{!list(f?.edges).length&&<p>没有已填连线</p>}
    <h5>最终产出</h5>{list(f?.deliverables).map((d,j)=><div className="bfr-read-node" key={j}><strong>{text(d?.name)||'未命名产出'}</strong><p>{text(d?.description)||'未填写说明'}</p><p>产生步骤：{list(d?.produced_by).map(name).join('、')||'未填写'}</p>{attribution(d?.attribution)}</div>)}
   </article>;
  })}
  <h4>全部待核问题</h4>{list(business.questions).map((q,i)=><article key={i}><strong>{text(q?.question)||'问题未填写'}</strong><p>原因：{text(q?.reason)||'未填写'}</p>{cite(q?.citations)}</article>)}{!list(business.questions).length&&<p>没有已填待核问题</p>}
 </>}
 <h4>本稿完整引用文字</h4>{quotes.map((q,i)=><article key={i}><strong>原文片段 {i+1}</strong><p>{text(q?.quote)||'引用文字未填写'}</p></article>)}{!quotes.length&&<p>未列入依据</p>}
 </div>;
}

export function reviewValueMatchesOriginal(value,baseline){
 if(value===baseline)return true;
 if(Array.isArray(value)||Array.isArray(baseline))return Array.isArray(value)&&Array.isArray(baseline)&&value.length===baseline.length&&value.every((item,i)=>reviewValueMatchesOriginal(item,baseline[i]));
 if(!obj(value)||!obj(baseline))return false;
 const keys=Object.keys(value);return keys.length===Object.keys(baseline).length&&keys.every(key=>Object.hasOwn(baseline,key)&&reviewValueMatchesOriginal(value[key],baseline[key]));
}

export default function BusinessFlowReview({producerId,actorId,workspaceId,onRegisterGuard,onConfigurationAdopted,prepareConfiguration,refreshToken=0,openRequest=null}){
 const[,draw]=useState(0),[flowIndex,setFlow]=useState(0),[nodeIndex,setNode]=useState(0),[edgeIndex,setEdge]=useState(0),[pane,setPane]=useState('edit'),[focusedSource,setFocusedSource]=useState(null);
 const lifetime=useRef(null),root=useRef(null);if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();
 const c=useMemo(()=>new BusinessFlowReviewController({producerId,actorId,workspaceId,api:json}),[producerId,actorId,workspaceId]);
 const typedHTTP=useMemo(()=>scopedPreparationRequester({user_id:actorId,workspace_id:workspaceId},async(path,options)=>(await api(path,options)).text()),[actorId,workspaceId,c]);
 const author=authorReviewState(c),nestedAuthor=nestedAuthorReviewState(c),selectedQualification=c.state?.executionSelected?.[c.state.selected];
 let retainedExecutions=[],retainedNestedExecutions=[],retainedExecutionError='';try{retainedExecutions=executionEditorEntries(c).filter(entry=>entry.qualification.attempt_id===c.state.selected);retainedNestedExecutions=nestedExecutionEntries(c).filter(entry=>entry.qualification.attempt_id===c.state.selected);}catch(error){retainedExecutionError=error.message;}
 const qualification=[author?.result,...(author?.history||[]),...retainedExecutions.map(entry=>entry.qualification)].find(v=>v&&selectedQualification&&v.id===selectedQualification&&v.attempt_id===c.state.selected);
 const nestedQualification=[...(Object.values(nestedAuthor?.forms||{}).map(form=>form.result)),...(nestedAuthor?.history||[]),...retainedNestedExecutions.map(entry=>entry.qualification)].find(v=>v&&selectedQualification&&v.id===selectedQualification&&v.attempt_id===c.state.selected);
 const configureExecution=value=>{c.check();if(c.busy||c.identityStatus!=='verified'||!['admin','operator','viewer'].includes(c.role)||!c.localSaved||value.attempt_id!==c.state.selected)throw Error('请先保全本页输入并核对当前账号，再打开执行配置；只读账号不能修改或采用。');c.state.executionSelected??={};c.state.executionSelected[c.state.selected]=value.id;if(!c.persist())throw Error(c.error);};
 const scope=[actorId,workspaceId,producerId].join(':');
 useEffect(()=>{const lease=lifetime.current.setup(c,scope);if(!lease)return;let mounted=true;c.onChange=()=>{if(mounted&&c.active&&!c.suspended)draw(n=>n+1);};c.open().catch(()=>{});
  const before=e=>{if(!c.safeToLeave()){e.preventDefault();e.returnValue='';}};
  const focus=()=>c.recheckIdentity().catch(()=>{});window.addEventListener('beforeunload',before);window.addEventListener('focus',focus);
  return()=>{mounted=false;window.removeEventListener('beforeunload',before);window.removeEventListener('focus',focus);lifetime.current.cleanup(lease);};},[c,scope]);
 useEffect(()=>{if(typeof onRegisterGuard!=='function')return;const guard=()=>c.safeToLeave();onRegisterGuard(guard);return()=>onRegisterGuard(null,guard);},[c,onRegisterGuard]);
 useEffect(()=>{if(!refreshToken)return;let stopped=false,timer;const refresh=()=>{if(stopped||!c.active||c.suspended||c.denied||c.identityStatus!=='verified')return;if(c.busy){timer=setTimeout(refresh,150);return;}c.operation(()=>c.refreshCandidates()).catch(()=>{});};refresh();return()=>{stopped=true;clearTimeout(timer);};},[c,refreshToken]);
 useEffect(()=>{if(!openRequest?.attemptId)return;let stopped=false,timer;const open=()=>{if(stopped||!c.active||c.suspended||c.denied)return;if(c.identityStatus!=='verified'||c.busy){timer=setTimeout(open,150);return;}if(!c.safeToLeave()){c.error='请先保护当前人工稿，再打开已保存的新候选。';c.notify();return;}c.operation(async()=>{await c.refreshCandidates();if(stopped)return;await c.select(openRequest.attemptId);if(stopped)return;setFlow(0);setNode(0);setEdge(0);setPane('edit');setFocusedSource(null);}).catch(()=>{});};open();return()=>{stopped=true;clearTimeout(timer);};},[c,openRequest]);
 const action=fn=>{try{const result=fn();if(result?.catch)result.catch(error=>{c.error=error.message;c.notify();});}catch(error){c.error=error.message;c.notify();}};
 const editable=c.canEdit&&!!c.form,detail=c.detail,value=c.form?.value,business=obj(value?.business)?value.business:null;
 const revised=detail?.source_binding?.generation_profile==='flow-v185',nested=isNestedFlowPlan(value)||detail?.source_binding?.value_schema==='report-business-flow-nested-plan-v192',recoveredNested=detail?.source_binding?.schema==='business-flow-nested-component-review-source-v194'||detail?.generation_recovery?.schema==='business-flow-nested-component-view-v194',structured=['flow-v184','flow-v185','flow-v187','flow-v190','flow-v193'].includes(detail?.source_binding?.generation_profile);
 const flows=list(business?.flows),flow=flows[flowIndex],nodes=list(flow?.nodes),edges=list(flow?.edges),node=nodes[nodeIndex],edge=edges[edgeIndex];
 const evidence=list(detail?.diagnostics?.source_evidence),issues=list(detail?.diagnostics?.issues),freshDiagnostics=!!c.form&&!c.form.dirty;
 const citationUses=reviewCitationUses(value),currentEvidence=list(value?.evidence),boundName=detail?.source_available===true?text(detail.name):text(detail?.baseline_value?.business?.name)||text(business?.name);
 function editTracked(value,{backup=false,removal=null}={}){
  c.edit(value,c.form.note,{backup});
  if(backup){const entry=c.form.backups.at(-1);entry.applied_value=reviewCopy(c.form.value);entry.applied_note=c.form.note;if(removal)entry.removal=reviewCopy(removal);c.persist();}
 }
 function change(path,next,backup=false){action(()=>{const v=reviewCopy(c.form.value);let target=v;for(let i=0;i<path.length-1;i++){const key=path[i];if(!obj(target[key])&&!Array.isArray(target[key]))target[key]=typeof path[i+1]==='number'?[]:{};target=target[key];}target[path.at(-1)]=next;editTracked(v,{backup});});}
 function mutate(fn,backup=false){action(()=>{const v=reviewCopy(c.form.value);fn(v);editTracked(v,{backup});});}
 function removeArrayItem(path,index){
  const v=reviewCopy(c.form.value),array=path.length===1?v.evidence:v.business?.questions;
  if(!Array.isArray(array)||index<0||index>=array.length)throw Error('该条目已变化，未删除。');
  const removal={path:reviewCopy(path),index,item:reviewCopy(array[index]),original_array:reviewCopy(array)};array.splice(index,1);editTracked(v,{backup:true,removal});
 }
 function undoStructure(){action(()=>{
  c.check();if(!c.canEdit||!c.form)throw Error('当前账号或稿件无法编辑，未撤销。');
  const entry=c.form?.backups.at(-1);if(!entry)throw Error('没有可撤销的修改。');
  if(entry.removal){const next=reviewRestoreRemoval(c.form.value,entry.removal);c.form.backups.pop();c.edit(next,c.form.note);return;}
  if(!Object.hasOwn(entry,'applied_value')||JSON.stringify(c.form.value)!==JSON.stringify(entry.applied_value)||c.form.note!==entry.applied_note)throw Error('结构修改后还有其他输入，未整稿回退。当前输入和完整备份均保留，请先保存或导出后核对。');
  c.undo();
 });}
 const fpath=['business','flows',flowIndex],npath=[...fpath,'nodes',nodeIndex],epath=[...fpath,'edges',edgeIndex];
 function citationControls(refs,path){
  return <div data-review-path={'/'+path.join('/')}>
   {list(refs).map((ref,i)=>{const source=evidence.findIndex(s=>s.span_ref===ref);return <div className="bfr-citation" key={i}><span>{source<0?'原引用不在当前业务范围，需明确移除或核对':`已关联原文片段 ${source+1}`}</span><button disabled={!editable} onClick={()=>change(path,list(refs).filter((_ref,index)=>index!==i),true)}>移除此关联原文 {i+1}（可撤销）</button>{source>=0&&<button onClick={()=>jump({span_ref:ref,path:'/'+path.join('/')})}>查看对应原文</button>}</div>;})}
   <details><summary>关联原文：已选 {list(refs).length} 段，展开选择</summary>
    <p>选择关联原文并填入完整引用（可多选）；原模型稿保持只读。</p>
    {evidence.map((source,i)=><label key={source.span_ref} className="bfr-check"><input type="checkbox" disabled={!editable} checked={list(refs).includes(source.span_ref)} onChange={e=>mutate(v=>{
     let t=v;for(let i=0;i<path.length-1;i++){const k=path[i];if(!obj(t[k])&&!Array.isArray(t[k]))t[k]=typeof path[i+1]==='number'?[]:{};t=t[k];}
     const selected=list(refs).filter(x=>x!==source.span_ref);if(e.target.checked)selected.push(source.span_ref);t[path.at(-1)]=selected;
     if(e.target.checked){if(!Array.isArray(v.evidence))v.evidence=[];if(!v.evidence.some(x=>x?.span_ref===source.span_ref))v.evidence.push({span_ref:source.span_ref,quote:source.source_text});}
    },!e.target.checked)}/> 原文片段 {i+1}</label>)}
    {!evidence.length&&<p>原文暂不可读取；不要补造引用，仍可保留本机输入。</p>}
   </details></div>;
 }
 function sourceControls(owner,path){
  const current=obj(owner?.attribution)?owner.attribution:attr();
  return <fieldset className="bfr-sources" data-review-path={'/'+[...path,'attribution'].join('/')}><legend>依据与推断</legend>
   <label><input type="checkbox" disabled={!editable} checked={current.inferred===true} onChange={e=>change([...path,'attribution'],{...current,inferred:e.target.checked})}/> 这是需要人工核对的推断</label>
   <Field label="依据说明 / 推断原因" value={current.reason} disabled={!editable} area onChange={v=>change([...path,'attribution'],{...current,reason:v})}/>
   {citationControls(current.citations,[...path,'attribution','citations'])}
  </fieldset>;
 }
 function nodeSelect(label,current,onChange,multiple=false){return <label className="bfr-field"><span>{label}</span><select aria-label={label} disabled={!editable} multiple={multiple} value={multiple?list(current):text(current)} onChange={e=>onChange(multiple?Array.from(e.target.selectedOptions,x=>x.value):e.target.value)}>{!multiple&&<option value="">请选择步骤</option>}{!multiple&&current&&!nodes.some(n=>n?.node_id===current)&&<option value={current}>原去向缺失，请重新选择</option>}{nodes.filter(n=>typeof n?.node_id==='string').map((n,i)=><option key={n.node_id} value={n.node_id}>{text(n.name)||`未命名步骤 ${i+1}`}</option>)}</select></label>;}
 function jump(issue){
  setPane('edit');const target=reviewIssueTarget(issue.path);if(target){setFlow(target.flow);if(target.kind==='nodes')setNode(target.index);if(target.kind==='edges')setEdge(target.index);}
  const at=/^\/evidence\/(\d+)/.exec(issue.path||''),ref=typeof issue.span_ref==='string'?issue.span_ref:at?currentEvidence[Number(at[1])]?.span_ref:null;
  setFocusedSource(typeof ref==='string'?ref:null);
  requestAnimationFrame(()=>{
   const matches=Array.from(root.current?.querySelectorAll('[data-review-path]')||[]).filter(el=>issue.path===el.dataset.reviewPath||issue.path?.startsWith(el.dataset.reviewPath+'/')).sort((a,b)=>b.dataset.reviewPath.length-a.dataset.reviewPath.length);
   const source=ref?Array.from(root.current?.querySelectorAll('[data-focused-source]')||[]).find(el=>el.dataset.focusedSource===ref):null,match=source||matches[0];
   for(let parent=match?.parentElement;parent&&parent!==root.current;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
   match?.scrollIntoView?.({block:'center',behavior:'smooth'});(match?.querySelector('input,textarea,select,button')||match)?.focus?.();
  });
 }
 function removeEvidence(index){action(()=>{
  const entries=list(c.form.value?.evidence),ref=entries[index]?.span_ref,used=reviewCitationUses(c.form.value).filter(use=>use.span_ref===ref),copies=entries.filter(entry=>typeof ref==='string'&&entry?.span_ref===ref).length;
  if(used.length&&copies<2)throw Error('此依据仍被使用，请先定位并明确移除相应引用，未删除在用依据。');
  removeArrayItem(['evidence'],index);
 });}
 function sourceComparison(ref){
  const source=evidence.find(item=>item.span_ref===ref),entries=currentEvidence.map((entry,index)=>({entry,index})).filter(({entry})=>entry?.span_ref===ref);
  return <section className="bfr-card" data-focused-source={ref} tabIndex={-1}><h4>当前问题对应原文</h4>{source?<p className="bfr-source-text">{source.source_text}</p>:<p>此引用不在当前已核对的原文范围。请定位使用项后明确移除，不会替换为其他来源。</p>}{entries.map(({entry,index})=><div key={index}><p>当前人工稿引用文字：</p><p className="bfr-source-text">{text(entry?.quote)||'未填写'}</p>{source&&<button disabled={!editable} onClick={()=>change(['evidence',index,'quote'],source.source_text,true)}>将此原文填入引用 {index+1}</button>}</div>)}{!entries.length&&<p>当前人工稿尚未列入此段引用文字，可在对应问题或职责的“关联原文”中明确选择。</p>}</section>;
 }
 function download(){action(()=>{const blob=new Blob([c.exportText()],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='业务流程修订备份.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),0);});}
 return <section className="bfr" ref={root}><style>{styles}</style><header className="bfr-head"><div><h2>修订业务流程</h2><p>未完成的流程也可保存为草稿。原模型稿只读保留，保存不代表确认、采用或执行。</p></div><button disabled={!editable||c.busy||!!c.pending||!c.localSaved||detail?.source_available!==true} onClick={()=>action(()=>c.save())}>{c.busy?'正在核对保存…':'保存未完成草稿'}</button></header>
 {c.error&&<div role="alert" className="bfr-error">{c.error}</div>}{c.notice&&<p role="status" className="bfr-notice">{c.notice}</p>}
 {c.identityStatus!=='verified'?<div role="status"><p>正在核对当前账号与工作区。身份未核对前不显示私人稿件。</p><button disabled={c.denied} onClick={()=>action(()=>c.open())}>重新读取</button>{c.denied&&<p>会话已变化，请回到当前账号重新打开此页面；原账号稿件仍保留。</p>}</div>:<>
 {!c.localSaved&&<div className="bfr-error">本机恢复记录尚未保存，请勿关闭本页。<button onClick={()=>c.persist()}>重试本机保存</button><button onClick={download}>导出备份</button></div>}
 {c.pending&&<div className="bfr-pending"><strong>原保存结果待核对</strong><p>可以继续编辑；新输入不会替换已经发出的原请求。尚未查到回执也不表示原请求未到达。</p><button disabled={c.busy} onClick={()=>action(()=>c.recover())}>查询原保存结果</button>{c.notFound&&!c.pending.rejection&&<button disabled={c.busy||!c.localSaved} onClick={()=>action(()=>c.recover(true))}>按原内容重试保存</button>}{c.pending.rejection&&<button disabled={c.busy||!c.localSaved} onClick={()=>action(()=>c.archiveRejected())}>保留被拒原请求，处理版本冲突</button>}</div>}
 {detail?.source_binding?.schema==='business-flow-component-review-source-v186'&&<p role="alert">原AI复核未通过定位格式检查。本稿只提取了其中通过流程结构与原文引用核对的流程部分，另存为未完成稿；原失败记录和原路径保留，不代表原复核成功。共有 {list(detail?.generation_review?.location_issues).length} 处定位问题，仍需人工逐项核对。</p>}
 {recoveredNested&&<section className="bfr-pending" role="alert" aria-label="原回答未通过来源核对"><strong>原回答的输入来源核对未通过；已单独恢复流程草稿供修订，原失败记录保留。</strong><p>本稿仅恢复原回答中的流程部分；不表示原回答成功，也不表示资料来源、实际能力或执行权限已验证。{list(detail?.generation_recovery?.issues).length?'已知问题在原候选对应资料项旁标示，修改后仍须重新核对。':'详细问题暂不可读取，仍须逐项对照原文。'}</p>{list(detail?.generation_recovery?.issues).filter(issue=>/^\/business\/flows\/\d+\/.*\/inputs\/\d+(?:\/|$)/.test(issue?.path||'')).map((issue,index)=><button type="button" key={index} onClick={()=>jump(issue)}>定位原候选资料问题 {index+1}</button>)}</section>}
 {revised&&<section className="bfr-card" aria-label="原文复核发现"><h3>原文复核后的新候选</h3><p>这是独立机器修订，原始AI计划与原人工稿保留；下列发现尚需人工核对，不表示结论正确或执行授权。</p>{list(detail?.generation_review?.findings).map((finding,i)=><article key={i}><strong>问题 {i+1} · {finding.resolution==='unresolved'?'尚待解决':finding.resolution==='removed'?'已从新候选移除':'已提出修订'}</strong><p>{finding.explanation}</p><p>{finding.resolution_explanation}</p><p>依据：{finding.basis==='platform_policy'?'平台职责政策':finding.basis==='both'?'平台职责政策与本次原文':'本次原文'}</p>{list(finding.citations).map(ref=><button key={ref} onClick={()=>setFocusedSource(ref)}>查看对应原文</button>)}</article>)}{!list(detail?.generation_review?.findings).length&&<p>本次机器复核未列出发现，仍需核对实际业务。</p>}</section>}
 {structured&&<p role="note">{nested?'此候选保留AI计划中条件、并行与循环的层级，原始回答另行保留；引用对应原文不代表业务结论、能力或执行权限已经核实。':'步骤、条件和依据选择来自AI计划；流程编号、连线和引用原文由平台按该计划整理。原始AI回答另行保留。引用字面一致不代表业务结论已经核实。'}</p>}
 <div className="bfr-layout"><aside className="bfr-left"><h3>业务候选</h3><button disabled={!c.localSaved} onClick={()=>action(()=>c.refreshCandidates())}>刷新已保存候选</button>{!c.items.length&&<p>暂无可修订的已保存回答。</p>}{c.items.map((item,i)=><button className={item.attempt_id===c.state.selected?'selected':''} key={item.attempt_id} disabled={!c.localSaved} onClick={()=>{setFlow(0);setNode(0);setEdge(0);setPane('edit');setFocusedSource(null);action(()=>c.select(item.attempt_id));}}><strong>{item.name||`业务候选 ${i+1}`}</strong><small>{item.saved_version?'已有修订草稿':'从原模型稿开始修订'}</small></button>)}
 {!!c.form&&<><h3>工作流程</h3>{flows.map((f,i)=><button key={i} className={i===flowIndex?'selected':''} onClick={()=>{setFlow(i);setNode(0);setEdge(0);}}>{text(f?.name)||`未命名流程 ${i+1}`}</button>)}<button disabled={!editable||!business||(nested&&flows.length>=16)} onClick={()=>mutate(v=>{if(!Array.isArray(v.business.flows))v.business.flows=[];v.business.flows.push(nested?nestedFlow():{flow_id:newId('flow'),name:'',trigger:{kind:'manual',description:'',attribution:attr()},start_node:'',end_nodes:[],nodes:[],edges:[],deliverables:[]});setFlow(v.business.flows.length-1);},nested)}>＋ 添加工作流程</button><button disabled={!editable||!c.form.backups.length} onClick={undoStructure}>撤销最近一次结构修改</button><button onClick={download}>导出本机修订备份</button></>}</aside>
 <main className="bfr-editor">{!c.form?<p>选择左侧业务候选，读取服务器原稿与已保存修订。</p>:<><div className="bfr-tabs">{[['edit','编辑草稿'],['baseline',structured?'AI计划生成的流程（只读）':'原模型稿（只读）'],['server','服务器已存稿（只读）']].map(([id,label])=><button key={id} aria-pressed={pane===id} onClick={()=>setPane(id)}>{label}</button>)}</div><p className="bfr-state">服务器版本 {c.form.version} · {c.form.dirty?'当前修改尚未保存到服务器':'当前稿已与读取版本对齐'} · 始终为未完成草稿</p>{detail?.source_available===false&&<p className="bfr-error">原文来源暂不可核对，保存到服务器已暂停；本机修改与原稿保留。</p>}{detail&&detail.version!==c.form.version&&<div className="bfr-pending">服务器已有版本 {detail.version}。请先查看“服务器已存稿”，当前输入不会被覆盖。<button disabled={!!c.pending||!c.localSaved} onClick={()=>action(()=>c.alignVersion())}>保留本稿，按已读取的新版本继续</button></div>}
 {pane!=='edit'?<ReadValue value={pane==='baseline'?detail?.baseline_value:detail?.value} sources={evidence} onCitation={ref=>setFocusedSource(ref)}/>:!obj(value)?<p>原回答无法读成可编辑的流程对象。原模型稿仍保留，当前不能伪装成有效方案。</p>:<>
 {nested?<NestedFlowReview key={c.state.selected+':edit:'+flowIndex} value={value} flowIndex={flowIndex} disabled={!editable} sources={evidence} generationCausality={recoveredNested?undefined:detail?.generation_causality} generationRecovery={recoveredNested?detail?.generation_recovery:undefined} currentChanged={!!c.form?.dirty||!reviewValueMatchesOriginal(c.form?.value,detail?.baseline_value)} onChange={(next,options)=>action(()=>editTracked(next,options))} onCitation={ref=>setFocusedSource(ref)} onFlowIndexChange={setFlow}/>:!business?<div className="bfr-pending"><p>业务结构缺失，可补齐空白结构后逐项填写；原模型稿不变。</p><button disabled={!editable} onClick={()=>mutate(v=>{v.business={name:boundName,flows:[],questions:[]};},true)}>补齐业务编辑结构</button></div>:<><Field label="业务名称（本次范围固定）" value={boundName} readOnly path="/business/name"/><p>本次修订仅针对该业务范围，各工作流程可单独命名。</p>{detail?.source_available===true&&business.name!==boundName&&<div className="bfr-pending">当前人工稿的业务名称与本次范围不一致。原名称仍保留在模型对照稿中。<button disabled={!editable} onClick={()=>change(['business','name'],boundName,true)}>按本业务范围名称修正（可撤销）</button></div>}
 {obj(flow)?<><div className="bfr-row"><Field label="流程名称" value={flow.name} disabled={!editable} path={`/business/flows/${flowIndex}/name`} onChange={v=>change([...fpath,'name'],v)}/><button disabled={!editable} onClick={()=>mutate(v=>{v.business.flows.splice(flowIndex,1);setFlow(0);},true)}>删除此流程（可撤销）</button></div><label className="bfr-field"><span>何时启动</span><select disabled={!editable} value={text(flow.trigger?.kind)} onChange={e=>change([...fpath,'trigger'],{...obj(flow.trigger)?flow.trigger:{description:'',attribution:attr()},kind:e.target.value})}><option value="">请选择</option><option value="manual">人工发起</option><option value="schedule">按计划启动</option><option value="event">发生业务事件时</option><option value="data_arrival">收到相关数据时</option></select></label><Field label="触发条件说明" area value={flow.trigger?.description} disabled={!editable} onChange={v=>change([...fpath,'trigger','description'],v)}/>{sourceControls(flow.trigger,[...fpath,'trigger'])}
 {nodeSelect('从哪个步骤开始',flow.start_node,v=>change([...fpath,'start_node'],v))}{nodeSelect('哪些步骤结束后完成本流程（可多选）',flow.end_nodes,v=>change([...fpath,'end_nodes'],v),true)}
 <h3>步骤与职责</h3><div className="bfr-list">{nodes.map((n,i)=><button key={i} aria-pressed={i===nodeIndex} onClick={()=>setNode(i)}>{i+1}. {text(n?.name)||'未命名步骤'}</button>)}<button disabled={!editable} onClick={()=>mutate(v=>{if(!Array.isArray(v.business.flows[flowIndex].nodes))v.business.flows[flowIndex].nodes=[];v.business.flows[flowIndex].nodes.push({node_id:newId('node'),name:'',actor:'human',role:'',responsibility:'',inputs:[],outputs:[],attribution:attr()});setNode(nodes.length);})}>＋ 添加步骤</button></div>
 {obj(node)&&<div className="bfr-card" data-review-path={`/business/flows/${flowIndex}/nodes/${nodeIndex}`}><Field label="步骤名称" value={node.name} disabled={!editable} onChange={v=>change([...npath,'name'],v)}/><label className="bfr-field"><span>由谁承担职责</span><select disabled={!editable} value={text(node.actor)} onChange={e=>change([...npath,'actor'],e.target.value)}><option value="">请选择职责类型</option>{Object.entries(actors).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><Field label="负责岗位 / 角色" value={node.role} disabled={!editable} onChange={v=>change([...npath,'role'],v)}/><Field label="具体职责" area value={node.responsibility} disabled={!editable} onChange={v=>change([...npath,'responsibility'],v)}/><Field label="需要哪些输入（每行一项）" area value={toLines(node.inputs)} disabled={!editable} onChange={v=>change([...npath,'inputs'],fromLines(v))}/><BusinessFlowCausalEvidence view={detail?.generation_causality} flowIndex={flowIndex} nodeId={node.node_id} currentChanged={!!c.form?.dirty||!reviewValueMatchesOriginal(c.form?.value,detail?.baseline_value)} onCitation={ref=>setFocusedSource(ref)}/><Field label="产生哪些输出（每行一项）" area value={toLines(node.outputs)} disabled={!editable} onChange={v=>change([...npath,'outputs'],fromLines(v))}/>{sourceControls(node,npath)}<button disabled={!editable} onClick={()=>mutate(v=>{v.business.flows[flowIndex].nodes.splice(nodeIndex,1);setNode(0);},true)}>删除此步骤（保留连线供重新核对）</button></div>}
 <h3>步骤之间如何衔接</h3><div className="bfr-list">{edges.map((e,i)=><button key={i} aria-pressed={i===edgeIndex} onClick={()=>setEdge(i)}>{edgeNames[e?.kind]||'待补连线'} {i+1}</button>)}<button disabled={!editable} onClick={()=>mutate(v=>{const f=v.business.flows[flowIndex];if(!Array.isArray(f.edges))f.edges=[];f.edges.push({kind:'sequence',from_node:'',to_node:'',attribution:attr()});setEdge(f.edges.length-1);})}>＋ 添加连线</button></div>
 {obj(edge)&&<div className="bfr-card" data-review-path={`/business/flows/${flowIndex}/edges/${edgeIndex}`}><label className="bfr-field"><span>衔接方式</span><select disabled={!editable} value={text(edge.kind)} onChange={e=>{const kind=e.target.value,common={kind,to_node:text(edge.to_node),attribution:obj(edge.attribution)?edge.attribution:attr()};change(epath,kind==='wait'?{...common,from_nodes:[],mode:'all'}:kind==='condition'?{...common,from_node:'',condition:''}:kind==='parallel'?{...common,from_node:'',group:edges.find(x=>x?.kind==='parallel')?.group||newId('group')}:{...common,from_node:''},true);}}>{Object.entries(edgeNames).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>{edge.kind==='wait'?<>{nodeSelect('等待哪些步骤（可多选）',edge.from_nodes,v=>change([...epath,'from_nodes'],v),true)}<label className="bfr-field"><span>等待规则</span><select disabled={!editable} value={text(edge.mode)} onChange={e=>change([...epath,'mode'],e.target.value)}><option value="all">全部完成后继续</option><option value="any">任意一项完成后继续</option></select></label></>:nodeSelect('从哪个步骤出发',edge.from_node,v=>change([...epath,'from_node'],v))}{nodeSelect('接下来到哪个步骤',edge.to_node,v=>change([...epath,'to_node'],v))}{edge.kind==='condition'&&<Field label="满足什么条件才继续" area value={edge.condition} disabled={!editable} onChange={v=>change([...epath,'condition'],v)}/>} {edge.kind==='parallel'&&<label className="bfr-field"><span>并行分组</span><select disabled={!editable} value={text(edge.group)} onChange={e=>change([...epath,'group'],e.target.value==='__new'?newId('group'):e.target.value)}>{Array.from(new Set(edges.filter(e=>e?.kind==='parallel').map(e=>e.group))).map((g,i)=><option key={g} value={g}>并行分组 {i+1}</option>)}<option value="__new">新建并行分组</option></select></label>}{sourceControls(edge,epath)}<button disabled={!editable} onClick={()=>mutate(v=>{v.business.flows[flowIndex].edges.splice(edgeIndex,1);setEdge(0);},true)}>删除此连线（可撤销）</button></div>}
 <h3>最终产出</h3>{list(flow.deliverables).map((d,i)=><div className="bfr-card" key={i} data-review-path={`/business/flows/${flowIndex}/deliverables/${i}`}><Field label={`产出名称 ${i+1}`} value={d?.name} disabled={!editable} onChange={v=>change([...fpath,'deliverables',i,'name'],v)}/><Field label={`产出说明 ${i+1}`} area value={d?.description} disabled={!editable} onChange={v=>change([...fpath,'deliverables',i,'description'],v)}/>{nodeSelect('由哪些步骤产生（可多选）',d?.produced_by,v=>change([...fpath,'deliverables',i,'produced_by'],v),true)}{sourceControls(d,[...fpath,'deliverables',i])}<button disabled={!editable} onClick={()=>mutate(v=>v.business.flows[flowIndex].deliverables.splice(i,1),true)}>删除此产出（可撤销）</button></div>)}<button disabled={!editable} onClick={()=>mutate(v=>{const f=v.business.flows[flowIndex];if(!Array.isArray(f.deliverables))f.deliverables=[];f.deliverables.push({name:'',description:'',produced_by:[],attribution:attr()});})}>＋ 添加最终产出</button>
 </>:<p>此流程结构暂不可编辑。请添加一个空白工作流程补充，原模型内容仍保留对照。</p>}
 <h3>仍需核对的问题</h3>{list(business.questions).map((q,i)=><div className="bfr-card" key={i} data-review-path={`/business/questions/${i}`}><Field label={`待核问题 ${i+1}`} value={q?.question} area disabled={!editable} path={`/business/questions/${i}/question`} onChange={v=>change(['business','questions',i,'question'],v)}/><Field label={`问题原因 ${i+1}`} value={q?.reason} area disabled={!editable} path={`/business/questions/${i}/reason`} onChange={v=>change(['business','questions',i,'reason'],v)}/>{citationControls(q?.citations,['business','questions',i,'citations'])}<button disabled={!editable} onClick={()=>action(()=>removeArrayItem(['business','questions'],i))}>移除此待核问题 {i+1}（可撤销）</button></div>)}<button disabled={!editable} onClick={()=>mutate(v=>{if(!Array.isArray(v.business.questions))v.business.questions=[];v.business.questions.push({question:'',reason:'',citations:[]});})}>＋ 保留待核问题</button></>}
 <Field label="本次修订说明" area disabled={!editable} value={c.form.note} onChange={v=>action(()=>c.edit(c.form.value,v))}/>{nested?<NestedFlowAuthorReview controller={c} onConfigureExecution={configureExecution}/>:<BusinessFlowAuthorReview controller={c} onConfigureExecution={configureExecution}/>}{retainedExecutionError&&<p role="alert">{retainedExecutionError} 原执行记录保留，请先导出本页备份。</p>}{(nested?retainedNestedExecutions:retainedExecutions).length>0&&<section className="bfr-card" aria-label="保留的执行草稿与原请求"><h3>保留的执行草稿与原请求</h3><p>后来的核对不会覆盖旧版本的执行草稿。请选择对应版本继续处理。</p><ul>{(nested?retainedNestedExecutions:retainedExecutions).map(entry=><li key={entry.qualification.id}>核对版本 {entry.qualification.draft_version} · {entry.pending?(entry.pending.receipt_saved?"原采用结果已保存，页面待同步":"原请求结果待核对"):"执行草稿保留"}<button disabled={c.busy||c.identityStatus!=='verified'||!c.localSaved} onClick={()=>action(()=>configureExecution(entry.qualification))}>打开此版本的执行配置</button></li>)}</ul></section>}{!nested&&qualification&&<BusinessFlowExecutionEditor key={qualification.id} controller={c} qualification={qualification} typedHTTP={typedHTTP} prepareConfiguration={prepareConfiguration} onConfigurationAdopted={onConfigurationAdopted}/>} {nested&&nestedQualification&&<NestedBusinessFlowExecutionEditor key={nestedQualification.id} controller={c} qualification={nestedQualification} typedHTTP={typedHTTP} prepareConfiguration={prepareConfiguration} onConfigurationAdopted={onConfigurationAdopted}/>}</>}</>}</main>
 <aside className="bfr-right"><h3>待核对事项</h3><p>{freshDiagnostics?'以下是服务器对最近保存稿的检查。':'以下是最近一次服务器检查；当前修改尚未重新检查。'}结构与引用检查不证明业务语义正确。</p>{issues.map((issue,i)=><button key={i} className="bfr-issue" onClick={()=>jump(issue)}>{issue.message||'请补充此处内容'} <span>定位修改 →</span></button>)}{detail&&!issues.length&&<p>本次未返回定位问题，仍不是确认或采用依据。</p>}
 {focusedSource&&sourceComparison(focusedSource)}
 <h3>人工稿依据清单</h3><p>使用次数按当前人工稿计算，包含流程职责和待核问题。仅明确清理所选条目，原模型依据保持不变。</p>
 <details><summary>查看全部依据 · {currentEvidence.length} 条</summary>{currentEvidence.map((entry,index)=>{
  const ref=entry?.span_ref,source=evidence.find(s=>typeof ref==='string'&&s.span_ref===ref),uses=citationUses.filter(use=>use.span_ref===ref),copies=currentEvidence.filter(q=>typeof ref==='string'&&q?.span_ref===ref).length,blocked=uses.length>0&&copies<2;
  return <article className="bfr-evidence" key={index} data-review-path={`/evidence/${index}`}><strong>人工稿依据 {index+1} · 使用 {uses.length} 次</strong>{!source&&<p>不在当前业务原文范围或条目尚不完整。</p>}{copies>1&&<p>同一原文有 {copies} 条重复依据；可明确删除多余条目，保留另一条。</p>}{!uses.length&&<p>当前没有使用此依据，可明确移除。</p>}<details><summary>当前引用文字</summary><p className="bfr-source-text">{text(entry?.quote)||'引用文字未填写或格式待核对'}</p></details>{uses.map((use,i)=><button key={i} onClick={()=>jump({path:use.path})}>定位使用处：{use.label}</button>)}{typeof ref==='string'&&<button onClick={()=>jump({path:'/evidence/'+index,span_ref:ref})}>对照此依据原文</button>}<button disabled={!editable||blocked} onClick={()=>removeEvidence(index)}>{copies>1?'删除此重复依据（保留另一条，可撤销）':'移除此依据（可撤销）'}</button>{blocked&&<p>此依据仍在使用；请先定位并明确移除相关引用，再删除依据条目。</p>}</article>;
 })}{!currentEvidence.length&&<p>当前人工稿没有依据条目。</p>}</details>
 <h3>原文依据</h3>{evidence.map((source,i)=>{const entries=currentEvidence.map((entry,index)=>({entry,index})).filter(({entry})=>entry?.span_ref===source.span_ref);return <article className="bfr-evidence" key={source.span_ref}><strong>原文片段 {i+1}</strong><p className="bfr-source-text">{source.source_text}</p>{entries.map(({entry,index})=><div key={index}><details><summary>查看人工稿引用 {index+1} 的文字</summary><p className="bfr-source-text">{text(entry.quote)||'当前引用未填写'}</p></details><button disabled={!editable} onClick={()=>change(['evidence',index,'quote'],source.source_text,true)}>将此原文填入引用 {index+1}</button></div>)}</article>;})}{!evidence.length&&<p>尚未读取到可核对的原文，不会显示替代内容。</p>}</aside></div></>}
 </section>;
}

const styles=`.bfr{color:#17304f;background:#f6f8fc;padding:20px;border:1px solid #dae2ef;border-radius:12px}.bfr h2,.bfr h3,.bfr h4{margin:0 0 12px}.bfr p{line-height:1.6}.bfr-head{display:flex;gap:20px;justify-content:space-between;align-items:center}.bfr button{border:1px solid #c9d5e7;background:white;color:#164c91;border-radius:6px;padding:8px 12px;cursor:pointer}.bfr button:disabled{opacity:.55;cursor:not-allowed}.bfr button:focus-visible,.bfr input:focus-visible,.bfr textarea:focus-visible,.bfr select:focus-visible{outline:3px solid #98c1ff;outline-offset:2px}.bfr-head>button{background:#1764d6;color:white;white-space:nowrap}.bfr-layout{display:grid;grid-template-columns:220px minmax(360px,1fr) 340px;gap:16px;margin-top:16px}.bfr-left,.bfr-editor,.bfr-right{background:white;padding:16px;border:1px solid #dbe4f1;border-radius:8px;min-width:0}.bfr-left button{display:block;text-align:left;width:100%;margin-bottom:8px}.bfr-left h3{margin-top:20px}.bfr-left small{display:block;color:#62758c;margin-top:6px}.bfr .selected,.bfr button[aria-pressed=true]{background:#e8f1ff;border-color:#2d75dc}.bfr-tabs,.bfr-row,.bfr-list{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;align-items:center}.bfr-field{display:flex;flex-direction:column;gap:6px;margin:12px 0;flex:1;min-width:0}.bfr-field>span{font-weight:600}.bfr input:not([type=checkbox]),.bfr textarea,.bfr select{box-sizing:border-box;padding:9px;border:1px solid #bdcde1;border-radius:5px;width:100%;font:inherit;min-width:0}.bfr textarea{resize:vertical}.bfr select[multiple]{min-height:110px}.bfr-card,.bfr-sources{border:1px solid #dce6f3;border-radius:7px;padding:12px;margin:12px 0}.bfr-check{display:block;margin:8px 0}.bfr-sources legend{font-weight:600}.bfr-state{color:#566f8d}.bfr-error{padding:12px;background:#fff0ed;color:#9f2b1f;border:1px solid #edc4be;margin:12px 0}.bfr-notice,.bfr-pending{padding:12px;background:#fff7e6;border:1px solid #edd6a0;margin:12px 0}.bfr-pending button,.bfr-error button{margin:4px}.bfr-right{max-height:calc(100vh - 130px);overflow:auto}.bfr-issue{text-align:left;width:100%;margin:6px 0}.bfr-issue span{display:block;margin-top:4px;font-size:12px}.bfr-evidence{border-top:1px solid #dbe4f0;margin-top:12px;padding-top:12px}.bfr-source-text,.bfr-readonly{white-space:pre-wrap;overflow-wrap:anywhere}.bfr-read-node{border-left:3px solid #bfd4ee;padding-left:12px;margin:14px 0}.bfr-read-node p{margin:5px 0}@media(max-width:1200px){.bfr-layout{grid-template-columns:190px minmax(300px,1fr)}.bfr-right{grid-column:1/-1;max-height:600px}}@media(max-width:760px){.bfr-head{display:block}.bfr-layout{display:block}.bfr-left,.bfr-editor,.bfr-right{margin-top:12px}.bfr-tabs button{flex:1}}`;
