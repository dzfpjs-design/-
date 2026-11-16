import React,{useState} from 'react';
import NestedFlowCausalEvidence from './NestedFlowCausalEvidence.jsx';
import {isNestedFlowPlan,nestedPointer,nestedSet,nestedArrayEdit,nestedPhase,nestedAction,nestedCapacity,NESTED_LIMITS,nestedAttribution} from './nested-flow-review.mjs';

const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value),list=value=>Array.isArray(value)?value:[],text=value=>typeof value==='string'?value:'';
const actors={business_agent:'业务驱动智能体',perception_agent:'多模感知智能体',data_agent:'数据管理智能体',human:'人工岗位',task_center:'任务中心',execution_service:'执行服务'};
const phaseNames={steps:'依次完成步骤',decision:'条件分支',parallel:'并行协作',repeat:'循环核查'};
function Field({label,value,area=false,readOnly,disabled,onChange,path}){
 if(readOnly)return <div data-review-path={path}><strong>{label}</strong><p className="bfr-source-text">{text(value)||'未填写'}</p></div>;
 const Tag=area?'textarea':'input';return <label className="bfr-field" data-review-path={path}><span>{label}</span><Tag aria-label={label} value={text(value)} disabled={disabled} rows={area?3:undefined} onChange={event=>onChange(event.target.value)}/></label>;
}
function CitationChooser({sources,current,label,disabled,onChange}){
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[visible,setVisible]=useState(20);
 const needle=query.trim().toLocaleLowerCase(),matches=open?sources.map((source,index)=>({source,index})).filter(({source})=>!needle||source.source_text.toLocaleLowerCase().includes(needle)):[];
 const unknown=open?current.map((ref,index)=>({ref,index})).filter(({ref})=>!sources.some(source=>source.span_ref===ref)):[];
 return <details onToggle={event=>setOpen(event.currentTarget.open)}><summary>选择或调整关联原文</summary>{open&&<>
  <label className="bfr-field"><span>搜索全部可用原文</span><input aria-label={label+'搜索全部原文'} value={query} onChange={event=>{setQuery(event.target.value);setVisible(20);}}/></label><p>共 {sources.length} 段可选原文，匹配 {matches.length} 段；当前显示 {Math.min(visible,matches.length)} 段。搜索和展开不会移除已有引用。</p>
  {matches.slice(0,visible).map(({source,index})=><label className="bfr-check" key={source.span_ref}><input type="checkbox" aria-label={label+'关联原文 '+(index+1)} disabled={disabled} checked={current.includes(source.span_ref)} onChange={event=>onChange(event.target.checked?[...current,source.span_ref]:current.filter(ref=>ref!==source.span_ref))}/>原文片段 {index+1}<p className="bfr-source-text">{source.source_text}</p></label>)}
  {visible<matches.length&&<button type="button" onClick={()=>setVisible(count=>count+20)}>继续显示后20段原文</button>}{!matches.length&&<p>没有匹配的原文；可清空搜索查看全部目录，现有引用保持不变。</p>}
  {unknown.map(({index})=><button type="button" key={index} disabled={disabled} onClick={()=>onChange(current.filter((_,i)=>i!==index))}>移除暂不可读取的关联 {index+1}（可撤销）</button>)}
 </>}</details>;
}

export default function NestedFlowReview({value,flowIndex=0,readOnly=false,disabled=false,sources=[],generationCausality,generationRecovery,currentChanged=false,onChange,onCitation,onFlowIndexChange,showQuestions=true}){
 const [error,setError]=useState(''),[removal,setRemoval]=useState(null);
 const editable=!readOnly&&!disabled&&typeof onChange==='function',flow=list(value?.business?.flows)[flowIndex],fp=['business','flows',flowIndex];
 const evidence=list(sources).filter(row=>typeof row?.span_ref==='string'&&typeof row.source_text==='string');
 if(!isNestedFlowPlan(value))return <p role="alert">这份原稿暂不能按递归流程显示；完整原值与已保存稿件仍保留，未转成其他流程。</p>;
 const run=operation=>{try{operation();setError('');}catch(cause){setError(cause.message||'这次修改尚不能安全保存，原稿保留。');}};
 const commit=(next,backup=false)=>{if(!editable)throw Error('当前为只读，未修改原稿。');onChange(next,{backup});};
 const set=(path,next,backup=false)=>run(()=>commit(nestedSet(value,path,next),backup));
 const selectAfter=(path,operation,next)=>{if(path.join('.')!=='business.flows')return;const index=operation.kind==='up'?flowIndex-1:operation.kind==='down'?flowIndex+1:Math.min(flowIndex,list(next.business.flows).length-1);onFlowIndexChange?.(Math.max(0,index));};
 const array=(path,operation)=>run(()=>{const next=nestedArrayEdit(value,path,operation);commit(next,true);selectAfter(path,operation,next);});
 const remove=(path,index,label)=>setRemoval({path,index,label,original:JSON.stringify(value)});
 const field=(label,path,current,area=false)=><Field key={nestedPointer(path)} label={label} path={nestedPointer(path)} value={current} area={area} readOnly={readOnly} disabled={!editable} onChange={next=>set(path,next)}/>;
 function order(path,index,length,label){return !readOnly&&<div className="bfr-row"><button type="button" aria-label={label+'上移'} disabled={!editable||index===0} onClick={()=>array(path,{kind:'up',index})}>上移</button><button type="button" aria-label={label+'下移'} disabled={!editable||index===length-1} onClick={()=>array(path,{kind:'down',index})}>下移</button><button type="button" disabled={!editable} onClick={()=>remove(path,index,label)}>删除{label}</button></div>;}
 function citations(refs,path,label){
  const current=list(refs);
  return <fieldset className="bfr-sources" data-review-path={nestedPointer(path)}><legend>{label} · 原文依据</legend>
   {current.map((ref,index)=>{const source=evidence.find(row=>row.span_ref===ref);return <article key={index}>{source?<><p className="bfr-source-text">{source.source_text}</p><button type="button" disabled={typeof onCitation!=='function'} onClick={()=>onCitation?.(ref)}>查看关联原文 {index+1}</button></>:<p>关联依据 {index+1} 的原文暂不可读取，原引用仍保留。</p>}</article>;})}
   {!current.length&&<p>尚未关联原文；不会自动补成已有依据。</p>}
   {!readOnly&&<CitationChooser sources={evidence} current={current} label={label} disabled={!editable} onChange={next=>set(path,next,true)}/>}
  </fieldset>;
 }
 function attribution(current,path,label){
  return <details className="bfr-card" data-review-path={nestedPointer(path)}><summary>{label}的依据与说明</summary>{citations(current?.citations,[...path,'citations'],label)}
   {readOnly?<p>{current?.inferred?'原候选标为推断，仍待核对。':'原候选标为资料记述，仍待核对。'}</p>:<label className="bfr-check"><input type="checkbox" aria-label={label+'属于推断'} checked={current?.inferred===true} disabled={!editable} onChange={event=>set([...path,'inferred'],event.target.checked)}/>这项内容需要推断</label>}
   {field(label+'依据或推断说明',[...path,'reason'],current?.reason,true)}
  </details>;
 }
 function ports(values,path,label){
  if(!Array.isArray(values))return <p role="alert">{label}暂不能按条目编辑，原值保留。</p>;
  const recoveryIssues=generationRecovery?.schema==='business-flow-nested-component-view-v194'&&generationRecovery.component_path==='/plan'&&generationRecovery.original_protocol_valid===false?list(generationRecovery.issues):[];
  return <fieldset className="bfr-card" data-review-path={nestedPointer(path)}><legend>{label}</legend><p>每项资料独立保存，项内换行仍属于同一项；重复名称按各自位置保留。</p>{values.map((current,index)=>{const pointer=nestedPointer([...path,index]),failed=recoveryIssues.some(issue=>issue?.path===pointer||issue?.path?.startsWith(pointer+'/'));return <div key={index}>{failed&&<p role="alert" data-original-recovery-issue>原候选在此位置的资料来源核对未通过，原错误记录保留。稿件修改后须重新核对，不能按名称或位置继承来源结论。</p>}{field(label+' '+(index+1),[...path,index],current,true)}{order(path,index,values.length,label+' '+(index+1))}</div>;})}{!values.length&&<p>尚未填写此项资料。</p>}{!readOnly&&<button type="button" disabled={!editable} onClick={()=>array(path,{kind:'append',item:''})}>＋ 为{label}添加一项</button>}</fieldset>;
 }
 function action(current,path,label){
  if(!object(current))return <p role="alert" key={nestedPointer(path)}>{label}暂不能完整显示，原值保留。</p>;
  return <section className="bfr-card" data-review-path={nestedPointer(path)} key={nestedPointer(path)}><h5>{label}{current.name?' · '+current.name:''}</h5>
   {field(label+'步骤名称',[...path,'name'],current.name)}
   {readOnly?<p>由谁承担职责：{actors[current.actor]||'职责待核对'}</p>:<label className="bfr-field"><span>{label}由谁承担职责</span><select aria-label={label+'由谁承担职责'} disabled={!editable} value={text(current.actor)} onChange={event=>set([...path,'actor'],event.target.value)}>{!Object.hasOwn(actors,current.actor||'')&&<option value={text(current.actor)}>原职责暂待核对</option>}{Object.entries(actors).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>}
   {field(label+'负责岗位',[...path,'role'],current.role)}{field(label+'具体职责',[...path,'responsibility'],current.responsibility,true)}
   {ports(current.inputs,[...path,'inputs'],label+'需要的资料')}
   <NestedFlowCausalEvidence view={generationCausality} flowIndex={flowIndex} actionPath={nestedPointer(path)} currentChanged={currentChanged} onCitation={onCitation}/>
   {ports(current.outputs,[...path,'outputs'],label+'形成的结果')}
   {attribution(current.attribution,[...path,'attribution'],label)}
  </section>;
 }
 function actionList(values,path,label){
  return <div>{list(values).map((current,index)=><div key={index}>{action(current,[...path,index],label+'步骤 '+(index+1))}{order(path,index,list(values).length,label+'步骤 '+(index+1))}</div>)}{!list(values).length&&<p>此处尚无步骤。原空列表保留，仍需核对。</p>}{!readOnly&&<button type="button" disabled={!editable} onClick={()=>array(path,{kind:'append',item:nestedAction()})}>＋ 在{label}添加步骤</button>}</div>;
 }
 function phases(values,path,parentDepth,label){
  return <div>{list(values).map((phase,index)=>{
   const at=[...path,index],title=label+'阶段 '+(index+1),depth=parentDepth+(['decision','parallel','repeat'].includes(phase?.kind)?1:0);
   if(!object(phase)||!Object.hasOwn(phaseNames,phase.kind||''))return <p role="alert" key={index}>此处原阶段暂不能完整显示，原值保留，未展平为步骤。</p>;
   return <section className="bfr-card" key={index} data-review-path={nestedPointer(at)} style={{borderLeft:'3px solid #bfd4ee',marginLeft:parentDepth?12:0}}><h4>{title} · {phaseNames[phase.kind]}</h4>{order(path,index,list(values).length,title)}{attribution(phase.attribution,[...at,'attribution'],title)}
    {phase.kind==='steps'&&actionList(phase.steps,[...at,'steps'],title)}
    {phase.kind==='repeat'&&<><p>完成本轮步骤后核查，再按结果决定是否重复；结束条件尚未明确时不能视为可执行。</p>{actionList(phase.steps,[...at,'steps'],title)}{action(phase.check,[...at,'check'],title+'本轮核查')}{field(title+'什么情况下再次执行',[...at,'repeat_if'],phase.repeat_if,true)}{field(title+'什么情况下结束循环',[...at,'finish_if'],phase.finish_if,true)}</>}
    {['decision','parallel'].includes(phase.kind)&&<>{action(phase[phase.kind==='decision'?'decision':'dispatch'],[...at,phase.kind==='decision'?'decision':'dispatch'],title+(phase.kind==='decision'?'条件核对':'组织并行工作'))}
     <p>{phase.kind==='decision'?'根据条件核对结果选择对应分支；各分支保持独立。':'各分支并行推进，不因页面上下顺序变成依次执行。'}</p>
     {phase.kind==='parallel'&&(readOnly?<p>汇合规则：{phase.wait_mode==='any'?'任一分支完成后继续':'全部分支完成后继续'}</p>:<label className="bfr-field"><span>{title}汇合规则</span><select aria-label={title+'汇合规则'} disabled={!editable} value={text(phase.wait_mode)} onChange={event=>set([...at,'wait_mode'],event.target.value)}><option value="all">全部分支完成后继续</option><option value="any">任一分支完成后继续</option></select></label>)}
     {list(phase.branches).map((branch,branchIndex)=>{const bp=[...at,'branches',branchIndex],branchTitle=title+(phase.kind==='decision'?'条件分支 ':'并行分支 ')+(branchIndex+1);return <section className="bfr-card" key={branchIndex} data-review-path={nestedPointer(bp)}><h5>{branchTitle}</h5>{order([...at,'branches'],branchIndex,list(phase.branches).length,branchTitle)}{phase.kind==='decision'&&<>{field(branchTitle+'进入条件',[...bp,'condition'],branch?.condition,true)}{attribution(branch?.attribution,[...bp,'attribution'],branchTitle+'进入条件')}</>}{!list(branch?.phases).length&&<p>空分支：直接结束本分支，仍遵守所属阶段的汇合规则；不会制造业务结果。</p>}{phases(branch?.phases,[...bp,'phases'],depth,branchTitle+' · ')}</section>;})}
     {!readOnly&&<button type="button" disabled={!editable||list(phase.branches).length>=NESTED_LIMITS.branches} onClick={()=>array([...at,'branches'],{kind:'append',item:phase.kind==='decision'?{condition:'',attribution:nestedAttribution(),phases:[]}:{phases:[]}})}>＋ 为{title}添加分支</button>}
     {phase.kind==='parallel'&&attribution(phase.join_attribution,[...at,'join_attribution'],title+'汇合规则')}
    </>}
   </section>;
  })}{!readOnly&&<div className="bfr-row" aria-label={label+'添加阶段'}>{Object.entries(phaseNames).map(([kind,name])=><button type="button" key={kind} disabled={!editable||(kind!=='steps'&&parentDepth>=NESTED_LIMITS.depth)} onClick={()=>array(path,{kind:'append',item:nestedPhase(kind)})}>＋ 在{label}添加{name}</button>)}{parentDepth>=NESTED_LIMITS.depth&&<p>已到三层控制结构；可继续添加本层普通步骤，不再嵌套条件、并行或循环。</p>}</div>}</div>;
 }
 const counts=nestedCapacity(value)[flowIndex];
 return <section className="nested-flow-review" aria-label={readOnly?'递归流程只读对照':'递归流程编辑'}>
  <p role="note">保留条件、并行和循环的原层级；本页修订和保存规划，不签发执行授权。</p>{error&&<p role="alert" className="bfr-error">{error}</p>}
  {removal&&!readOnly&&<section role="alert" className="bfr-pending"><strong>删除{removal.label}及其内部内容？</strong><p>完整删除前的稿件会保留为结构备份，可用页面的“撤销最近一次结构修改”恢复。后续输入不会被自动覆盖。</p><button type="button" disabled={!editable} onClick={()=>run(()=>{if(removal.original!==JSON.stringify(value))throw Error('草稿已变化，请重新核对要删除的条目。');const next=nestedArrayEdit(value,removal.path,{kind:'remove',index:removal.index});commit(next,true);selectAfter(removal.path,{kind:'remove'},next);setRemoval(null);})}>确认删除所选内容</button><button type="button" onClick={()=>setRemoval(null)}>取消删除</button></section>}
  <Field label="业务名称（本次范围固定）" path="/business/name" value={value?.business?.name} readOnly/>
  {!object(flow)?<p>请选择一个工作流程；已有完整稿件仍保留。</p>:<>
   {order(['business','flows'],flowIndex,list(value.business.flows).length,'工作流程 '+(flowIndex+1))}
   {counts&&<p>当前流程：{counts.phases} 个阶段、{counts.actions} 个动作、{counts.depth} 层控制结构。最多12个阶段、32个动作、3层控制结构。</p>}
   {field('工作流程名称',[...fp,'name'],flow.name)}
   {readOnly?<p>何时启动：{{manual:'人工发起',schedule:'按计划启动',event:'发生业务事件',data_arrival:'收到相关数据'}[flow.trigger?.kind]||'待核对'}</p>:<label className="bfr-field"><span>何时启动</span><select aria-label="递归流程何时启动" disabled={!editable} value={text(flow.trigger?.kind)} onChange={event=>set([...fp,'trigger','kind'],event.target.value)}>{!['manual','schedule','event','data_arrival'].includes(flow.trigger?.kind)&&<option value={text(flow.trigger?.kind)}>原条件待核对</option>}<option value="manual">人工发起</option><option value="schedule">按计划启动</option><option value="event">发生业务事件</option><option value="data_arrival">收到相关数据</option></select></label>}
   {field('触发条件说明',[...fp,'trigger','description'],flow.trigger?.description,true)}{attribution(flow.trigger?.attribution,[...fp,'trigger','attribution'],'流程触发条件')}{attribution(flow.phase_order,[...fp,'phase_order'],'阶段顺序')}
   {phases(flow.phases,[...fp,'phases'],0,'')}{action(flow.completion,[...fp,'completion'],'流程结束工作')}
  </>}
  {showQuestions&&<><h3>仍需核对的问题</h3>{list(value?.business?.questions).map((question,index)=><section className="bfr-card" key={index} data-review-path={nestedPointer(['business','questions',index])}>{field('待核问题 '+(index+1),['business','questions',index,'question'],question?.question,true)}{field('问题原因 '+(index+1),['business','questions',index,'reason'],question?.reason,true)}{citations(question?.citations,['business','questions',index,'citations'],'待核问题 '+(index+1))}{order(['business','questions'],index,list(value.business.questions).length,'待核问题 '+(index+1))}</section>)}
  {!readOnly&&<button type="button" disabled={!editable||list(value?.business?.questions).length>=NESTED_LIMITS.questions} onClick={()=>array(['business','questions'],{kind:'append',item:{question:'',reason:'',citations:[]}})}>＋ 保留待核问题</button>}</>}
 </section>;
}
